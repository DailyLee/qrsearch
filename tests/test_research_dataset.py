from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from qresearch.research.dataset import build_research_dataset
from qresearch.research.domain import FeatureSnapshot, LabelSet, SampleSet
from qresearch.research.providers.market import ResearchDataError


def _samples() -> SampleSet:
    return SampleSet(
        frame=pl.DataFrame(
            {
                "sample_id": ["market-1", "market-2"],
                "instrument": ["000001.SZ", "000002.SZ"],
                "asof_session": [date(2024, 1, 2), date(2024, 1, 2)],
                "effective_session": [date(2024, 1, 3), date(2024, 1, 3)],
                "sample_weight": [1.0, 1.0],
            }
        ).with_columns(pl.col("asof_session", "effective_session").cast(pl.Date)),
        manifest={"sample_set_hash": "samples-1"},
    )


def _features() -> FeatureSnapshot:
    return FeatureSnapshot(
        frame=_samples().frame.drop("sample_weight").with_columns(
            pl.Series("features.momentum", [1.0, None], dtype=pl.Float64)
        ),
        manifest={"feature_snapshot_hash": "features-1"},
    )


def _labels() -> LabelSet:
    return LabelSet(
        frame=_samples().frame.drop("sample_weight").with_columns(
            pl.Series("label_start", [date(2024, 1, 3), date(2024, 1, 3)], dtype=pl.Date),
            pl.Series("label_end", [date(2024, 1, 10), date(2024, 1, 10)], dtype=pl.Date),
            pl.Series("forward_return", [0.1, None], dtype=pl.Float64),
            pl.Series("label_status", ["ok", "missing_exit"], dtype=pl.Utf8),
        ),
        spec={"label_set_hash": "labels-1"},
    )


def _extra_feature_row() -> pl.DataFrame:
    return _features().frame.head(1).with_columns(pl.lit("market-extra").alias("sample_id"))


def _extra_label_row() -> pl.DataFrame:
    return _labels().frame.head(1).with_columns(pl.lit("market-extra").alias("sample_id"))


def test_build_dataset_left_joins_observation_keys_and_records_lineage() -> None:
    # An inner join would erase the missing feature or label states that downstream screening must see.
    dataset = build_research_dataset(_samples(), _features(), _labels())

    assert dataset.frame.height == 2
    assert dataset.frame.select("sample_id", "features.momentum", "forward_return", "label_status").to_dicts() == [
        {
            "sample_id": "market-1",
            "features.momentum": 1.0,
            "forward_return": 0.1,
            "label_status": "ok",
        },
        {
            "sample_id": "market-2",
            "features.momentum": None,
            "forward_return": None,
            "label_status": "missing_exit",
        },
    ]
    assert dataset.metadata == {
        "feature_coverage": {"features.momentum": 0.5},
        "label_status_counts": {"missing_exit": 1, "ok": 1},
        "input_hashes": {"samples": "samples-1", "features": "features-1", "labels": "labels-1"},
    }


@pytest.mark.parametrize("source", ["features", "labels"])
def test_build_dataset_rejects_extra_observation_keys(source: str) -> None:
    # Accepting an orphan key would make lineage look complete while concealing a mismatched snapshot.
    if source == "features":
        features = FeatureSnapshot(
            frame=pl.concat([_features().frame, _extra_feature_row()]),
            manifest={"feature_snapshot_hash": "features-1"},
        )
        labels = _labels()
    else:
        features = _features()
        labels = LabelSet(
            frame=pl.concat([_labels().frame, _extra_label_row()]),
            spec={"label_set_hash": "labels-1"},
        )

    with pytest.raises(ResearchDataError, match="extra observation keys"):
        build_research_dataset(_samples(), features, labels)


@pytest.mark.parametrize("source", ["features", "labels"])
def test_build_dataset_rejects_duplicate_observation_keys(source: str) -> None:
    # Permitting duplicate keys would duplicate a sample after assembly and alter its weight.
    if source == "features":
        features = FeatureSnapshot(
            frame=_features().frame,
            manifest={"feature_snapshot_hash": "features-1"},
        )
        object.__setattr__(features, "frame", pl.concat([features.frame, features.frame.head(1)]))
        labels = _labels()
    else:
        features = _features()
        labels = LabelSet(
            frame=_labels().frame,
            spec={"label_set_hash": "labels-1"},
        )
        object.__setattr__(labels, "frame", pl.concat([labels.frame, labels.frame.head(1)]))

    with pytest.raises(ResearchDataError, match="duplicate observation keys"):
        build_research_dataset(_samples(), features, labels)
