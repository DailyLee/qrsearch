"""Fixed-horizon, point-in-time market labels."""

from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path

import polars as pl

from qresearch.config.models import LabelConfig, ResearchConfig
from qresearch.engines.data.panel import PricePanel, load_price_panel
from qresearch.research.domain import LabelSet, SampleSet


_LABEL_SCHEMA = {
    "sample_id": pl.Utf8,
    "instrument": pl.Utf8,
    "asof_session": pl.Date,
    "effective_session": pl.Date,
    "label_start": pl.Date,
    "label_end": pl.Date,
    "forward_return": pl.Float64,
    "label_status": pl.Utf8,
}
_COMPAT_CALENDAR_MARGIN_DAYS = 14


def load_research_price_panel(
    samples: SampleSet,
    label: LabelConfig,
    research: ResearchConfig,
    cache_dir: Path,
) -> PricePanel:
    """Load a panel through the legacy event-shaped loader without persisting events."""
    events = samples.frame.select("instrument", "effective_session").with_columns(
        pl.col("effective_session").alias("entry_intent_date"),
        (
            pl.col("effective_session")
            + pl.duration(
                days=(label.entry_lag_sessions + label.horizon_sessions) * 2
                + _COMPAT_CALENDAR_MARGIN_DAYS
            )
        ).alias("exit_intent_date"),
    ).select("instrument", "entry_intent_date", "exit_intent_date")
    return load_price_panel(events, research, cache_dir=cache_dir)


def materialize_labels(samples: SampleSet, panel: PricePanel, config: LabelConfig) -> LabelSet:
    """Compute fixed-horizon returns while retaining every sample observation."""
    rows: list[dict[str, object]] = []
    for sample in samples.frame.iter_rows(named=True):
        asof = sample["asof_session"]
        if not isinstance(asof, date):
            asof = date.fromisoformat(str(asof))
        entry_session = panel.next_session(asof, config.entry_lag_sessions)
        exit_session = (
            panel.next_session(entry_session, config.horizon_sessions)
            if entry_session is not None
            else None
        )
        status = "ok"
        forward_return: float | None = None
        if entry_session is None or exit_session is None:
            status = "no_calendar_session"
        else:
            entry_bar = panel.get(sample["instrument"], entry_session, asof=exit_session)
            entry_price = None if entry_bar is None else entry_bar.get(config.entry_price)
            if entry_price is None:
                status = "missing_entry"
            else:
                exit_bar = panel.get(sample["instrument"], exit_session, asof=exit_session)
                exit_price = None if exit_bar is None else exit_bar.get(config.exit_price)
                if exit_price is None:
                    status = "missing_exit"
                else:
                    forward_return = float(exit_price) / float(entry_price) - 1.0
        rows.append(
            {
                "sample_id": sample["sample_id"],
                "instrument": sample["instrument"],
                "asof_session": asof,
                "effective_session": sample["effective_session"],
                "label_start": entry_session,
                "label_end": exit_session,
                "forward_return": forward_return,
                "label_status": status,
            }
        )

    frame = pl.DataFrame(rows, schema=_LABEL_SCHEMA)
    spec: dict[str, object] = {
        "entry_price": config.entry_price,
        "entry_lag_sessions": config.entry_lag_sessions,
        "horizon_sessions": config.horizon_sessions,
        "exit_price": config.exit_price,
        "price_panel_fingerprint": panel.data_fingerprint,
    }
    payload = {"frame": frame.to_dicts(), "spec": spec}
    spec["label_set_hash"] = sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return LabelSet(frame=frame, spec=spec)
