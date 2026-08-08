"""Train-only adapters for the public zer0factor evaluation service."""

from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import sys

import pandas as pd
import polars as pl

from qresearch.config import get_settings
from qresearch.config.models import ResearchConfig
from qresearch.research.domain import (
    FactorScreeningResult,
    FeatureSnapshot,
    ResearchDataset,
    resolve_repo_revision,
    sha256_path,
)
from qresearch.research.providers.market import ResearchDataError
from qresearch.research.providers.zer0factor import Zer0FactorDependencyError, _snapshot_hash
from qresearch.research.redundancy import compute_train_factor_redundancy


def _parse_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except (TypeError, ValueError) as exc:
        raise ResearchDataError(f"invalid evaluation date: {value!r}") from exc


def _require_snapshot_hash(snapshot: FeatureSnapshot) -> str:
    digest = snapshot.manifest.get("feature_snapshot_hash")
    meta = snapshot.manifest.get("meta")
    meta_digest = meta.get("feature_snapshot_hash") if isinstance(meta, dict) else None
    if not isinstance(digest, str) or not digest or meta_digest != digest:
        raise ResearchDataError("feature snapshot manifest hash mismatch")
    persisted_path = snapshot.manifest.get("feature_snapshot_path")
    if persisted_path is not None:
        if not isinstance(persisted_path, str) or not persisted_path:
            raise ResearchDataError("feature snapshot persisted path mismatch")
        path = Path(persisted_path)
        if not path.is_file() or sha256_path(path) != digest:
            raise ResearchDataError("feature snapshot persisted hash mismatch")
        try:
            persisted_frame = pl.read_parquet(path)
        except (OSError, pl.exceptions.PolarsError) as exc:
            raise ResearchDataError("invalid persisted feature snapshot") from exc
        if not persisted_frame.equals(snapshot.frame):
            raise ResearchDataError("feature snapshot persisted frame mismatch")
    else:
        hash_input_manifest = dict(snapshot.manifest)
        hash_input_manifest.pop("feature_snapshot_hash", None)
        hash_input_manifest.pop("meta", None)
        if _snapshot_hash(snapshot.frame, hash_input_manifest) != digest:
            raise ResearchDataError("feature snapshot content hash mismatch")
    return digest


class FrozenSnapshotStorage:
    """Expose one immutable FeatureSnapshot through FactorStorage's read shape."""

    def __init__(self, snapshot: FeatureSnapshot) -> None:
        _require_snapshot_hash(snapshot)
        self._snapshot = snapshot

    def read(
        self,
        factor_name: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        feature_column = f"features.{factor_name}"
        if feature_column not in self._snapshot.frame.columns:
            raise ResearchDataError(f"unknown factor in frozen snapshot: {factor_name}")

        frame = self._snapshot.frame
        if frame.select(pl.struct(["instrument", "asof_session"]).is_duplicated().any()).item():
            raise ResearchDataError("frozen snapshot contains duplicate factor keys")
        if start_date is not None:
            frame = frame.filter(pl.col("asof_session") >= pl.lit(_parse_date(start_date)))
        if end_date is not None:
            frame = frame.filter(pl.col("asof_session") <= pl.lit(_parse_date(end_date)))

        return pd.DataFrame(
            {
                "ts_code": frame.get_column("instrument").to_list(),
                "trade_date": [value.strftime("%Y%m%d") for value in frame["asof_session"]],
                "value": frame.get_column(feature_column).to_list(),
            }
        )


class TrainUniversePro:
    """Serve frozen train membership while delegating only market price queries."""

    def __init__(self, pro: object, train_samples: pl.DataFrame) -> None:
        missing = {"instrument", "asof_session", "role"}.difference(train_samples.columns)
        if missing:
            raise ResearchDataError(
                "train membership is missing columns: " + ", ".join(sorted(missing))
            )
        self._pro = pro
        self._train_samples = train_samples.filter(pl.col("role") == "train").select(
            "instrument", "asof_session"
        )

    def universe(
        self,
        universe: str,
        start_date: str,
        end_date: str | None,
        fields: str,
    ) -> pd.DataFrame:
        del fields
        frame = self._train_samples.filter(
            pl.col("asof_session") >= pl.lit(_parse_date(start_date))
        )
        if end_date is not None:
            frame = frame.filter(pl.col("asof_session") <= pl.lit(_parse_date(end_date)))
        frame = frame.unique(subset=["instrument", "asof_session"], maintain_order=True)
        return pd.DataFrame(
            {
                "trade_date": [value.strftime("%Y%m%d") for value in frame["asof_session"]],
                "universe": [universe] * frame.height,
                "ts_code": frame.get_column("instrument").to_list(),
            }
        )

    def pro_bar(self, **kwargs: object) -> pd.DataFrame:
        return self._pro.pro_bar(**kwargs)  # type: ignore[attr-defined,no-any-return]

    def index_daily(self, **kwargs: object) -> pd.DataFrame:
        return self._pro.index_daily(**kwargs)  # type: ignore[attr-defined,no-any-return]


def _load_evaluation_api() -> tuple[type, type, str]:
    root = Path(get_settings().zer0factor_root)
    if not root.is_dir():
        raise Zer0FactorDependencyError(f"zer0factor root is unavailable: {root}")
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    try:
        from zer0factor.services.evaluate import EvaluationService
        from zer0factor.eval.domain import EvaluationRequest
    except ImportError as exc:
        raise Zer0FactorDependencyError(
            "zer0factor EvaluationService dependency is unavailable"
        ) from exc
    resolved_root = root.resolve()
    for module_name in ("zer0factor.services.evaluate", "zer0factor.eval.domain"):
        module = sys.modules.get(module_name)
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            raise Zer0FactorDependencyError(
                f"zer0factor public module has no file identity: {module_name}"
            )
        try:
            Path(module_file).resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise Zer0FactorDependencyError(
                f"zer0factor public module is outside configured root: {module_name}"
            ) from exc
    revision, revision_error = resolve_repo_revision(resolved_root)
    if revision is None:
        raise Zer0FactorDependencyError(
            "zer0factor evaluator revision is unavailable"
            + (f": {revision_error}" if revision_error else "")
        )
    return EvaluationService, EvaluationRequest, revision


def _request_manifest(request: object) -> dict[str, object]:
    return {
        "factor_names": list(request.factor_names),  # type: ignore[attr-defined]
        "factor_source": request.factor_source,  # type: ignore[attr-defined]
        "start_date": request.start_date,  # type: ignore[attr-defined]
        "end_date": request.end_date,  # type: ignore[attr-defined]
        "periods": list(request.periods),  # type: ignore[attr-defined]
        "return_type": request.return_type,  # type: ignore[attr-defined]
        "universe": request.universe,  # type: ignore[attr-defined]
        "benchmark_index": request.benchmark_index,  # type: ignore[attr-defined]
        "workers": request.workers,  # type: ignore[attr-defined]
        "generate_report": request.generate_report,  # type: ignore[attr-defined]
    }


def _require_path(path: Path, description: str) -> Path:
    if not path.is_file():
        raise ResearchDataError(f"zer0factor {description} artifact is missing: {path}")
    return path


def _validate_metadata(path: Path, request: object) -> None:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchDataError(f"invalid zer0factor metadata artifact: {path}") from exc
    if not isinstance(metadata, dict):
        raise ResearchDataError("zer0factor metadata artifact must be a JSON object")
    expected = _request_manifest(request)
    for key in ("factor_names", "start_date", "end_date", "periods", "return_type", "universe"):
        if metadata.get(key) != expected[key]:
            raise ResearchDataError(f"zer0factor metadata {key} does not match evaluation request")


def _clean_factor_keys(path: Path) -> set[tuple[str, str]]:
    try:
        clean = pd.read_parquet(path).reset_index()
    except (OSError, ValueError) as exc:
        raise ResearchDataError(f"invalid zer0factor clean_factor_data artifact: {path}") from exc
    if not {"date", "asset"}.issubset(clean.columns):
        raise ResearchDataError("zer0factor clean_factor_data is missing date/asset index")
    try:
        dates = pd.to_datetime(clean["date"], errors="raise").dt.strftime("%Y%m%d")
    except (TypeError, ValueError) as exc:
        raise ResearchDataError("zer0factor clean_factor_data contains invalid dates") from exc
    return set(zip(dates.tolist(), clean["asset"].astype(str).tolist()))


def _audit_artifacts(
    run_dir: Path,
    request: object,
    train_samples: pl.DataFrame,
) -> dict[str, Path]:
    paths = {
        "summary_csv": _require_path(run_dir / "summary.csv", "summary"),
        "summary_parquet": _require_path(run_dir / "summary.parquet", "summary"),
        "metadata": _require_path(run_dir / "metadata.json", "metadata"),
        "report": _require_path(run_dir / "report.md", "report"),
    }
    _validate_metadata(paths["metadata"], request)
    membership = {
        (session.strftime("%Y%m%d"), str(instrument))
        for session, instrument in train_samples.select(
            "asof_session", "instrument"
        ).iter_rows()
    }
    for factor_name in request.factor_names:  # type: ignore[attr-defined]
        factor_dir = run_dir / "factors" / factor_name
        clean_key = f"factors.{factor_name}.clean_factor_data"
        paths[clean_key] = _require_path(
            factor_dir / "clean_factor_data.parquet", f"{factor_name} clean_factor_data"
        )
        paths[f"factors.{factor_name}.daily_ic"] = _require_path(
            factor_dir / "daily_ic.parquet", f"{factor_name} daily_ic"
        )
        paths[f"factors.{factor_name}.quantile_returns"] = _require_path(
            factor_dir / "quantile_returns.parquet", f"{factor_name} quantile_returns"
        )
        outside_train = _clean_factor_keys(paths[clean_key]) - membership
        if outside_train:
            raise ResearchDataError(
                f"zer0factor clean_factor_data contains {len(outside_train)} row(s) outside train membership"
            )
    return paths


def _excluded_rows(dataset: ResearchDataset) -> dict[str, int]:
    counts = {
        role: dataset.frame.filter(pl.col("role") == role).height
        for role in ("validate", "holdout_final", "holdout_stress")
    }
    return {**counts, "total": sum(counts.values())}


def run_factor_screening(
    dataset: ResearchDataset,
    snapshot: FeatureSnapshot,
    config: ResearchConfig,
    pro: object,
    output_dir: Path,
    run_id: str,
) -> FactorScreeningResult:
    """Run zer0factor's public evaluation service on frozen train evidence only."""
    if "role" not in dataset.frame.columns:
        raise ResearchDataError("research dataset is missing temporal role")
    unexpected_roles = set(dataset.frame.get_column("role").unique()) - {
        "train",
        "validate",
        "holdout_final",
        "holdout_stress",
    }
    if unexpected_roles:
        raise ResearchDataError(f"research dataset contains invalid temporal role: {unexpected_roles}")
    train_samples = dataset.frame.filter(pl.col("role") == "train")
    if train_samples.is_empty():
        raise ResearchDataError("factor screening requires train membership")

    snapshot_path = snapshot.manifest.get("feature_snapshot_path")
    if not isinstance(snapshot_path, str) or not snapshot_path:
        raise ResearchDataError(
            "factor screening requires a persisted feature snapshot"
        )
    snapshot_hash = _require_snapshot_hash(snapshot)
    input_hashes = dataset.metadata.get("input_hashes")
    dataset_snapshot_hash = input_hashes.get("features") if isinstance(input_hashes, dict) else None
    if dataset_snapshot_hash != snapshot_hash:
        raise ResearchDataError("research dataset feature snapshot hash mismatch")

    EvaluationService, EvaluationRequest, evaluator_revision = _load_evaluation_api()
    snapshot_revision = snapshot.manifest.get("zer0factor_repo_revision")
    if not isinstance(snapshot_revision, str) or not snapshot_revision:
        raise ResearchDataError("feature snapshot zer0factor revision is unavailable")
    if snapshot_revision != evaluator_revision:
        raise ResearchDataError(
            "feature snapshot and zer0factor evaluator revision mismatch"
        )
    min_train_session = train_samples.get_column("asof_session").min()
    max_train_session = train_samples.get_column("asof_session").max()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    request = EvaluationRequest(
        factor_names=tuple(ref.name for ref in config.features.refs),
        factor_source="explicit",
        start_date=min_train_session.strftime("%Y%m%d"),
        end_date=max_train_session.strftime("%Y%m%d"),
        periods=tuple(config.ic_horizons),
        return_type="open_t1",
        universe=config.sample.universe,
        output_dir=output_dir / "zer0factor_evaluation",
        benchmark_index=config.benchmark.instrument,
        workers=1,
        generate_report=True,
    )
    service = EvaluationService.from_dependencies(
        storage=FrozenSnapshotStorage(snapshot),
        pro=TrainUniversePro(pro, train_samples),
    )
    try:
        workflow_result = service.run(request, run_id=f"{run_id}_train")
    except ResearchDataError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ResearchDataError(f"zer0factor evaluation failed: {exc}") from exc

    run_dir = Path(workflow_result.run.run_dir)
    expected_run_dir = request.output_dir / f"{run_id}_train"
    if run_dir != expected_run_dir:
        raise ResearchDataError("zer0factor evaluation returned an unexpected run directory")
    artifact_paths = _audit_artifacts(run_dir, request, train_samples)

    feature_names = [ref.name for ref in config.features.refs]
    redundancy = compute_train_factor_redundancy(dataset, feature_names)
    redundancy_path = output_dir / "factor_redundancy.parquet"
    redundancy.write_parquet(redundancy_path)
    artifact_paths["factor_redundancy"] = redundancy_path
    artifact_hashes = {
        key: {"path": str(path), "sha256": sha256_path(path)}
        for key, path in artifact_paths.items()
    }

    manifest: dict[str, object] = {
        "evidence_role": "train_screening",
        "oos": False,
        "promotable": False,
        "zer0factor_run_dir": str(run_dir),
        "request": _request_manifest(request),
        "input_hashes": input_hashes if isinstance(input_hashes, dict) else {},
        "feature_snapshot_sha256": snapshot_hash,
        "zer0factor_revision": evaluator_revision,
        "snapshot_zer0factor_revision": snapshot_revision,
        "evaluation_zer0factor_revision": evaluator_revision,
        "artifact_hashes": artifact_hashes,
        "excluded_rows": _excluded_rows(dataset),
        "purged_train_count": int(dataset.metadata.get("purged_train_count", 0)),
    }
    manifest_path = output_dir / "factor_screening_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        summary = pl.read_parquet(artifact_paths["summary_parquet"])
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise ResearchDataError("invalid zer0factor summary artifact") from exc
    return FactorScreeningResult(summary=summary, run_dir=run_dir, manifest=manifest)
