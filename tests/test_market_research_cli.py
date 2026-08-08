from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
import yaml
from typer.testing import CliRunner

from qresearch.cli import app
from qresearch.config.models import AppSettings
from qresearch.engines.data.vendor import VendorError
from qresearch.research.domain import (
    FactorScreeningResult,
    FeatureSnapshot,
    LabelSet,
    SampleSet,
    sha256_path,
)
from qresearch.research.providers.market import ResearchDataError
from qresearch.research.providers.zer0factor import Zer0FactorDependencyError
from qresearch.research.pipeline import ResearchConfigurationError


def _write_config(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "sample": {
                    "universe": "univ_trade_base",
                    "start_date": "2021-01-01",
                    "end_date": "2023-12-31",
                },
                "features": {
                    "provider": "zer0factor",
                    "refs": [
                        {"name": "alpha", "availability_lag_sessions": 0},
                        {"name": "beta", "availability_lag_sessions": 1},
                    ],
                },
                "evaluation": {
                    "train_years": ["2021"],
                    "validate_years": ["2022"],
                    "holdouts": [{"years": ["2023"], "role": "final"}],
                },
            }
        ),
        encoding="utf-8",
    )


def _samples() -> SampleSet:
    return SampleSet(
        frame=pl.DataFrame(
            {
                "sample_id": ["s-train", "s-validate", "s-holdout"],
                "instrument": ["000001.SZ", "000002.SZ", "000003.SZ"],
                "asof_session": [date(2021, 1, 4), date(2022, 1, 4), date(2023, 1, 3)],
                "effective_session": [date(2021, 1, 5), date(2022, 1, 5), date(2023, 1, 4)],
                "sample_weight": [1.0, 1.0, 1.0],
            }
        ).with_columns(pl.col("asof_session", "effective_session").cast(pl.Date)),
        manifest={"zer0share_data_fingerprint": "sample-source"},
    )


def _features(samples: SampleSet) -> FeatureSnapshot:
    return FeatureSnapshot(
        frame=samples.frame.drop("sample_weight").with_columns(
            pl.Series("features.alpha", [1.0, 2.0, 3.0], dtype=pl.Float64),
            pl.Series("features.beta", [3.0, None, 1.0], dtype=pl.Float64),
        ),
        manifest={
            "feature_provider": "zer0factor",
            "zer0factor_repo_revision": "zer0factor-revision",
            "factors": [
                {"name": "alpha", "coverage": 1.0},
                {"name": "beta", "coverage": 2 / 3},
            ],
        },
    )


def _labels(samples: SampleSet) -> LabelSet:
    return LabelSet(
        frame=samples.frame.drop("sample_weight").with_columns(
            pl.Series(
                "label_start",
                [date(2021, 1, 5), date(2022, 1, 5), date(2023, 1, 4)],
                dtype=pl.Date,
            ),
            pl.Series(
                "label_end",
                [date(2021, 1, 12), date(2022, 1, 12), date(2023, 1, 11)],
                dtype=pl.Date,
            ),
            pl.Series("forward_return", [0.1, None, -0.1], dtype=pl.Float64),
            pl.Series("label_status", ["ok", "missing_exit", "ok"], dtype=pl.Utf8),
        ),
        spec={"label_set_hash": "labels-in-memory", "price_panel_fingerprint": "prices"},
    )


def _install_materialize_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, list[str]]:
    import qresearch.research.pipeline as pipeline

    config_path = tmp_path / "market.yaml"
    _write_config(config_path)
    calls: list[str] = []
    samples = _samples()

    class FakeSampleProvider:
        def __init__(self, pro: object, calendar: list[date]) -> None:
            assert pro is sentinel_pro
            assert calendar

        def materialize(self, config: object) -> SampleSet:
            calls.append("samples")
            return samples

    class FakeFeatureProvider:
        def __init__(self, storage: object, calendar: list[date]) -> None:
            assert storage is sentinel_storage
            assert calendar

        def materialize(self, materialized: SampleSet, refs: list[object]) -> FeatureSnapshot:
            calls.append("features")
            assert materialized is samples
            assert len(refs) == 2
            return _features(samples)

    sentinel_pro = object()
    sentinel_storage = object()
    sentinel_panel = object()
    settings = AppSettings(
        runs_dir=tmp_path / "runs",
        cache_dir=tmp_path / "cache",
        studies_dir=tmp_path / "studies",
    )
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "get_local_pro", lambda: sentinel_pro)
    monkeypatch.setattr(
        pipeline,
        "load_trade_calendar",
        lambda start, end: [date(2020, 12, 31), date(2021, 1, 4), date(2022, 1, 4), date(2023, 1, 3), date(2024, 1, 2)],
    )
    monkeypatch.setattr(pipeline, "get_factor_storage", lambda _settings: sentinel_storage)
    monkeypatch.setattr(pipeline, "MarketSampleProvider", FakeSampleProvider)
    monkeypatch.setattr(pipeline, "Zer0FactorFeatureProvider", FakeFeatureProvider)

    def fake_panel(*args: object, **kwargs: object) -> object:
        calls.append("price_panel")
        return sentinel_panel

    def fake_labels(materialized: SampleSet, panel: object, config: object) -> LabelSet:
        calls.append("labels")
        assert materialized.frame.equals(samples.frame)
        assert panel is sentinel_panel
        return _labels(samples)

    monkeypatch.setattr(pipeline, "load_research_price_panel", fake_panel)
    monkeypatch.setattr(pipeline, "materialize_labels", fake_labels)
    return config_path, calls


def test_research_factors_returns_sorted_single_json_without_creating_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel_storage = object()
    monkeypatch.setattr("qresearch.cli.get_settings", lambda: AppSettings(runs_dir=tmp_path / "runs"))
    monkeypatch.setattr("qresearch.cli.get_factor_storage", lambda settings: sentinel_storage)
    monkeypatch.setattr(
        "qresearch.cli.list_available_factors",
        lambda storage: ["alpha", "beta"] if storage is sentinel_storage else [],
    )

    result = CliRunner().invoke(app, ["research", "factors"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1.0"
    assert payload["command"] == "research.factors"
    assert payload["summary"] == {"count": 2, "factors": ["alpha", "beta"]}
    assert not (tmp_path / "runs").exists()


def test_materialize_and_evaluate_emit_frozen_artifacts_and_reuse_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qresearch.research.pipeline as pipeline

    config_path, calls = _install_materialize_fakes(monkeypatch, tmp_path)
    runner = CliRunner()
    materialized = runner.invoke(
        app,
        ["research", "materialize", "--config", str(config_path), "--run-id", "market-run"],
    )

    assert materialized.exit_code == 0, materialized.output
    payload = json.loads(materialized.stdout)
    assert calls == ["samples", "features", "price_panel", "labels"]
    assert payload["schema_version"] == "1.0"
    assert payload["run_id"] == "market-run"
    assert payload["summary"]["sample_rows"] == 3
    assert payload["summary"]["feature_coverage"] == {
        "features.alpha": 1.0,
        "features.beta": 2 / 3,
    }
    assert payload["summary"]["label_status"] == {"missing_exit": 1, "ok": 2}

    artifacts = payload["artifacts"]
    required_materialized = {
        "run_dir",
        "config_snapshot",
        "meta",
        "sample_set",
        "feature_snapshot",
        "feature_manifest",
        "label_set",
        "dataset",
        "split_summary",
    }
    assert set(artifacts) == required_materialized
    assert all(Path(artifacts[key]).exists() for key in required_materialized - {"run_dir"})
    artifact_dir = Path(artifacts["run_dir"]) / "artifacts"
    assert {path.name for path in artifact_dir.iterdir()} == {
        "sample_set.parquet",
        "feature_snapshot.parquet",
        "feature_manifest.json",
        "label_set.parquet",
        "dataset.parquet",
        "split_summary.json",
    }
    snapshot_path = Path(artifacts["feature_snapshot"])
    expected_snapshot_sha = sha256_path(snapshot_path)
    snapshot_manifest = json.loads(Path(artifacts["feature_manifest"]).read_text(encoding="utf-8"))
    run_meta = json.loads(Path(artifacts["meta"]).read_text(encoding="utf-8"))
    dataset_meta = json.loads(Path(artifacts["split_summary"]).read_text(encoding="utf-8"))
    assert payload["summary"]["snapshot_sha256"] == expected_snapshot_sha
    assert snapshot_manifest["feature_snapshot_hash"] == expected_snapshot_sha
    assert snapshot_manifest["meta"]["feature_snapshot_hash"] == expected_snapshot_sha
    assert run_meta["feature_snapshot_sha256"] == expected_snapshot_sha
    assert dataset_meta["input_hashes"]["features"] == expected_snapshot_sha

    def fake_screening(dataset, snapshot, config, pro, output_dir, run_id):
        assert snapshot.manifest["feature_snapshot_hash"] == expected_snapshot_sha
        assert dataset.metadata["input_hashes"]["features"] == expected_snapshot_sha
        zf_run = output_dir / "zer0factor_evaluation" / f"{run_id}_train"
        zf_run.mkdir(parents=True)
        paths: dict[str, Path] = {
            "summary_csv": zf_run / "summary.csv",
            "summary_parquet": zf_run / "summary.parquet",
            "metadata": zf_run / "metadata.json",
            "report": zf_run / "report.md",
            "factors.alpha.daily_ic": zf_run / "factors" / "alpha" / "daily_ic.parquet",
            "factors.alpha.quantile_returns": zf_run / "factors" / "alpha" / "quantile_returns.parquet",
            "factor_redundancy": output_dir / "factor_redundancy.parquet",
        }
        for path in paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"evidence")
        manifest = {
            "feature_snapshot_sha256": expected_snapshot_sha,
            "artifact_hashes": {
                key: {"path": str(path), "sha256": sha256_path(path)} for key, path in paths.items()
            },
        }
        (output_dir / "factor_screening_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        return FactorScreeningResult(
            summary=pl.DataFrame({"factor_name": ["alpha"], "mean_ic": [0.05]}),
            run_dir=zf_run,
            manifest=manifest,
        )

    monkeypatch.setattr(pipeline, "run_factor_screening", fake_screening)
    monkeypatch.setattr(
        pipeline,
        "MarketSampleProvider",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("evaluate rematerialized samples")),
    )
    evaluated = runner.invoke(
        app,
        ["research", "evaluate", "--config", str(config_path), "--run-id", "market-run"],
    )

    assert evaluated.exit_code == 0, evaluated.output
    evaluation = json.loads(evaluated.stdout)
    assert evaluation["run_id"] == "market-run"
    assert evaluation["summary"]["screening_run_id"] == "market-run_train"
    assert evaluation["summary"]["screening_summary"] == [
        {"factor_name": "alpha", "mean_ic": 0.05}
    ]
    assert evaluation["summary"]["screening_report"].endswith("report.md")
    assert evaluation["summary"]["snapshot_sha256"] == expected_snapshot_sha
    assert evaluation["artifacts"]["factor_screening_manifest"].endswith(
        "factor_screening_manifest.json"
    )
    assert evaluation["artifacts"]["zer0factor_report"].endswith("report.md")
    assert evaluation["artifacts"]["factors.alpha.daily_ic"].endswith("daily_ic.parquet")
    assert evaluation["artifacts"]["factors.alpha.quantile_returns"].endswith(
        "quantile_returns.parquet"
    )


@pytest.mark.parametrize(
    ("exception", "expected_exit", "expected_code"),
    [
        (ResearchConfigurationError("invalid market config"), 2, "config"),
        (ResearchDataError("missing universe coverage"), 3, "data"),
        (Zer0FactorDependencyError("zer0factor unavailable"), 5, "dependency"),
    ],
)
def test_research_cli_maps_failures_to_contract_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    exception: Exception,
    expected_exit: int,
    expected_code: str,
) -> None:
    monkeypatch.setattr(
        "qresearch.cli.materialize_research",
        lambda config, run_id=None: (_ for _ in ()).throw(exception),
    )

    result = CliRunner().invoke(
        app,
        ["research", "materialize", "--config", "market.yaml"],
    )

    assert result.exit_code == expected_exit
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == expected_code
    assert result.stdout.count('"schema_version"') == 1


def test_missing_market_price_data_is_exit_3_not_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qresearch.research.pipeline as pipeline

    config_path, _ = _install_materialize_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(
        pipeline,
        "load_research_price_panel",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            VendorError("no daily bars for configured market universe")
        ),
    )

    result = CliRunner().invoke(
        app,
        ["research", "materialize", "--config", str(config_path), "--run-id", "no-prices"],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "data"
    assert "daily bars" in payload["error"]["message"]


def test_zero_factor_coverage_fails_before_loading_prices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qresearch.research.pipeline as pipeline

    config_path, _ = _install_materialize_fakes(monkeypatch, tmp_path)
    samples = _samples()

    class ZeroCoverageProvider:
        def __init__(self, storage: object, calendar: list[date]) -> None:
            pass

        def materialize(self, materialized: SampleSet, refs: list[object]) -> FeatureSnapshot:
            return FeatureSnapshot(
                frame=samples.frame.drop("sample_weight").with_columns(
                    pl.lit(None).cast(pl.Float64).alias("features.alpha"),
                    pl.lit(None).cast(pl.Float64).alias("features.beta"),
                ),
                manifest={"zer0factor_repo_revision": "zer0factor-revision"},
            )

    monkeypatch.setattr(pipeline, "Zer0FactorFeatureProvider", ZeroCoverageProvider)
    monkeypatch.setattr(
        pipeline,
        "load_research_price_panel",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("zero coverage must fail before price loading")
        ),
    )

    result = CliRunner().invoke(
        app,
        ["research", "materialize", "--config", str(config_path), "--run-id", "zero-coverage"],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "data"
    assert "alpha" in payload["error"]["message"]
    assert not (tmp_path / "runs" / "zero-coverage").exists()


def test_run_id_cannot_escape_the_runs_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, _ = _install_materialize_fakes(monkeypatch, tmp_path)

    result = CliRunner().invoke(
        app,
        ["research", "materialize", "--config", str(config_path), "--run-id", "../escape"],
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "config"
    assert "run_id" in payload["error"]["message"]
    assert not (tmp_path / "escape").exists()


def test_evaluate_with_missing_explicit_run_id_fails_without_materializing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qresearch.research.pipeline as pipeline

    config_path, calls = _install_materialize_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(
        pipeline,
        "MarketSampleProvider",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("explicit evaluate must not materialize")
        ),
    )

    result = CliRunner().invoke(
        app,
        ["research", "evaluate", "--config", str(config_path), "--run-id", "missing-run"],
    )

    assert result.exit_code == 3, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "data"
    assert "missing-run" in payload["error"]["message"]
    assert calls == []
    assert not (tmp_path / "runs" / "missing-run").exists()
