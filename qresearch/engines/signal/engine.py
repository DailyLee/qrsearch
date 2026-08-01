"""State-independent signal filtering and ranking."""

from __future__ import annotations

import operator
from typing import Any

import polars as pl

from qresearch.config.models import FilterRule, ResearchConfig, SignalsConfig

_OPS = {
    "ge": operator.ge,
    "gt": operator.gt,
    "le": operator.le,
    "lt": operator.lt,
    "eq": operator.eq,
    "ne": operator.ne,
}


def _get_field(row: dict[str, Any], field: str) -> Any:
    if field in row:
        return row[field]
    if field.startswith("features.") and field in row:
        return row[field]
    # allow bare feature name
    alt = f"features.{field}" if not field.startswith("features.") else field
    return row.get(alt)


def apply_filter(row: dict[str, Any], rule: FilterRule) -> bool:
    val = _get_field(row, rule.field)
    if val is None:
        return False
    if rule.op == "between":
        if rule.value is None or rule.value_max is None:
            return False
        return float(rule.value) <= float(val) <= float(rule.value_max)
    op = _OPS[rule.op]
    return bool(op(float(val), float(rule.value)))


def rank_events(events: pl.DataFrame, signals: SignalsConfig) -> pl.DataFrame:
    rows = events.to_dicts()
    kept = []
    for r in rows:
        ok = True
        for rule in signals.filters:
            if not apply_filter(r, rule):
                ok = False
                break
        if ok:
            kept.append(r)
    if not kept:
        return events.head(0)

    def sort_key(r: dict) -> tuple:
        keys = []
        for rb in signals.rank_by:
            v = _get_field(r, rb.field)
            try:
                fv = float(v) if v is not None else (1e18 if rb.ascending else -1e18)
            except (TypeError, ValueError):
                fv = 1e18 if rb.ascending else -1e18
            keys.append(fv if rb.ascending else -fv)
        # stable tie-breakers
        keys.append(str(r.get("entry_intent_date")))
        keys.append(str(r.get("instrument")))
        return tuple(keys)

    if signals.rank_by:
        kept.sort(key=sort_key)
    # add rank score
    for i, r in enumerate(kept):
        r["rank_score"] = float(i)
    return pl.DataFrame(kept)


def build_ranked(events: pl.DataFrame, config: ResearchConfig) -> pl.DataFrame:
    from qresearch.engines.signal.composite import apply_composite

    enriched = apply_composite(events, config.signals.composite)
    return rank_events(enriched, config.signals)
