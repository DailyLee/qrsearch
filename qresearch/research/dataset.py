"""Assembly of immutable market-research inputs into a temporal dataset."""

from __future__ import annotations

from qresearch.research.domain import (
    OBSERVATION_KEYS,
    FeatureSnapshot,
    LabelSet,
    ResearchDataset,
    SampleSet,
)
from qresearch.research.providers.market import ResearchDataError


def _validate_join_source(name: str, frame, sample_keys: set[tuple[object, ...]]) -> None:
    keys = [tuple(row) for row in frame.select(list(OBSERVATION_KEYS)).iter_rows()]
    if len(keys) != len(set(keys)):
        raise ResearchDataError(f"{name} contains duplicate observation keys")
    if extra := set(keys) - sample_keys:
        raise ResearchDataError(f"{name} contains extra observation keys: {len(extra)}")


def _input_hashes(samples: SampleSet, features: FeatureSnapshot, labels: LabelSet) -> dict[str, object | None]:
    return {
        "samples": samples.manifest.get("sample_set_hash")
        or samples.manifest.get("zer0share_data_fingerprint"),
        "features": features.manifest.get("feature_snapshot_hash"),
        "labels": labels.spec.get("label_set_hash"),
    }


def build_research_dataset(
    samples: SampleSet,
    features: FeatureSnapshot,
    labels: LabelSet,
) -> ResearchDataset:
    """Left join point-in-time inputs by the complete immutable observation key."""
    sample_keys = {
        tuple(row) for row in samples.frame.select(list(OBSERVATION_KEYS)).iter_rows()
    }
    _validate_join_source("features", features.frame, sample_keys)
    _validate_join_source("labels", labels.frame, sample_keys)

    frame = samples.frame.join(features.frame, on=list(OBSERVATION_KEYS), how="left")
    frame = frame.join(labels.frame, on=list(OBSERVATION_KEYS), how="left")
    if frame.height != samples.frame.height:
        raise ResearchDataError("research dataset assembly changed sample row count")

    feature_columns = [column for column in features.frame.columns if column.startswith("features.")]
    feature_coverage = {
        column: frame.get_column(column).is_not_null().sum() / frame.height if frame.height else 0.0
        for column in feature_columns
    }
    label_status_counts = dict(
        sorted(
            (str(row["label_status"]), int(row["len"]))
            for row in frame.group_by("label_status").len().iter_rows(named=True)
            if row["label_status"] is not None
        )
    )
    return ResearchDataset(
        frame=frame,
        metadata={
            "feature_coverage": feature_coverage,
            "label_status_counts": label_status_counts,
            "input_hashes": _input_hashes(samples, features, labels),
        },
    )
