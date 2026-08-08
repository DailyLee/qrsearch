"""Train-only cross-factor redundancy diagnostics."""

from __future__ import annotations

from itertools import combinations
import math

import polars as pl

from qresearch.research.domain import ResearchDataset
from qresearch.research.providers.market import ResearchDataError


_OUTPUT_SCHEMA = {
    "factor_a": pl.Utf8,
    "factor_b": pl.Utf8,
    "mean_daily_rank_corr": pl.Float64,
    "valid_dates": pl.Int64,
}


def compute_train_factor_redundancy(
    dataset: ResearchDataset,
    feature_names: list[str],
) -> pl.DataFrame:
    """Average same-session cross-sectional rank correlation over train dates."""
    if len(feature_names) != len(set(feature_names)):
        raise ResearchDataError("duplicate factor name in redundancy request")
    if "role" not in dataset.frame.columns:
        raise ResearchDataError("research dataset is missing temporal role")

    columns = [f"features.{name}" for name in feature_names]
    missing = [name for name, column in zip(feature_names, columns) if column not in dataset.frame]
    if missing:
        raise ResearchDataError("missing factor for redundancy: " + ", ".join(missing))
    if len(columns) < 2:
        return pl.DataFrame(schema=_OUTPUT_SCHEMA)

    train = dataset.frame.filter(pl.col("role") == "train").select(
        "asof_session", *columns
    )
    if train.is_empty():
        raise ResearchDataError("factor redundancy requires train rows")
    pandas_train = train.to_pandas()

    daily_correlations: dict[tuple[str, str], list[float]] = {
        pair: [] for pair in combinations(feature_names, 2)
    }
    for _, daily in pandas_train.groupby("asof_session", sort=True):
        ranked = daily[columns].rank(method="average")
        for factor_a, factor_b in daily_correlations:
            correlation = ranked[f"features.{factor_a}"].corr(
                ranked[f"features.{factor_b}"], method="pearson"
            )
            if correlation is not None and math.isfinite(float(correlation)):
                daily_correlations[(factor_a, factor_b)].append(float(correlation))

    rows = []
    for (factor_a, factor_b), values in daily_correlations.items():
        rows.append(
            {
                "factor_a": factor_a,
                "factor_b": factor_b,
                "mean_daily_rank_corr": sum(values) / len(values) if values else None,
                "valid_dates": len(values),
            }
        )
    return pl.DataFrame(rows, schema=_OUTPUT_SCHEMA)
