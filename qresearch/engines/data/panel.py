"""PricePanel construction, caching, and PIT-safe lookups."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import polars as pl

from qresearch.config.models import ResearchConfig
from qresearch.engines.data import vendor
from qresearch.engines.data.vendor import is_index_ts_code

AdjMode = Literal["qfq", "hfq", "none"]


@dataclass
class PricePanel:
    bars: pl.DataFrame
    calendar: list[date]
    adjustment_as_of: str
    data_fingerprint: str
    start: date
    end: date
    instruments: list[str]
    adj_mode: AdjMode = "qfq"
    _by_key: dict[tuple[str, date], dict] = field(default_factory=dict, repr=False)
    _adj_by_key: dict[tuple[str, date], float] = field(default_factory=dict, repr=False)

    def build_index(self) -> None:
        self._by_key.clear()
        self._adj_by_key.clear()
        for row in self.bars.iter_rows(named=True):
            d = row["trade_date"]
            if not isinstance(d, date):
                d = date.fromisoformat(str(d))
            self._by_key[(row["instrument"], d)] = row
            af = row.get("adj_factor")
            if af is not None:
                try:
                    self._adj_by_key[(row["instrument"], d)] = float(af)
                except (TypeError, ValueError):
                    pass

    def adj_factor_on(self, instrument: str, session: date) -> float | None:
        """Last known adj_factor on or before session (PIT; no future peek)."""
        v = self._adj_by_key.get((instrument, session))
        if v is not None:
            return v
        # walk calendar backward
        try:
            idx = self.calendar.index(session)
        except ValueError:
            idx = None
            for i, d in enumerate(self.calendar):
                if d >= session:
                    idx = i
                    break
            if idx is None:
                return None
        for j in range(idx, -1, -1):
            v = self._adj_by_key.get((instrument, self.calendar[j]))
            if v is not None:
                return v
        return None

    def _scale(self, instrument: str, bar_session: date, asof: date) -> float:
        mode = self.adj_mode
        if mode == "none":
            return 1.0
        af_bar = self.adj_factor_on(instrument, bar_session)
        if af_bar is None or af_bar <= 0:
            return 1.0  # tests / missing factor → raw
        if mode == "hfq":
            return af_bar
        # qfq PIT: base = factor known on asof (not study end)
        af_asof = self.adj_factor_on(instrument, asof)
        if af_asof is None or af_asof <= 0:
            return 1.0
        return af_bar / af_asof

    def get(
        self,
        instrument: str,
        session: date,
        *,
        asof: date | None = None,
    ) -> dict[str, Any] | None:
        """Return OHLC bar; for qfq, prices are forward-adjusted as-of `asof` (default=session)."""
        raw = self._by_key.get((instrument, session))
        if raw is None:
            return None
        asof_d = asof or session
        scale = self._scale(instrument, session, asof_d)
        if abs(scale - 1.0) < 1e-15:
            return raw
        out = dict(raw)
        for c in ("open", "high", "low", "close"):
            if out.get(c) is not None:
                out[c] = round(float(out[c]) * scale, 4)
        out["adj_scale"] = scale
        out["adj_asof"] = asof_d
        return out

    def prior_close(self, instrument: str, session: date) -> float | None:
        """Previous session close, qfq-adjusted as-of `session` (PIT)."""
        try:
            idx = self.calendar.index(session)
        except ValueError:
            return None
        if idx <= 0:
            return None
        prev = self.calendar[idx - 1]
        bar = self.get(instrument, prev, asof=session)
        return None if bar is None else float(bar["close"])

    def next_session(self, session: date, n: int = 1) -> date | None:
        try:
            idx = self.calendar.index(session)
        except ValueError:
            for d in self.calendar:
                if d >= session:
                    idx = self.calendar.index(d)
                    break
            else:
                return None
        j = idx + n
        if j < 0 or j >= len(self.calendar):
            return None
        return self.calendar[j]


def derive_panel_range(events: pl.DataFrame, config: ResearchConfig) -> tuple[date, date]:
    entry_min = events["entry_intent_date"].min()
    entry_max = events["entry_intent_date"].max()
    exit_max = events["exit_intent_date"].max()
    if not isinstance(entry_min, date):
        entry_min = date.fromisoformat(str(entry_min))
        entry_max = date.fromisoformat(str(entry_max))
        exit_max = date.fromisoformat(str(exit_max))
    max_hold = config.risk.max_hold_sessions or 0
    start = entry_min - timedelta(days=config.lookback_sessions * 2 + 10)
    end = max(
        exit_max,
        entry_max
        + timedelta(
            days=max(max_hold, config.execution.order_validity_sessions)
            + config.delay_buffer_sessions
            + config.suspend_buffer_sessions
            + 5
        ),
    )
    return start, end


def _cache_key(
    instruments: list[str],
    start: date,
    end: date,
    adj_mode: str,
) -> str:
    """Cache raw+adj_factor panels; bump prefix when on-disk schema changes."""
    uni = hashlib.sha1(",".join(sorted(instruments)).encode()).hexdigest()[:12]
    return f"pit_raw_v1_{adj_mode}_{start.isoformat()}_{end.isoformat()}_{uni}"


def load_price_panel(
    events: pl.DataFrame,
    config: ResearchConfig,
    *,
    cache_dir: Path | None = None,
    extra_instruments: list[str] | None = None,
    bars_override: pl.DataFrame | None = None,
    calendar_override: list[date] | None = None,
) -> PricePanel:
    start, end = derive_panel_range(events, config)
    instruments = sorted(set(events["instrument"].to_list()) | set(extra_instruments or []))
    if config.benchmark.instrument:
        instruments = sorted(set(instruments) | {config.benchmark.instrument})
    stock_codes = [c for c in instruments if not is_index_ts_code(c)]
    index_codes = [c for c in instruments if is_index_ts_code(c)]

    adj_mode: AdjMode = config.adjustment.mode  # type: ignore[assignment]
    # Label only (PIT qfq does not peek at panel end)
    adj_as_of = config.adjustment.as_of or "session_pit"
    fp = "unavailable"

    if bars_override is not None:
        bars = bars_override
        if "adj_factor" not in bars.columns:
            bars = bars.with_columns(pl.lit(1.0).alias("adj_factor"))
        calendar = calendar_override or sorted(bars["trade_date"].unique().to_list())
    else:
        cache_dir = Path(cache_dir or "workspace/cache/prices")
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = _cache_key(stock_codes, start, end, adj_mode)
        cache_path = cache_dir / f"{key}.parquet"
        if cache_path.exists():
            bars = pl.read_parquet(cache_path)
            fp = "cache_hit"
        else:
            bars, fp = vendor.load_daily_long(
                stock_codes, start, end, adj=adj_mode
            )
            bars.write_parquet(cache_path)
        if index_codes:
            idx_bars = vendor.load_indices_long(index_codes, start, end)
            if idx_bars.height:
                if "instrument" in bars.columns and bars.height:
                    bars = bars.filter(~pl.col("instrument").is_in(index_codes))
                bars = pl.concat([bars, idx_bars], how="diagonal_relaxed")
        try:
            calendar = vendor.load_trade_calendar(start, end)
        except Exception:
            calendar = sorted(bars["trade_date"].unique().to_list())
        if not calendar:
            calendar = sorted(bars["trade_date"].unique().to_list())

    if "adj_factor" not in bars.columns:
        bars = bars.with_columns(pl.lit(1.0).alias("adj_factor"))

    panel = PricePanel(
        bars=bars,
        calendar=[d if isinstance(d, date) else date.fromisoformat(str(d)) for d in calendar],
        adjustment_as_of=str(adj_as_of),
        data_fingerprint=fp,
        start=start,
        end=end,
        instruments=instruments,
        adj_mode=adj_mode,
    )
    panel.build_index()
    return panel
