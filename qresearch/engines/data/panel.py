"""PricePanel construction, caching, and lookuptables."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from qresearch.config.models import ResearchConfig
from qresearch.engines.data import vendor


@dataclass
class PricePanel:
    bars: pl.DataFrame
    calendar: list[date]
    adjustment_as_of: str
    data_fingerprint: str
    start: date
    end: date
    instruments: list[str]
    _by_key: dict[tuple[str, date], dict] = field(default_factory=dict, repr=False)

    def build_index(self) -> None:
        self._by_key.clear()
        for row in self.bars.iter_rows(named=True):
            d = row["trade_date"]
            if not isinstance(d, date):
                d = date.fromisoformat(str(d))
            self._by_key[(row["instrument"], d)] = row

    def get(self, instrument: str, session: date) -> dict | None:
        return self._by_key.get((instrument, session))

    def prior_close(self, instrument: str, session: date) -> float | None:
        # previous calendar trading day close
        try:
            idx = self.calendar.index(session)
        except ValueError:
            return None
        if idx <= 0:
            return None
        prev = self.calendar[idx - 1]
        bar = self.get(instrument, prev)
        return None if bar is None else float(bar["close"])

    def next_session(self, session: date, n: int = 1) -> date | None:
        try:
            idx = self.calendar.index(session)
        except ValueError:
            # find next available
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
        entry_max + timedelta(days=max(max_hold, config.execution.order_validity_sessions) + config.delay_buffer_sessions + config.suspend_buffer_sessions + 5),
    )
    return start, end


def _cache_key(
    instruments: list[str],
    start: date,
    end: date,
    adj_mode: str,
    adj_as_of: str,
) -> str:
    uni = hashlib.sha1(",".join(sorted(instruments)).encode()).hexdigest()[:12]
    return f"{adj_mode}_{adj_as_of}_{start.isoformat()}_{end.isoformat()}_{uni}"


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

    adj_as_of = config.adjustment.as_of or end.strftime("%Y%m%d")
    fp = "unavailable"

    if bars_override is not None:
        bars = bars_override
        calendar = calendar_override or sorted(bars["trade_date"].unique().to_list())
    else:
        cache_dir = Path(cache_dir or "workspace/cache/prices")
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = _cache_key(instruments, start, end, config.adjustment.mode, adj_as_of)
        cache_path = cache_dir / f"{key}.parquet"
        if cache_path.exists():
            bars = pl.read_parquet(cache_path)
            fp = "cache_hit"
        else:
            bars, fp = vendor.load_daily_long(
                instruments, start, end, adj=config.adjustment.mode
            )
            # ensure adjustment as_of end: qfq already uses window end
            bars.write_parquet(cache_path)
        try:
            calendar = vendor.load_trade_calendar(start, end)
        except Exception:
            calendar = sorted(bars["trade_date"].unique().to_list())
        if not calendar:
            calendar = sorted(bars["trade_date"].unique().to_list())

    panel = PricePanel(
        bars=bars,
        calendar=[d if isinstance(d, date) else date.fromisoformat(str(d)) for d in calendar],
        adjustment_as_of=adj_as_of,
        data_fingerprint=fp,
        start=start,
        end=end,
        instruments=instruments,
    )
    panel.build_index()
    return panel
