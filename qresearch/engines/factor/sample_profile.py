"""Event-sample profiling for research artifacts."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

import polars as pl


def _as_date(v: object) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def build_sample_profile(events: pl.DataFrame, feature_cols: list[str] | None = None) -> dict[str, Any]:
    if events.height == 0:
        return {
            "n_events": 0,
            "n_instruments": 0,
            "years": {},
            "years_span": {},
            "feature_non_null": {},
        }

    entry_dates = [_as_date(d) for d in events["entry_intent_date"].to_list()]
    years = Counter(d.year for d in entry_dates)
    span: dict[str, dict[str, str | int]] = {}
    by_year: dict[int, list[date]] = {}
    for d in entry_dates:
        by_year.setdefault(d.year, []).append(d)
    for y, ds in sorted(by_year.items()):
        span[str(y)] = {
            "n": len(ds),
            "entry_min": str(min(ds)),
            "entry_max": str(max(ds)),
        }

    n_inst = int(events["instrument"].n_unique())
    dup = events.height - events.unique(subset=["instrument", "entry_intent_date"]).height

    feat_cols = feature_cols or [c for c in events.columns if str(c).startswith("features.")]
    non_null: dict[str, dict[str, float | int]] = {}
    for c in feat_cols:
        if c not in events.columns:
            continue
        nn = int(events.get_column(c).drop_nulls().len())
        non_null[c] = {"n_non_null": nn, "pct": round(nn / events.height, 4)}

    return {
        "n_events": events.height,
        "n_instruments": n_inst,
        "entry_min": str(events["entry_intent_date"].min()),
        "entry_max": str(events["entry_intent_date"].max()),
        "duplicate_keys": int(dup),
        "years": {str(k): int(v) for k, v in sorted(years.items())},
        "years_span": span,
        "feature_non_null": non_null,
        "n_feature_cols_profiled": len(non_null),
    }
