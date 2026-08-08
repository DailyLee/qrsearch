from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from qresearch.config.models import (
    FeatureRefConfig,
    FeatureSourceConfig,
    ResearchConfig,
    SampleConfig,
)
from qresearch.research.domain import SampleSet, resolve_repo_revision, sha256_path


def _observations(*, effective_after_asof: bool = True, weight: float = 1.0) -> pl.DataFrame:
    """A valid market sample; mutations below each exercise one invariant."""
    return pl.DataFrame(
        {
            "sample_id": ["market-1"],
            "instrument": ["000001.SZ"],
            "asof_session": [date(2024, 1, 3)],
            "effective_session": [date(2024, 1, 4) if effective_after_asof else date(2024, 1, 2)],
            "sample_weight": [weight],
        }
    ).with_columns(
        pl.col("asof_session").cast(pl.Date),
        pl.col("effective_session").cast(pl.Date),
    )


def test_sample_set_rejects_duplicate_observation_keys() -> None:
    # Removing uniqueness validation would permit two weights for one observation.
    frame = pl.concat([_observations(), _observations()])

    with pytest.raises(ValueError, match="duplicate"):
        SampleSet(frame=frame, manifest={})


def test_sample_set_rejects_effective_session_before_asof_session() -> None:
    # Reversing chronology validation would admit an observation before it becomes effective.
    with pytest.raises(ValueError, match="effective_session"):
        SampleSet(frame=_observations(effective_after_asof=False), manifest={})


def test_sample_set_rejects_negative_sample_weight() -> None:
    # Removing the lower bound would invert an observation's contribution.
    with pytest.raises(ValueError, match="sample_weight"):
        SampleSet(frame=_observations(weight=-0.01), manifest={})


@pytest.mark.parametrize(
    ("sample", "message"),
    [
        ({"start_date": "2024-01-01", "end_date": "2024-01-02"}, "universe"),
        ({"universe": "a", "end_date": "2024-01-02"}, "start_date"),
        ({"universe": "a", "start_date": "2024-01-01"}, "end_date"),
        (
            {"universe": "a", "start_date": "2024-01-03", "end_date": "2024-01-02"},
            "start_date",
        ),
    ],
)
def test_sample_config_rejects_missing_or_reversed_bounds(sample: dict[str, str], message: str) -> None:
    # Removing required/bound ordering validation permits an undefined market sample.
    with pytest.raises(ValidationError, match=message):
        SampleConfig.model_validate(sample)


def test_feature_source_requires_zer0factor_refs() -> None:
    # Removing this invariant would issue an evaluation request with no factors.
    with pytest.raises(ValidationError, match="refs"):
        FeatureSourceConfig()


@pytest.mark.parametrize(
    "field",
    [
        "mode",
        "kind",
        "sources",
        "inline",
    ],
)
def test_research_config_rejects_legacy_and_unknown_fields(field: str) -> None:
    # Changing extra handling to ignore would silently accept incompatible config.
    with pytest.raises(ValidationError, match=field):
        ResearchConfig.model_validate(
            {
                "sample": {
                    "universe": "a_share",
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-31",
                },
                "features": {"refs": [{"name": "daily_return_ma5", "availability_lag_sessions": 0}]},
                field: "legacy",
            }
        )


def test_feature_source_rejects_unknown_provider_and_invalid_family_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Bypassing the zer0factor family parser would accept a ref analysis cannot consume.
    with pytest.raises(ValidationError, match="provider"):
        FeatureSourceConfig.model_validate(
            {"provider": "inline", "refs": [{"name": "x", "availability_lag_sessions": 0}]}
        )

    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "zer0factor"))
    with pytest.raises(ValidationError, match="rolling_return"):
        FeatureSourceConfig.model_validate(
            {
                "refs": [{"name": "not_a_rolling_return_factor", "availability_lag_sessions": 0}],
                "analysis_family": "rolling_return",
            }
        )


def test_domain_helpers_hash_file_and_report_git_failure(tmp_path: Path) -> None:
    # Hashing metadata rather than file bytes, or raising on non-repos, breaks provenance capture.
    path = tmp_path / "artifact.txt"
    path.write_bytes(b"market-data")

    assert sha256_path(path) == "f58ff5e1e3acb63bc454ea3bef0336117d231f55e34ca93cf5be6960fb1e34f8"
    revision, error = resolve_repo_revision(tmp_path)
    assert revision is None
    assert error
