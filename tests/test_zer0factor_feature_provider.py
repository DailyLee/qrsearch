from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import polars as pl
import pytest

from qresearch.config.models import AppSettings, FeatureRefConfig
from qresearch.research.domain import SampleSet
from qresearch.research.providers.zer0factor import (
    ResearchFeatureError,
    Zer0FactorFeatureProvider,
    Zer0FactorDependencyError,
    get_factor_storage,
    list_available_factors,
)


class FakeFactorStorage:
    """In-memory stand-in that exposes the read-only FactorStorage boundary."""

    def __init__(self, frames: dict[str, pd.DataFrame], factor_dir: Path) -> None:
        self.frames = frames
        self._factor_dir = factor_dir
        self.list_calls = 0
        self.read_calls: list[tuple[str, str | None, str | None]] = []

    def list_factors(self) -> list[str]:
        self.list_calls += 1
        return list(reversed(list(self.frames)))

    def read(self, name: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        self.read_calls.append((name, start_date, end_date))
        return self.frames[name].copy()

    def write(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("feature materialization must not write factors")

    def register(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("feature materialization must not register factors")

    def write_partitions(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("feature materialization must not write factor partitions")


def _samples() -> SampleSet:
    return SampleSet(
        frame=pl.DataFrame(
            {
                "sample_id": ["s1", "s2", "s3"],
                "instrument": ["000001.SZ", "000001.SZ", "000002.SZ"],
                "asof_session": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 3)],
                "effective_session": [date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 4)],
                "sample_weight": [1.0, 1.0, 1.0],
            }
        ).with_columns(
            pl.col("asof_session").cast(pl.Date),
            pl.col("effective_session").cast(pl.Date),
        ),
        manifest={},
    )


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["trade_date", "ts_code", "value"])


def test_materialize_joins_only_exact_available_sessions_and_reads_each_ref_once(tmp_path: Path) -> None:
    # Forward-filling alpha from Jan 2 into Jan 3 would leak stale factor values.
    storage = FakeFactorStorage(
        {
            "alpha": _frame(
                [
                    {"trade_date": "20240102", "ts_code": "000001.SZ", "value": 10.0},
                    {"trade_date": "20240102", "ts_code": "000002.SZ", "value": 40.0},
                ]
            ),
            "beta": _frame(
                [{"trade_date": "20240102", "ts_code": "000001.SZ", "value": 100.0}]
            ),
        },
        tmp_path,
    )
    refs = [
        FeatureRefConfig(name="alpha", availability_lag_sessions=0),
        FeatureRefConfig(name="beta", availability_lag_sessions=1),
    ]

    snapshot = Zer0FactorFeatureProvider(
        storage, [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    ).materialize(_samples(), refs)

    assert snapshot.frame.select(
        "sample_id", "features.alpha", "features.beta"
    ).to_dicts() == [
        {"sample_id": "s1", "features.alpha": 10.0, "features.beta": None},
        {"sample_id": "s2", "features.alpha": None, "features.beta": 100.0},
        {"sample_id": "s3", "features.alpha": None, "features.beta": None},
    ]
    assert storage.read_calls == [
        ("alpha", "20240102", "20240103"),
        ("beta", "20240102", "20240103"),
    ]
    assert storage.list_calls == 1
    assert snapshot.manifest["feature_snapshot_hash"] == snapshot.manifest["meta"][
        "feature_snapshot_hash"
    ]
    assert len(snapshot.manifest["feature_snapshot_hash"]) == 64
    assert snapshot.manifest["factors"] == [
        {
            "name": "alpha",
            "declared_lag_sessions": 0,
            "rows": 2,
            "coverage": 1 / 3,
            "min_factor_date": "2024-01-02",
            "max_factor_date": "2024-01-02",
            "factor_directory_fingerprint": "unavailable",
        },
        {
            "name": "beta",
            "declared_lag_sessions": 1,
            "rows": 1,
            "coverage": 1 / 3,
            "min_factor_date": "2024-01-02",
            "max_factor_date": "2024-01-02",
            "factor_directory_fingerprint": "unavailable",
        },
    ]


def test_materialize_rejects_unknown_factors_before_reading_values(tmp_path: Path) -> None:
    # Silently yielding nulls for a misspelled ref hides a broken research configuration.
    storage = FakeFactorStorage({"alpha": _frame([])}, tmp_path)

    with pytest.raises(ResearchFeatureError, match="unknown factor"):
        Zer0FactorFeatureProvider(storage, [date(2024, 1, 2), date(2024, 1, 3)]).materialize(
            _samples(), [FeatureRefConfig(name="missing", availability_lag_sessions=0)]
        )

    assert storage.read_calls == []


def test_materialize_rejects_duplicate_factor_keys_and_feature_column_collisions(tmp_path: Path) -> None:
    # Either duplicate would make one feature value or one output column ambiguous.
    duplicate_rows = _frame(
        [
            {"trade_date": "20240102", "ts_code": "000001.SZ", "value": 1.0},
            {"trade_date": "20240102", "ts_code": "000001.SZ", "value": 2.0},
        ]
    )
    storage = FakeFactorStorage({"alpha": duplicate_rows}, tmp_path)
    provider = Zer0FactorFeatureProvider(storage, [date(2024, 1, 2), date(2024, 1, 3)])

    with pytest.raises(ResearchFeatureError, match="duplicate factor"):
        provider.materialize(_samples(), [FeatureRefConfig(name="alpha", availability_lag_sessions=0)])

    storage = FakeFactorStorage({"alpha": _frame([])}, tmp_path)
    with pytest.raises(ResearchFeatureError, match="feature column collision"):
        Zer0FactorFeatureProvider(storage, [date(2024, 1, 2), date(2024, 1, 3)]).materialize(
            _samples(),
            [
                FeatureRefConfig(name="alpha", availability_lag_sessions=0),
                FeatureRefConfig(name="alpha", availability_lag_sessions=1),
            ],
        )


def test_list_available_factors_reads_registry_once_without_reading_factor_values(tmp_path: Path) -> None:
    # Repeated registry reads can race a live factor writer and do not establish one frozen catalog.
    storage = FakeFactorStorage({"zeta": _frame([]), "alpha": _frame([]), "": _frame([])}, tmp_path)

    assert list_available_factors(storage) == ["alpha", "zeta"]
    assert storage.list_calls == 1
    assert storage.read_calls == []


def test_app_settings_define_strict_zer0factor_locations() -> None:
    # Omitting these paths would let provider setup invent an implicit factor source.
    fields = AppSettings.model_fields

    assert fields["zer0factor_root"].default == r"C:\Users\dl271\Downloads\code\zer0factor"
    assert fields["zer0factor_factor_dir"].default == Path("../zer0factor/data/factors")
    assert fields["zer0factor_db_path"].default == Path("../zer0factor/data/factors.duckdb")


def test_get_factor_storage_reports_missing_dependencies_without_creating_them(tmp_path: Path) -> None:
    # Constructing a fallback storage would fabricate a factor source and hide an unsynced dependency.
    factor_dir = tmp_path / "missing-factors"
    db_path = tmp_path / "missing.duckdb"
    settings = AppSettings(
        zer0factor_root=str(tmp_path / "missing-repo"),
        zer0factor_factor_dir=factor_dir,
        zer0factor_db_path=db_path,
    )

    with pytest.raises(Zer0FactorDependencyError, match="zer0factor root"):
        get_factor_storage(settings)

    assert not factor_dir.exists()
    assert not db_path.exists()
