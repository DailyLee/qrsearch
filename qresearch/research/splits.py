"""Explicit temporal roles and label-overlap purging for market research."""

from __future__ import annotations

import polars as pl

from qresearch.config.models import EvaluationConfig
from qresearch.research.domain import ResearchDataset
from qresearch.research.providers.market import ResearchDataError


_ROLES = ("train", "validate", "holdout_final", "holdout_stress")


def _year_roles(evaluation: EvaluationConfig) -> dict[int, str]:
    declarations: list[tuple[str, list[str]]] = [
        ("train", evaluation.train_years),
        ("validate", evaluation.validate_years),
    ]
    declarations.extend(
        (f"holdout_{window.role}", window.years) for window in evaluation.holdouts
    )
    roles: dict[int, str] = {}
    for role, years in declarations:
        for raw_year in years:
            try:
                year = int(raw_year)
            except (TypeError, ValueError) as exc:
                raise ResearchDataError(f"invalid evaluation year: {raw_year!r}") from exc
            existing = roles.get(year)
            if existing is not None and existing != role:
                raise ResearchDataError(
                    f"evaluation year {year} is assigned to both {existing} and {role}"
                )
            roles[year] = role
    return roles


def assign_temporal_roles(
    dataset: ResearchDataset,
    evaluation: EvaluationConfig,
) -> ResearchDataset:
    """Assign declared year roles and purge train labels crossing into non-train data."""
    if "label_end" not in dataset.frame.columns:
        raise ResearchDataError("research dataset is missing label_end for temporal purge")

    year_roles = _year_roles(evaluation)
    years = set(dataset.frame.get_column("asof_session").dt.year().to_list())
    undeclared = sorted(years - set(year_roles))
    if undeclared:
        rendered = ", ".join(str(year) for year in undeclared)
        raise ResearchDataError(f"dataset contains undeclared evaluation year(s): {rendered}")

    frame = dataset.frame.with_columns(
        pl.col("asof_session")
        .dt.year()
        .replace_strict(year_roles)
        .cast(pl.Utf8)
        .alias("role")
    )
    first_non_train = frame.filter(pl.col("role") != "train").get_column("asof_session").min()
    purge = pl.lit(False)
    if first_non_train is not None:
        purge = (
            (pl.col("role") == "train")
            & pl.col("label_end").is_not_null()
            & (pl.col("label_end") >= pl.lit(first_non_train))
        )
    purged_train_count = frame.filter(purge).height
    frame = frame.filter(~purge)

    role_counts = {
        role: frame.filter(pl.col("role") == role).height
        for role in _ROLES
    }
    return ResearchDataset(
        frame=frame,
        metadata={
            **dataset.metadata,
            "purged_train_count": purged_train_count,
            "split_summary": role_counts,
        },
    )
