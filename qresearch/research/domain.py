"""Immutable, date-only datasets exchanged by the market research kernel."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import subprocess

import polars as pl


OBSERVATION_KEYS = ("sample_id", "instrument", "asof_session", "effective_session")


def _validate_observations(frame: pl.DataFrame, *, require_weight: bool = False) -> None:
    required = [*OBSERVATION_KEYS, *( ["sample_weight"] if require_weight else [])]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"research frame missing required columns: {', '.join(missing)}")

    for column in ("asof_session", "effective_session"):
        if frame.schema[column] != pl.Date:
            raise ValueError(f"{column} must have pl.Date dtype")
    if frame.select(pl.struct(list(OBSERVATION_KEYS)).is_duplicated().any()).item():
        raise ValueError("research frame contains duplicate observation keys")
    if frame.filter(pl.col("effective_session") < pl.col("asof_session")).height:
        raise ValueError("effective_session must be on or after asof_session")
    if require_weight and frame.filter(pl.col("sample_weight") < 0).height:
        raise ValueError("sample_weight must be non-negative")


@dataclass(frozen=True)
class SampleSet:
    frame: pl.DataFrame
    manifest: dict[str, object]

    def __post_init__(self) -> None:
        _validate_observations(self.frame, require_weight=True)
        object.__setattr__(self, "manifest", {**self.manifest, "sample_kind": "market"})


@dataclass(frozen=True)
class FeatureSnapshot:
    frame: pl.DataFrame
    manifest: dict[str, object]

    def __post_init__(self) -> None:
        _validate_observations(self.frame)


@dataclass(frozen=True)
class LabelSet:
    frame: pl.DataFrame
    spec: dict[str, object]

    def __post_init__(self) -> None:
        _validate_observations(self.frame)


@dataclass(frozen=True)
class ResearchDataset:
    frame: pl.DataFrame
    metadata: dict[str, object]

    def __post_init__(self) -> None:
        _validate_observations(self.frame)


@dataclass(frozen=True)
class FactorScreeningResult:
    summary: pl.DataFrame
    run_dir: Path
    manifest: dict[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_dir", Path(self.run_dir))


def sha256_path(path: Path) -> str:
    """Return the SHA-256 digest of a file without loading it all into memory."""
    digest = sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_repo_revision(root: Path) -> tuple[str | None, str | None]:
    """Resolve a git revision, preserving a diagnostic rather than raising."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        error = getattr(exc, "stderr", "") or str(exc)
        return None, error.strip()
    return result.stdout.strip(), None
