"""Feature column allow/deny resolution for factor diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from qresearch.config.models import FactorsConfig

DEFAULT_EXCLUDE = [
    "features.name",
    "features.industry",
    "features.support_levels",
    "features.resistance_levels",
    "features.outperform_index",
    "features.stock_return",
]


def _is_numeric_enough(series: pl.Series, min_non_null: int) -> bool:
    try:
        casted = series.cast(pl.Float64, strict=False)
    except Exception:
        return False
    n = int(casted.drop_nulls().len())
    return n >= min_non_null


def resolve_feature_cols(events: pl.DataFrame, factors: FactorsConfig) -> list[str]:
    exclude = set(factors.exclude or [])
    include = list(factors.include or [])
    if include:
        candidates = [c for c in include if c in events.columns and c not in exclude]
    else:
        candidates = [
            c
            for c in events.columns
            if str(c).startswith("features.") and c not in exclude
        ]

    kept: list[str] = []
    for c in candidates:
        if _is_numeric_enough(events.get_column(c), int(factors.min_non_null)):
            kept.append(c)
    if factors.max_features is not None and len(kept) > int(factors.max_features):
        kept = kept[: int(factors.max_features)]
    return kept
