from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from qresearch.research.domain import ResearchDataset
from qresearch.research.providers.market import ResearchDataError
from qresearch.research.redundancy import compute_train_factor_redundancy


def _dataset() -> ResearchDataset:
    rows: list[dict[str, object]] = []
    values = [
        (date(2021, 1, 4), "train", [1.0, 2.0, 3.0], [3.0, 2.0, 1.0]),
        (date(2021, 1, 5), "train", [3.0, 1.0, 2.0], [1.0, 3.0, 2.0]),
        (date(2022, 1, 4), "validate", [1.0, 2.0, 3.0], [1.0, 2.0, 3.0]),
        (date(2023, 1, 4), "holdout_final", [1.0, 2.0, 3.0], [1.0, 2.0, 3.0]),
    ]
    for asof, role, alpha, beta in values:
        for index, instrument in enumerate(("000001.SZ", "000002.SZ", "000003.SZ")):
            rows.append(
                {
                    "sample_id": f"{asof.isoformat()}-{instrument}",
                    "instrument": instrument,
                    "asof_session": asof,
                    "effective_session": asof + timedelta(days=1),
                    "role": role,
                    "features.alpha": alpha[index],
                    "features.beta": beta[index],
                    "features.gamma": alpha[index],
                }
            )
    return ResearchDataset(
        frame=pl.DataFrame(rows).with_columns(
            pl.col("asof_session", "effective_session").cast(pl.Date)
        ),
        metadata={},
    )


def test_redundancy_uses_only_daily_train_cross_sections() -> None:
    # Including validate/holdout (+1 correlation) would dilute the two train dates (-1 correlation).
    result = compute_train_factor_redundancy(_dataset(), ["alpha", "beta", "gamma"])

    assert result.to_dicts() == [
        {
            "factor_a": "alpha",
            "factor_b": "beta",
            "mean_daily_rank_corr": -1.0,
            "valid_dates": 2,
        },
        {
            "factor_a": "alpha",
            "factor_b": "gamma",
            "mean_daily_rank_corr": 1.0,
            "valid_dates": 2,
        },
        {
            "factor_a": "beta",
            "factor_b": "gamma",
            "mean_daily_rank_corr": -1.0,
            "valid_dates": 2,
        },
    ]


def test_redundancy_rejects_missing_or_duplicate_feature_requests() -> None:
    # Silent null columns or duplicate pairs would present fabricated redundancy evidence.
    with pytest.raises(ResearchDataError, match="missing factor"):
        compute_train_factor_redundancy(_dataset(), ["alpha", "missing"])
    with pytest.raises(ResearchDataError, match="duplicate factor"):
        compute_train_factor_redundancy(_dataset(), ["alpha", "alpha"])
