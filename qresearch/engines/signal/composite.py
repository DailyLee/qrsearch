"""Cross-sectional z-score composite features."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import polars as pl

if TYPE_CHECKING:
    from qresearch.config.models import CompositeConfig


def apply_composite(events: pl.DataFrame, composite: CompositeConfig) -> pl.DataFrame:
    if not composite.enabled or not composite.components:
        return events

    n = events.height
    if n == 0:
        return events

    score = np.zeros(n, dtype=float)
    weight_sum = 0.0
    for comp in composite.components:
        field = comp.field
        if field not in events.columns:
            continue
        raw = events.get_column(field).to_list()
        arr = np.asarray(
            [float(v) if v is not None else np.nan for v in raw],
            dtype=float,
        )
        mu = np.nanmean(arr)
        sigma = np.nanstd(arr, ddof=0)
        if not np.isfinite(sigma) or sigma < 1e-12:
            z = np.zeros(n, dtype=float)
        else:
            z = (arr - mu) / sigma
            z = np.nan_to_num(z, nan=0.0)
        if comp.ascending:
            z = -z
        score += float(comp.weight) * z
        weight_sum += abs(float(comp.weight))

    if weight_sum <= 0:
        return events

    col = f"features.{composite.name}" if not composite.name.startswith("features.") else composite.name
    out = events.with_columns(pl.Series(col, score.tolist()))
    return out
