"""Point-in-time market membership samples from zer0share."""

from __future__ import annotations

from datetime import date, datetime
import os
from pathlib import Path
from typing import Any

import polars as pl

from qresearch.config import get_settings
from qresearch.config.models import SampleConfig
from qresearch.engines.data.fingerprint import fingerprint_paths
from qresearch.research.domain import SampleSet


class ResearchDataError(RuntimeError):
    """Raised when a market data source cannot form a valid research sample."""


_MEMBERSHIP_COLUMNS = ("trade_date", "universe", "ts_code")
_SAMPLE_SCHEMA = {
    "sample_id": pl.Utf8,
    "instrument": pl.Utf8,
    "asof_session": pl.Date,
    "effective_session": pl.Date,
    "sample_weight": pl.Float64,
}


def _yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    rendered = str(value)
    try:
        return datetime.strptime(rendered, "%Y%m%d").date()
    except ValueError:
        try:
            return date.fromisoformat(rendered[:10])
        except ValueError as exc:
            raise ResearchDataError(f"invalid universe trade_date: {value!r}") from exc


def _universe_fingerprint(universe: str, calendar: list[date], config: SampleConfig) -> str:
    """Fingerprint just the zer0share universe partitions relevant to this sample."""
    settings = get_settings()
    data_root = Path(os.environ.get("ZER0SHARE_DATA", settings.data_dir()))
    universe_root = data_root / "stock" / "universe" / f"name={universe}"
    paths = [
        universe_root / f"date={_yyyymmdd(session)}" / "data.parquet"
        for session in calendar
        if config.start_date <= session <= config.end_date
        and (universe_root / f"date={_yyyymmdd(session)}" / "data.parquet").is_file()
    ]
    return fingerprint_paths(paths)


class MarketSampleProvider:
    """Materialize next-session-effective observations from daily universe snapshots."""

    def __init__(self, pro: object, calendar: list[date]) -> None:
        self._pro = pro
        self._calendar = sorted(set(calendar))
        self._next_session = {
            session: self._calendar[index + 1] if index + 1 < len(self._calendar) else None
            for index, session in enumerate(self._calendar)
        }

    def materialize(self, config: SampleConfig) -> SampleSet:
        membership = self._pro.universe(
            universe=config.universe,
            start_date=_yyyymmdd(config.start_date),
            end_date=_yyyymmdd(config.end_date),
            fields="trade_date,universe,ts_code",
        )
        rows = self._membership_rows(membership)
        observations: list[dict[str, Any]] = []
        dropped_no_effective_session = 0

        for row in rows:
            if str(row["universe"]) != config.universe:
                raise ResearchDataError(
                    "zer0share universe response contains membership for "
                    f"{row['universe']!r}, expected {config.universe!r}"
                )
            asof_session = _as_date(row["trade_date"])
            instrument = str(row["ts_code"])
            if asof_session not in self._next_session:
                raise ResearchDataError(f"universe trade_date absent from trading calendar: {asof_session}")
            effective_session = self._next_session[asof_session]
            if effective_session is None:
                dropped_no_effective_session += 1
                continue
            observations.append(
                {
                    "sample_id": f"market:{config.universe}:{_yyyymmdd(asof_session)}:{instrument}",
                    "instrument": instrument,
                    "asof_session": asof_session,
                    "effective_session": effective_session,
                    "sample_weight": 1.0,
                }
            )

        frame = pl.DataFrame(observations, schema=_SAMPLE_SCHEMA).sort("asof_session", "instrument")
        manifest = {
            "sample_kind": "market",
            "universe": config.universe,
            "start_date": config.start_date.isoformat(),
            "end_date": config.end_date.isoformat(),
            "rows": frame.height,
            "instruments": frame["instrument"].n_unique(),
            "dropped_no_effective_session": dropped_no_effective_session,
            "zer0share_data_fingerprint": _universe_fingerprint(
                config.universe, self._calendar, config
            ),
        }
        return SampleSet(frame=frame, manifest=manifest)

    @staticmethod
    def _membership_rows(membership: object) -> list[dict[str, object]]:
        if membership is None:
            return []
        if not hasattr(membership, "columns") or not hasattr(membership, "to_dict"):
            raise ResearchDataError("zer0share universe response must be a tabular dataframe")
        columns = set(membership.columns)
        missing = [column for column in _MEMBERSHIP_COLUMNS if column not in columns]
        if missing:
            raise ResearchDataError(f"zer0share universe response missing columns: {', '.join(missing)}")
        records = membership.loc[:, list(_MEMBERSHIP_COLUMNS)].to_dict("records")
        seen: set[tuple[date, str]] = set()
        for record in records:
            key = (_as_date(record["trade_date"]), str(record["ts_code"]))
            if key in seen:
                raise ResearchDataError(
                    f"duplicate universe membership for {key[0].isoformat()} {key[1]}"
                )
            seen.add(key)
        return records
