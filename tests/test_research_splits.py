from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from qresearch.config.models import EvaluationConfig, HoldoutWindow
from qresearch.research.domain import ResearchDataset
from qresearch.research.providers.market import ResearchDataError
from qresearch.research.splits import assign_temporal_roles


def _dataset() -> ResearchDataset:
    rows = [
        ("s-2019-a", "000001.SZ", date(2019, 1, 2), date(2019, 1, 3), date(2019, 1, 8)),
        ("s-2019-b", "000002.SZ", date(2019, 1, 2), date(2019, 1, 3), date(2019, 1, 8)),
        ("s-2021-purge", "000001.SZ", date(2021, 12, 31), date(2022, 1, 4), date(2022, 1, 4)),
        ("s-2022-v", "000003.SZ", date(2022, 1, 4), date(2022, 1, 5), date(2022, 1, 10)),
        ("s-2024-h", "000004.SZ", date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 8)),
        ("s-2025-s", "000005.SZ", date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 8)),
    ]
    return ResearchDataset(
        frame=pl.DataFrame(
            rows,
            schema={
                "sample_id": pl.Utf8,
                "instrument": pl.Utf8,
                "asof_session": pl.Date,
                "effective_session": pl.Date,
                "label_end": pl.Date,
            },
            orient="row",
        ),
        metadata={"input_hashes": {"features": "snapshot-sha"}},
    )


def _evaluation() -> EvaluationConfig:
    return EvaluationConfig(
        train_years=["2019", "2021"],
        validate_years=["2022"],
        holdouts=[
            HoldoutWindow(years=["2024"], role="final"),
            HoldoutWindow(years=["2025"], role="stress"),
        ],
    )


def test_assign_temporal_roles_uses_declared_years_and_purges_overlapping_train_labels() -> None:
    # Positional or contiguous-range splitting would lose the two-stock 2019 date or misclassify 2021.
    assigned = assign_temporal_roles(_dataset(), _evaluation())

    assert assigned.frame.select("sample_id", "role").to_dicts() == [
        {"sample_id": "s-2019-a", "role": "train"},
        {"sample_id": "s-2019-b", "role": "train"},
        {"sample_id": "s-2022-v", "role": "validate"},
        {"sample_id": "s-2024-h", "role": "holdout_final"},
        {"sample_id": "s-2025-s", "role": "holdout_stress"},
    ]
    assert assigned.metadata["purged_train_count"] == 1
    assert assigned.metadata["split_summary"] == {
        "train": 2,
        "validate": 1,
        "holdout_final": 1,
        "holdout_stress": 1,
    }


@pytest.mark.parametrize(
    "evaluation",
    [
        EvaluationConfig(train_years=["2019"], validate_years=["2019", "2021", "2022", "2024", "2025"]),
        EvaluationConfig(train_years=["2019"], validate_years=["2021", "2022", "2024"]),
    ],
)
def test_assign_temporal_roles_rejects_ambiguous_or_undeclared_years(
    evaluation: EvaluationConfig,
) -> None:
    # Silently choosing one role, or dropping undeclared years, can leak observations across protocol boundaries.
    with pytest.raises(ResearchDataError, match="year"):
        assign_temporal_roles(_dataset(), evaluation)
