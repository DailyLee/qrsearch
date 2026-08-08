"""Linear materialization and train-only evaluation for market factor research."""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import re
from typing import Any

import polars as pl
import yaml
from pydantic import ValidationError

from qresearch.config import get_settings, load_research_config
from qresearch.config.models import ResearchConfig
from qresearch.engines.data.vendor import VendorError, get_local_pro, load_trade_calendar
from qresearch.engines.data.panel import load_price_panel
from qresearch.engines.backtest.session import run_backtest
from qresearch.engines.analysis.invested import mean_invested_from_equity
from qresearch.engines.analysis.overfit import attach_overfit_metrics
from qresearch.engines.experiment.registry import RunWriter
from qresearch.engines.signal.engine import build_ranked
from qresearch.research.dataset import build_research_dataset
from qresearch.research.domain import (
    FeatureSnapshot,
    LabelSet,
    ResearchDataset,
    SampleSet,
    sha256_path,
)
from qresearch.research.labels import load_research_price_panel, materialize_labels
from qresearch.research.providers.market import MarketSampleProvider, ResearchDataError
from qresearch.research.providers.zer0factor import (
    Zer0FactorFeatureProvider,
    get_factor_storage,
)
from qresearch.research.providers.zer0factor_evaluation import run_factor_screening
from qresearch.research.splits import assign_temporal_roles
from qresearch.research.strategy import build_market_signal_frame


class ResearchConfigurationError(ValueError):
    """Raised when a research YAML cannot define the market workflow."""


_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


_MATERIALIZED_ARTIFACT_NAMES = {
    "config_snapshot": "config.snapshot.yaml",
    "meta": "meta.json",
    "sample_set": "artifacts/sample_set.parquet",
    "feature_snapshot": "artifacts/feature_snapshot.parquet",
    "feature_manifest": "artifacts/feature_manifest.json",
    "label_set": "artifacts/label_set.parquet",
    "dataset": "artifacts/dataset.parquet",
    "split_summary": "artifacts/split_summary.json",
}


def _load_config(config_path: str | Path) -> ResearchConfig:
    try:
        return load_research_config(config_path)
    except (FileNotFoundError, ValidationError, yaml.YAMLError, ValueError) as exc:
        raise ResearchConfigurationError(str(exc)) from exc


def _validate_run_id(run_id: str | None) -> None:
    if run_id is None:
        return
    if run_id in {".", ".."} or _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ResearchConfigurationError(
            "run_id must contain only letters, digits, dot, underscore, or hyphen"
        )


def _calendar_for(config: ResearchConfig) -> list:
    max_lag = max(ref.availability_lag_sessions for ref in config.features.refs)
    session_margin = max_lag + config.label.entry_lag_sessions + config.label.horizon_sessions + 10
    day_margin = max(31, session_margin * 3)
    calendar = load_trade_calendar(
        config.sample.start_date - timedelta(days=day_margin),
        config.sample.end_date + timedelta(days=day_margin),
    )
    if not calendar:
        raise ResearchDataError(
            "zer0share trading calendar is empty for the configured market sample"
        )
    return calendar


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _resolved(path: Path) -> str:
    return str(path.resolve())


def _materialized_artifacts(run_dir: Path) -> dict[str, str]:
    artifacts = {"run_dir": _resolved(run_dir)}
    artifacts.update(
        {
            key: _resolved(run_dir / relative)
            for key, relative in _MATERIALIZED_ARTIFACT_NAMES.items()
        }
    )
    return artifacts


def _require_feature_coverage(snapshot: FeatureSnapshot) -> None:
    missing_factors = []
    for column in snapshot.frame.columns:
        if column.startswith("features.") and snapshot.frame.get_column(column).is_not_null().sum() == 0:
            missing_factors.append(column.removeprefix("features."))
    if missing_factors:
        raise ResearchDataError(
            "zer0factor coverage is zero for configured factor(s): "
            + ", ".join(sorted(missing_factors))
        )


def _require_label_coverage(labels: LabelSet) -> None:
    if labels.frame.filter(pl.col("label_status") == "ok").is_empty():
        raise ResearchDataError("market label coverage has no usable rows")


def _persist_snapshot(writer: RunWriter, snapshot: FeatureSnapshot) -> FeatureSnapshot:
    path = writer.artifact_path("feature_snapshot.parquet")
    snapshot.frame.write_parquet(path)
    digest = sha256_path(path)
    manifest = dict(snapshot.manifest)
    manifest["feature_snapshot_path"] = _resolved(path)
    manifest["feature_snapshot_hash"] = digest
    nested_meta = dict(manifest.get("meta") or {})
    nested_meta["feature_snapshot_path"] = _resolved(path)
    nested_meta["feature_snapshot_hash"] = digest
    manifest["meta"] = nested_meta
    _write_json(writer.artifact_path("feature_manifest.json"), manifest)
    return FeatureSnapshot(frame=snapshot.frame, manifest=manifest)


def materialize_research(
    config_path: str | Path,
    run_id: str | None = None,
) -> dict[str, object]:
    """Materialize one immutable market dataset in a fixed linear order."""
    config = _load_config(config_path)
    _validate_run_id(run_id)
    settings = get_settings()
    requested_root = Path(settings.runs_dir) / run_id if run_id is not None else None
    if requested_root is not None and requested_root.exists():
        raise ResearchDataError(f"research run already exists: {run_id}")

    pro = get_local_pro()
    calendar = _calendar_for(config)
    storage = get_factor_storage(settings)

    samples = MarketSampleProvider(pro, calendar).materialize(config.sample)
    if samples.frame.is_empty():
        raise ResearchDataError(
            f"zer0share universe {config.sample.universe!r} "
            "has no market membership rows in the configured period"
        )
    raw_snapshot = Zer0FactorFeatureProvider(storage, calendar).materialize(
        samples, config.features.refs
    )
    _require_feature_coverage(raw_snapshot)

    writer = RunWriter(settings.runs_dir, run_id=run_id)
    config_snapshot_path = writer.write_config_snapshot(config)
    samples_path = writer.artifact_path("sample_set.parquet")
    samples.frame.write_parquet(samples_path)
    sample_manifest = {
        **samples.manifest,
        "sample_set_hash": sha256_path(samples_path),
        "sample_set_path": _resolved(samples_path),
    }
    samples = SampleSet(frame=samples.frame, manifest=sample_manifest)
    snapshot = _persist_snapshot(writer, raw_snapshot)

    try:
        panel = load_research_price_panel(
            samples,
            config.label,
            config,
            cache_dir=Path(settings.cache_dir) / "prices",
        )
    except VendorError as exc:
        raise ResearchDataError(str(exc)) from exc
    labels = materialize_labels(samples, panel, config.label)
    _require_label_coverage(labels)
    dataset = build_research_dataset(samples, snapshot, labels)
    dataset = assign_temporal_roles(dataset, config.evaluation)

    labels_path = writer.artifact_path("label_set.parquet")
    labels.frame.write_parquet(labels_path)
    dataset_path = writer.artifact_path("dataset.parquet")
    dataset.frame.write_parquet(dataset_path)
    _write_json(writer.artifact_path("split_summary.json"), dataset.metadata)

    meta_path = writer.write_meta(
        {
            "command": "research.materialize",
            "status": "materialized",
            "config_path": _resolved(Path(config_path)),
            "sample_rows": samples.frame.height,
            "feature_snapshot_sha256": snapshot.manifest["feature_snapshot_hash"],
            "input_hashes": dataset.metadata.get("input_hashes", {}),
        }
    )
    artifacts = _materialized_artifacts(writer.root)
    artifacts["config_snapshot"] = _resolved(config_snapshot_path)
    artifacts["meta"] = _resolved(meta_path)
    return {
        "run_id": writer.run_id,
        "summary": {
            "sample_rows": samples.frame.height,
            "feature_coverage": dataset.metadata.get("feature_coverage", {}),
            "label_status": dataset.metadata.get("label_status_counts", {}),
            "split_summary": dataset.metadata.get("split_summary", {}),
            "purged_train_count": dataset.metadata.get("purged_train_count", 0),
            "snapshot_sha256": snapshot.manifest["feature_snapshot_hash"],
        },
        "artifacts": artifacts,
    }


def _read_json_object(path: Path, description: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchDataError(f"invalid frozen {description}: {path}") from exc
    if not isinstance(payload, dict):
        raise ResearchDataError(f"frozen {description} must be a JSON object: {path}")
    return payload


def _load_frozen_run(
    run_dir: Path,
) -> tuple[FeatureSnapshot, ResearchDataset, dict[str, str]]:
    artifacts = _materialized_artifacts(run_dir)
    missing = [key for key, value in artifacts.items() if key != "run_dir" and not Path(value).is_file()]
    if missing:
        raise ResearchDataError(
            "research run is missing materialized artifact(s): " + ", ".join(missing)
        )

    snapshot_path = Path(artifacts["feature_snapshot"])
    manifest = _read_json_object(
        Path(artifacts["feature_manifest"]), "feature snapshot manifest"
    )
    recorded_path = manifest.get("feature_snapshot_path")
    if not isinstance(recorded_path, str) or Path(recorded_path).resolve() != snapshot_path.resolve():
        raise ResearchDataError("feature snapshot manifest path mismatch")
    digest = manifest.get("feature_snapshot_hash")
    if not isinstance(digest, str) or sha256_path(snapshot_path) != digest:
        raise ResearchDataError("feature snapshot persisted hash mismatch")
    try:
        snapshot_frame = pl.read_parquet(snapshot_path)
        dataset_frame = pl.read_parquet(artifacts["dataset"])
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise ResearchDataError("invalid frozen research parquet artifact") from exc
    snapshot = FeatureSnapshot(frame=snapshot_frame, manifest=manifest)
    dataset = ResearchDataset(
        frame=dataset_frame,
        metadata=_read_json_object(
            Path(artifacts["split_summary"]), "split summary"
        ),
    )
    return snapshot, dataset, artifacts


def _same_config(left: ResearchConfig, right: ResearchConfig) -> bool:
    return left.model_dump(mode="json") == right.model_dump(mode="json")


def evaluate_research(
    config_path: str | Path,
    run_id: str | None = None,
) -> dict[str, object]:
    """Evaluate only the frozen dataset/snapshot for one materialized run."""
    requested_config = _load_config(config_path)
    _validate_run_id(run_id)
    settings = get_settings()
    if run_id is None:
        materialized = materialize_research(config_path)
        run_id = str(materialized["run_id"])
    run_dir = Path(settings.runs_dir) / run_id
    if not run_dir.is_dir():
        raise ResearchDataError(f"research run not found: {run_id}")

    frozen_config = _load_config(run_dir / "config.snapshot.yaml")
    if not _same_config(requested_config, frozen_config):
        raise ResearchConfigurationError(
            f"config does not match frozen research run {run_id}"
        )
    snapshot, dataset, artifacts = _load_frozen_run(run_dir)
    pro = get_local_pro()
    screening = run_factor_screening(
        dataset,
        snapshot,
        frozen_config,
        pro,
        run_dir / "artifacts",
        run_id,
    )

    screening_manifest_path = run_dir / "artifacts" / "factor_screening_manifest.json"
    if not screening_manifest_path.is_file():
        raise ResearchDataError(
            f"factor screening manifest is missing: {screening_manifest_path}"
        )
    artifacts["factor_screening_manifest"] = _resolved(screening_manifest_path)
    artifacts["zer0factor_run_dir"] = _resolved(screening.run_dir)
    aliases = {
        "summary_csv": "zer0factor_summary_csv",
        "summary_parquet": "zer0factor_summary_parquet",
        "metadata": "zer0factor_metadata",
        "report": "zer0factor_report",
    }
    artifact_hashes = screening.manifest.get("artifact_hashes")
    if not isinstance(artifact_hashes, dict):
        raise ResearchDataError("factor screening artifact hashes are missing")
    for key, record in artifact_hashes.items():
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise ResearchDataError(f"invalid factor screening artifact audit: {key}")
        artifacts[aliases.get(str(key), str(key))] = str(record["path"])

    report_path = artifacts.get("zer0factor_report")
    if report_path is None:
        raise ResearchDataError("zer0factor screening report artifact is missing")
    meta_path = Path(artifacts["meta"])
    meta = _read_json_object(meta_path, "run meta")
    meta.update(
        {
            "command": "research.evaluate",
            "status": "evaluated",
            "feature_snapshot_sha256": snapshot.manifest["feature_snapshot_hash"],
            "zer0factor_screening_run_id": screening.run_dir.name,
            "zer0factor_screening_run_dir": _resolved(screening.run_dir),
        }
    )
    _write_json(meta_path, meta)
    return {
        "run_id": run_id,
        "summary": {
            "sample_rows": dataset.frame.height
            + int(dataset.metadata.get("purged_train_count", 0)),
            "feature_coverage": dataset.metadata.get("feature_coverage", {}),
            "label_status": dataset.metadata.get("label_status_counts", {}),
            "split_summary": dataset.metadata.get("split_summary", {}),
            "purged_train_count": dataset.metadata.get("purged_train_count", 0),
            "snapshot_sha256": snapshot.manifest["feature_snapshot_hash"],
            "screening_run_id": screening.run_dir.name,
            "screening_summary": screening.summary.to_dicts(),
            "screening_report": report_path,
        },
        "artifacts": artifacts,
    }


def run_research_strategy(
    config_path: str | Path,
    run_id: str | None = None,
    n_trials_assumed: int | None = None,
) -> dict[str, object]:
    """Run signals and the daily backtest from one newly frozen market dataset."""
    config = _load_config(config_path)
    materialized = materialize_research(config_path, run_id=run_id)
    resolved_run_id = str(materialized["run_id"])
    settings = get_settings()
    run_dir = Path(settings.runs_dir) / resolved_run_id
    snapshot, dataset, artifacts = _load_frozen_run(run_dir)

    calendar = _calendar_for(config)
    signal_frame = build_market_signal_frame(dataset, config, calendar)
    ranked = build_ranked(signal_frame, config)
    panel = load_price_panel(
        ranked,
        config,
        cache_dir=Path(settings.cache_dir) / "prices",
    )
    backtest = run_backtest(ranked, panel, config)
    assumed_trials = n_trials_assumed or config.gates.n_trials_assumed
    metrics = attach_overfit_metrics(backtest.metrics, n_trials=assumed_trials)
    metrics.update(mean_invested_from_equity(backtest.equity))

    artifact_dir = run_dir / "artifacts"
    ranked_path = artifact_dir / "ranked_signals.parquet"
    equity_path = artifact_dir / "equity.csv"
    trades_path = artifact_dir / "trades.csv"
    metrics_path = artifact_dir / "metrics.json"
    rejects_path = artifact_dir / "rejects_summary.json"
    ranked.write_parquet(ranked_path)
    pl.DataFrame(backtest.equity).write_csv(equity_path)
    pl.DataFrame(backtest.trades).write_csv(trades_path)
    _write_json(metrics_path, metrics)
    _write_json(rejects_path, backtest.rejects)
    artifacts.update(
        {
            "ranked_signals": _resolved(ranked_path),
            "equity": _resolved(equity_path),
            "trades": _resolved(trades_path),
            "metrics": _resolved(metrics_path),
            "rejects_summary": _resolved(rejects_path),
        }
    )

    meta_path = run_dir / "meta.json"
    meta = _read_json_object(meta_path, "run meta") if meta_path.is_file() else {}
    meta.update(
        {
            "command": "pipeline.research",
            "status": "backtested",
            "sample_kind": "market",
            "universe": config.sample.universe,
            "feature_snapshot_sha256": snapshot.manifest["feature_snapshot_hash"],
            "label_spec": config.label.model_dump(mode="json"),
            "split_summary": dataset.metadata.get("split_summary", {}),
            "zer0share_fingerprint": dataset.metadata.get("input_hashes", {}).get("samples"),
            "zer0factor_fingerprint": snapshot.manifest.get("zer0factor_data_fingerprint"),
            "zer0factor_revision": snapshot.manifest.get("zer0factor_repo_revision"),
            "n_trials_assumed": assumed_trials,
            "execution_model": "daily_open_historical_limits",
        }
    )
    _write_json(meta_path, meta)
    artifacts["meta"] = _resolved(meta_path)
    return {
        "run_id": resolved_run_id,
        "summary": {
            "sample_kind": "market",
            "universe": config.sample.universe,
            "snapshot_sha256": snapshot.manifest["feature_snapshot_hash"],
            "ranked_signals": ranked.height,
            "metrics": metrics,
        },
        "artifacts": artifacts,
    }
