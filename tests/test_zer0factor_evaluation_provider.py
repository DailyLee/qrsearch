from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pandas as pd
import polars as pl
import pytest

import qresearch.research.providers.zer0factor_evaluation as evaluation_provider
from qresearch.config.models import ResearchConfig
from qresearch.research.domain import FeatureSnapshot, ResearchDataset, sha256_path
from qresearch.research.providers.market import ResearchDataError
from qresearch.research.providers.zer0factor import _snapshot_hash
from qresearch.research.providers.zer0factor import Zer0FactorDependencyError
from qresearch.research.providers.zer0factor_evaluation import (
    FrozenSnapshotStorage,
    TrainUniversePro,
    run_factor_screening,
)


def _snapshot(*, duplicate_factor_key: bool = False, hash_mismatch: bool = False) -> FeatureSnapshot:
    frame = pl.DataFrame(
        {
            "sample_id": ["s1", "s2", "s3"],
            "instrument": ["000001.SZ", "000002.SZ", "000001.SZ"],
            "asof_session": [date(2021, 1, 4), date(2021, 1, 4), date(2022, 1, 4)],
            "effective_session": [date(2021, 1, 5), date(2021, 1, 5), date(2022, 1, 5)],
            "features.alpha": [1.0, 2.0, 3.0],
            "features.beta": [3.0, 2.0, 1.0],
        }
    ).with_columns(pl.col("asof_session", "effective_session").cast(pl.Date))
    if duplicate_factor_key:
        frame = pl.concat(
            [
                frame,
                frame.head(1).with_columns(
                    pl.lit("duplicate-sample").alias("sample_id"),
                    pl.lit(date(2021, 1, 6)).cast(pl.Date).alias("effective_session"),
                ),
            ]
        )
    base_manifest = {"zer0factor_repo_revision": "zer0factor-revision"}
    digest = _snapshot_hash(frame, base_manifest)
    return FeatureSnapshot(
        frame=frame,
        manifest={
            **base_manifest,
            "feature_snapshot_hash": digest,
            "meta": {"feature_snapshot_hash": "different" if hash_mismatch else digest},
        },
    )


def test_frozen_snapshot_storage_reads_only_requested_factor_and_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Calling FactorStorage.read would re-open mutable source partitions instead of the frozen run input.
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "zer0factor"))
    from zer0factor.storage import FactorStorage

    monkeypatch.setattr(
        FactorStorage,
        "read",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("mutable storage reread")),
    )

    values = FrozenSnapshotStorage(_snapshot()).read(
        "alpha", start_date="20210104", end_date="20211231"
    )

    pd.testing.assert_frame_equal(
        values.reset_index(drop=True),
        pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": ["20210104", "20210104"],
                "value": [1.0, 2.0],
            }
        ),
    )


def test_frozen_snapshot_storage_rejects_unknown_duplicate_or_mismatched_inputs() -> None:
    # Any of these cases makes the supposedly immutable factor input ambiguous or unauditable.
    with pytest.raises(ResearchDataError, match="unknown factor"):
        FrozenSnapshotStorage(_snapshot()).read("missing")
    with pytest.raises(ResearchDataError, match="duplicate"):
        FrozenSnapshotStorage(_snapshot(duplicate_factor_key=True)).read("alpha")
    with pytest.raises(ResearchDataError, match="hash"):
        FrozenSnapshotStorage(_snapshot(hash_mismatch=True))


def test_frozen_snapshot_storage_rejects_frame_changed_after_hashing() -> None:
    # Matching duplicate hash fields are insufficient if the in-memory snapshot content was mutated.
    snapshot = _snapshot()
    object.__setattr__(
        snapshot,
        "frame",
        snapshot.frame.with_columns(pl.col("features.alpha") * 10),
    )

    with pytest.raises(ResearchDataError, match="hash"):
        FrozenSnapshotStorage(snapshot)


def test_frozen_snapshot_storage_accepts_and_verifies_persisted_parquet_hash(
    tmp_path: Path,
) -> None:
    # Screening evidence must identify the exact persisted bytes, not the provider's in-memory ID.
    snapshot = _snapshot()
    snapshot_path = tmp_path / "feature_snapshot.parquet"
    snapshot.frame.write_parquet(snapshot_path)
    persisted_sha = sha256_path(snapshot_path)
    persisted = FeatureSnapshot(
        frame=snapshot.frame,
        manifest={
            "zer0factor_repo_revision": "zer0factor-revision",
            "feature_snapshot_path": str(snapshot_path),
            "feature_snapshot_hash": persisted_sha,
            "meta": {"feature_snapshot_hash": persisted_sha},
        },
    )

    assert FrozenSnapshotStorage(persisted).read("alpha").shape == (3, 3)

    snapshot_path.write_bytes(b"changed-after-materialize")
    with pytest.raises(ResearchDataError, match="hash"):
        FrozenSnapshotStorage(persisted)


class RecordingPro:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def universe(self, **kwargs: object) -> pd.DataFrame:
        self.calls.append(("universe", kwargs))
        return pd.DataFrame(
            {
                "trade_date": ["20210104"],
                "universe": [kwargs["universe"]],
                "ts_code": ["leaked-current-member"],
            }
        )

    def stock_basic(self, **kwargs: object) -> pd.DataFrame:
        raise AssertionError("historical membership must not use a current listing table")

    def pro_bar(self, **kwargs: object) -> pd.DataFrame:
        self.calls.append(("pro_bar", kwargs))
        return pd.DataFrame({"kind": ["prices"]})

    def index_daily(self, **kwargs: object) -> pd.DataFrame:
        self.calls.append(("index_daily", kwargs))
        return pd.DataFrame({"kind": ["benchmark"]})


def _role_samples() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "instrument": ["train-a", "validate-b", "holdout-c", "train-d"],
            "asof_session": [
                date(2021, 1, 4),
                date(2021, 1, 5),
                date(2021, 1, 6),
                date(2022, 1, 4),
            ],
            "role": ["train", "validate", "holdout_final", "train"],
        }
    ).with_columns(pl.col("asof_session").cast(pl.Date))


def test_train_universe_uses_frozen_train_membership_and_delegates_market_queries() -> None:
    # Asking LocalPro for universe membership would admit validate/holdout or today's listing set.
    pro = RecordingPro()
    adapter = TrainUniversePro(pro, _role_samples())

    membership = adapter.universe(
        universe="univ_trade_base",
        start_date="20210101",
        end_date="20211231",
        fields="trade_date,universe,ts_code",
    )
    prices = adapter.pro_bar(ts_code=None, start_date="20210101", end_date="20210131", adj=None)
    benchmark = adapter.index_daily(
        ts_code="000852.SH", start_date="20210101", end_date="20210131", fields="trade_date,pct_chg"
    )

    pd.testing.assert_frame_equal(
        membership,
        pd.DataFrame(
            {
                "trade_date": ["20210104"],
                "universe": ["univ_trade_base"],
                "ts_code": ["train-a"],
            }
        ),
    )
    pd.testing.assert_frame_equal(prices, pd.DataFrame({"kind": ["prices"]}))
    pd.testing.assert_frame_equal(benchmark, pd.DataFrame({"kind": ["benchmark"]}))
    assert [name for name, _ in pro.calls] == ["pro_bar", "index_daily"]


def _screening_dataset(snapshot_hash: str | None = None) -> ResearchDataset:
    snapshot_hash = snapshot_hash or _screening_snapshot_hash()
    return ResearchDataset(
        frame=pl.DataFrame(
            {
                "sample_id": ["train-a", "train-b", "validate-c", "holdout-d"],
                "instrument": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
                "asof_session": [
                    date(2021, 1, 4),
                    date(2021, 1, 4),
                    date(2022, 1, 4),
                    date(2023, 1, 4),
                ],
                "effective_session": [
                    date(2021, 1, 5),
                    date(2021, 1, 5),
                    date(2022, 1, 5),
                    date(2023, 1, 5),
                ],
                "role": ["train", "train", "validate", "holdout_final"],
                "features.alpha": [1.0, 2.0, 100.0, 200.0],
                "features.beta": [2.0, 1.0, 100.0, 200.0],
            }
        ).with_columns(pl.col("asof_session", "effective_session").cast(pl.Date)),
        metadata={
            "input_hashes": {
                "samples": "sample-input-sha",
                "features": snapshot_hash,
                "labels": "label-input-sha",
            },
            "purged_train_count": 1,
        },
    )


def _screening_snapshot(snapshot_path: Path | None = None) -> FeatureSnapshot:
    dataset = _screening_dataset()
    base_manifest = {"zer0factor_repo_revision": "zer0factor-revision"}
    frame = dataset.frame.select(
        "sample_id",
        "instrument",
        "asof_session",
        "effective_session",
        "features.alpha",
        "features.beta",
    )
    if snapshot_path is None:
        digest = _screening_snapshot_hash()
    else:
        frame.write_parquet(snapshot_path)
        digest = sha256_path(snapshot_path)
        base_manifest["feature_snapshot_path"] = str(snapshot_path)
    return FeatureSnapshot(
        frame=frame,
        manifest={
            **base_manifest,
            "feature_snapshot_hash": digest,
            "meta": {"feature_snapshot_hash": digest},
        },
    )


def _persisted_screening_inputs(tmp_path: Path) -> tuple[ResearchDataset, FeatureSnapshot]:
    snapshot = _screening_snapshot(tmp_path / "feature_snapshot.parquet")
    return _screening_dataset(str(snapshot.manifest["feature_snapshot_hash"])), snapshot


def _screening_snapshot_hash() -> str:
    frame = pl.DataFrame(
        {
            "sample_id": ["train-a", "train-b", "validate-c", "holdout-d"],
            "instrument": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
            "asof_session": [date(2021, 1, 4), date(2021, 1, 4), date(2022, 1, 4), date(2023, 1, 4)],
            "effective_session": [date(2021, 1, 5), date(2021, 1, 5), date(2022, 1, 5), date(2023, 1, 5)],
            "features.alpha": [1.0, 2.0, 100.0, 200.0],
            "features.beta": [2.0, 1.0, 100.0, 200.0],
        }
    ).with_columns(pl.col("asof_session", "effective_session").cast(pl.Date))
    return _snapshot_hash(frame, {"zer0factor_repo_revision": "zer0factor-revision"})


def _screening_config() -> ResearchConfig:
    return ResearchConfig.model_validate(
        {
            "sample": {
                "universe": "univ_trade_base",
                "start_date": "2021-01-01",
                "end_date": "2023-12-31",
            },
            "features": {
                "refs": [
                    {"name": "alpha", "availability_lag_sessions": 0},
                    {"name": "beta", "availability_lag_sessions": 0},
                ]
            },
            "benchmark": {"instrument": "000852.SH"},
            "ic_horizons": [1, 5],
        }
    )


def _write_service_artifacts(
    request: object,
    run_id: str,
    *,
    clean_keys: list[tuple[str, str]] | None = None,
    omit: str | None = None,
) -> SimpleNamespace:
    run_dir = request.output_dir / run_id
    run_dir.mkdir(parents=True)
    summary = pd.DataFrame({"factor_name": list(request.factor_names), "period": ["1D", "1D"]})
    if omit != "summary":
        summary.to_csv(run_dir / "summary.csv", index=False)
        summary.to_parquet(run_dir / "summary.parquet", index=False)
    metadata = {
        "run_id": run_id,
        "factor_names": list(request.factor_names),
        "start_date": request.start_date,
        "end_date": request.end_date,
        "periods": list(request.periods),
        "return_type": request.return_type,
        "universe": request.universe,
    }
    if omit != "metadata":
        metadata_payload: object = [] if omit == "metadata_non_object" else metadata
        (run_dir / "metadata.json").write_text(
            json.dumps(metadata_payload), encoding="utf-8"
        )
    if omit != "report":
        (run_dir / "report.md").write_text("# train screening\n", encoding="utf-8")

    keys = clean_keys or [("2021-01-04", "000001.SZ"), ("2021-01-04", "000002.SZ")]
    for factor_name in request.factor_names:
        factor_dir = run_dir / "factors" / factor_name
        factor_dir.mkdir(parents=True)
        clean = pd.DataFrame(
            {"factor": [1.0] * len(keys), "factor_quantile": [1] * len(keys), "1D": [0.01] * len(keys)},
            index=pd.MultiIndex.from_tuples(
                [(pd.Timestamp(day), instrument) for day, instrument in keys],
                names=["date", "asset"],
            ),
        )
        clean.to_parquet(factor_dir / "clean_factor_data.parquet")
        pd.DataFrame({"1D": [0.1]}, index=pd.to_datetime(["2021-01-04"])).to_parquet(
            factor_dir / "daily_ic.parquet"
        )
        if omit != f"{factor_name}:quantile_returns":
            pd.DataFrame({"1D": [0.01, 0.02]}, index=[1, 2]).to_parquet(
                factor_dir / "quantile_returns.parquet"
            )
    return SimpleNamespace(run=SimpleNamespace(run_dir=run_dir), summary=summary)


def _install_fake_public_evaluation_api(
    monkeypatch: pytest.MonkeyPatch,
    *,
    module_root: Path | None = None,
) -> tuple[type, type]:
    class EvaluationRequest:
        def __init__(self, **kwargs: object) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)
            self.factor_names = tuple(self.factor_names)
            self.periods = tuple(self.periods)
            self.output_dir = Path(self.output_dir)

    class EvaluationService:
        @classmethod
        def from_dependencies(cls, storage: object, pro: object) -> object:
            raise AssertionError("test must install its service factory")

    services_package = ModuleType("zer0factor.services")
    evaluate_module = ModuleType("zer0factor.services.evaluate")
    evaluate_module.EvaluationService = EvaluationService
    eval_package = ModuleType("zer0factor.eval")
    domain_module = ModuleType("zer0factor.eval.domain")
    domain_module.EvaluationRequest = EvaluationRequest
    resolved_root = module_root or Path(__file__).parents[2] / "zer0factor"
    evaluate_module.__file__ = str(resolved_root / "zer0factor" / "services" / "evaluate.py")
    domain_module.__file__ = str(resolved_root / "zer0factor" / "eval" / "domain.py")
    services_package.evaluate = evaluate_module
    eval_package.domain = domain_module
    monkeypatch.setitem(sys.modules, "zer0factor.services", services_package)
    monkeypatch.setitem(sys.modules, "zer0factor.services.evaluate", evaluate_module)
    monkeypatch.setitem(sys.modules, "zer0factor.eval", eval_package)
    monkeypatch.setitem(sys.modules, "zer0factor.eval.domain", domain_module)
    monkeypatch.setattr(
        evaluation_provider,
        "resolve_repo_revision",
        lambda root: ("zer0factor-revision", None),
        raising=False,
    )
    return EvaluationService, EvaluationRequest


def test_run_factor_screening_uses_public_service_and_audits_train_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Changing the request or accepting a non-train clean row breaks the frozen screening contract.
    EvaluationService, EvaluationRequest = _install_fake_public_evaluation_api(monkeypatch)

    captured: dict[str, object] = {}

    class FakeService:
        def run(self, request: EvaluationRequest, *, run_id: str) -> SimpleNamespace:
            captured["request"] = request
            captured["run_id"] = run_id
            captured["factor_values"] = captured["storage"].read(
                "alpha", start_date=request.start_date, end_date=request.end_date
            )
            captured["membership"] = captured["adapter_pro"].universe(
                universe=request.universe,
                start_date=request.start_date,
                end_date=request.end_date,
                fields="trade_date,universe,ts_code",
            )
            return _write_service_artifacts(request, run_id)

    def fake_from_dependencies(cls: type, storage: object, pro: object) -> FakeService:
        captured["storage"] = storage
        captured["adapter_pro"] = pro
        return FakeService()

    monkeypatch.setattr(EvaluationService, "from_dependencies", classmethod(fake_from_dependencies))
    output_dir = tmp_path / "artifacts"
    dataset, snapshot = _persisted_screening_inputs(tmp_path)

    result = run_factor_screening(
        dataset,
        snapshot,
        _screening_config(),
        RecordingPro(),
        output_dir,
        "market-run",
    )

    request = captured["request"]
    assert isinstance(request, EvaluationRequest)
    assert request.factor_names == ("alpha", "beta")
    assert request.factor_source == "explicit"
    assert request.start_date == "20210104"
    assert request.end_date == "20210104"
    assert request.periods == (1, 5)
    assert request.return_type == "open_t1"
    assert request.universe == "univ_trade_base"
    assert request.output_dir == output_dir / "zer0factor_evaluation"
    assert request.benchmark_index == "000852.SH"
    assert request.workers == 1
    assert request.generate_report is True
    assert captured["run_id"] == "market-run_train"
    assert set(captured["factor_values"]["ts_code"]) == {"000001.SZ", "000002.SZ"}
    assert set(captured["membership"]["ts_code"]) == {"000001.SZ", "000002.SZ"}

    assert result.summary.select("factor_name").to_series().to_list() == ["alpha", "beta"]
    assert result.run_dir == output_dir / "zer0factor_evaluation" / "market-run_train"
    assert result.manifest["evidence_role"] == "train_screening"
    assert result.manifest["oos"] is False
    assert result.manifest["promotable"] is False
    assert result.manifest["feature_snapshot_sha256"] == snapshot.manifest[
        "feature_snapshot_hash"
    ]
    assert result.manifest["zer0factor_revision"] == "zer0factor-revision"
    assert result.manifest["excluded_rows"] == {
        "validate": 1,
        "holdout_final": 1,
        "holdout_stress": 0,
        "total": 2,
    }
    assert (output_dir / "factor_screening_manifest.json").exists()
    assert (output_dir / "factor_redundancy.parquet").exists()
    assert "summary_parquet" in result.manifest["artifact_hashes"]
    assert "factor_redundancy" in result.manifest["artifact_hashes"]


@pytest.mark.parametrize(
    ("omit", "message"),
    [
        ("report", "report"),
        ("beta:quantile_returns", "quantile_returns"),
        ("metadata_non_object", "metadata"),
    ],
)
def test_run_factor_screening_rejects_missing_upstream_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    omit: str,
    message: str,
) -> None:
    # Treating a partial zer0factor run as evidence would make downstream review unauditable.
    EvaluationService, _ = _install_fake_public_evaluation_api(monkeypatch)

    class FakeService:
        def run(self, request: object, *, run_id: str) -> SimpleNamespace:
            return _write_service_artifacts(request, run_id, omit=omit)

    monkeypatch.setattr(
        EvaluationService,
        "from_dependencies",
        classmethod(lambda cls, storage, pro: FakeService()),
    )
    dataset, snapshot = _persisted_screening_inputs(tmp_path)

    with pytest.raises(ResearchDataError, match=message):
        run_factor_screening(
            dataset,
            snapshot,
            _screening_config(),
            RecordingPro(),
            tmp_path / "artifacts",
            f"missing-{message}",
        )


def test_run_factor_screening_rejects_clean_rows_outside_train_membership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A validate row inside clean_factor_data is direct evidence leakage even if request dates look correct.
    EvaluationService, _ = _install_fake_public_evaluation_api(monkeypatch)

    class FakeService:
        def run(self, request: object, *, run_id: str) -> SimpleNamespace:
            return _write_service_artifacts(
                request, run_id, clean_keys=[("2022-01-04", "000003.SZ")]
            )

    monkeypatch.setattr(
        EvaluationService,
        "from_dependencies",
        classmethod(lambda cls, storage, pro: FakeService()),
    )
    dataset, snapshot = _persisted_screening_inputs(tmp_path)

    with pytest.raises(ResearchDataError, match="train membership"):
        run_factor_screening(
            dataset,
            snapshot,
            _screening_config(),
            RecordingPro(),
            tmp_path / "artifacts",
            "leaked-clean-row",
        )


def test_run_factor_screening_maps_unavailable_public_service_to_dependency_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Inventing a fallback evaluator would hide an unavailable zer0factor installation from exit-code mapping.
    monkeypatch.setattr(
        "qresearch.research.providers.zer0factor_evaluation.get_settings",
        lambda: SimpleNamespace(zer0factor_root=str(tmp_path / "missing-zer0factor")),
    )
    dataset, snapshot = _persisted_screening_inputs(tmp_path)

    with pytest.raises(Zer0FactorDependencyError, match="zer0factor root"):
        run_factor_screening(
            dataset,
            snapshot,
            _screening_config(),
            RecordingPro(),
            tmp_path / "artifacts",
            "dependency-missing",
        )


def test_run_factor_screening_rejects_unpersisted_snapshot_before_evaluation(
    tmp_path: Path,
) -> None:
    # The provider's in-memory content ID is not final evidence for a screening run.
    with pytest.raises(ResearchDataError, match="persisted"):
        run_factor_screening(
            _screening_dataset(),
            _screening_snapshot(),
            _screening_config(),
            RecordingPro(),
            tmp_path / "artifacts",
            "unpersisted-snapshot",
        )


def test_run_factor_screening_rejects_evaluator_revision_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reporting only the snapshot revision conceals a changed evaluator implementation.
    EvaluationService, _ = _install_fake_public_evaluation_api(monkeypatch)
    monkeypatch.setattr(
        evaluation_provider,
        "resolve_repo_revision",
        lambda root: ("different-evaluator-revision", None),
        raising=False,
    )

    class FakeService:
        def run(self, request: object, *, run_id: str) -> SimpleNamespace:
            return _write_service_artifacts(request, run_id)

    monkeypatch.setattr(
        EvaluationService,
        "from_dependencies",
        classmethod(lambda cls, storage, pro: FakeService()),
    )
    dataset, snapshot = _persisted_screening_inputs(tmp_path)

    with pytest.raises(ResearchDataError, match="revision"):
        run_factor_screening(
            dataset,
            snapshot,
            _screening_config(),
            RecordingPro(),
            tmp_path / "artifacts",
            "revision-mismatch",
        )


def test_run_factor_screening_rejects_public_modules_outside_configured_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A preloaded same-name package must not override the explicitly configured zer0factor checkout.
    foreign_root = tmp_path / "foreign-package"
    EvaluationService, _ = _install_fake_public_evaluation_api(
        monkeypatch, module_root=foreign_root
    )

    class FakeService:
        def run(self, request: object, *, run_id: str) -> SimpleNamespace:
            return _write_service_artifacts(request, run_id)

    monkeypatch.setattr(
        EvaluationService,
        "from_dependencies",
        classmethod(lambda cls, storage, pro: FakeService()),
    )
    dataset, snapshot = _persisted_screening_inputs(tmp_path)

    with pytest.raises(Zer0FactorDependencyError, match="configured root"):
        run_factor_screening(
            dataset,
            snapshot,
            _screening_config(),
            RecordingPro(),
            tmp_path / "artifacts",
            "foreign-module",
        )
