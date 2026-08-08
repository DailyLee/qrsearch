"""Read-only, point-in-time feature snapshots from zer0factor."""

from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import sys
from typing import Any

import polars as pl

from qresearch.config.models import AppSettings, FeatureRefConfig
from qresearch.engines.data.fingerprint import fingerprint_dir_glob
from qresearch.research.domain import FeatureSnapshot, SampleSet, resolve_repo_revision


class Zer0FactorDependencyError(RuntimeError):
    """Raised when the configured, read-only zer0factor dependency is unavailable."""


class ResearchFeatureError(RuntimeError):
    """Raised when zer0factor data cannot form an unambiguous feature snapshot."""


_FACTOR_COLUMNS = ("trade_date", "ts_code", "value")


def get_factor_storage(settings: AppSettings) -> object:
    """Construct the configured FactorStorage without creating a fallback source."""
    root = Path(settings.zer0factor_root)
    factor_dir = Path(settings.zer0factor_factor_dir)
    db_path = Path(settings.zer0factor_db_path)
    if not root.is_dir():
        raise Zer0FactorDependencyError(f"zer0factor root is unavailable: {root}")
    if not factor_dir.is_dir():
        raise Zer0FactorDependencyError(f"zer0factor factor directory is unavailable: {factor_dir}")
    if not db_path.is_file():
        raise Zer0FactorDependencyError(f"zer0factor DuckDB is unavailable: {db_path}")

    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    try:
        from zer0factor.storage import FactorStorage
    except ImportError as exc:
        raise Zer0FactorDependencyError("zer0factor FactorStorage dependency is unavailable") from exc
    try:
        storage = FactorStorage(factor_dir, db_path, init_db=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise Zer0FactorDependencyError(f"could not open zer0factor storage: {exc}") from exc
    setattr(storage, "_qresearch_zer0factor_root", root)
    return storage


def list_available_factors(storage: object) -> list[str]:
    """Read the factor registry once, without touching factor values."""
    try:
        names = storage.list_factors()  # type: ignore[attr-defined]
    except (AttributeError, OSError, RuntimeError) as exc:
        raise Zer0FactorDependencyError("could not read zer0factor factor registry") from exc
    return sorted({str(name).strip() for name in names if str(name).strip()})


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    rendered = str(value)
    try:
        return datetime.strptime(rendered, "%Y%m%d").date()
    except ValueError:
        try:
            return date.fromisoformat(rendered[:10])
        except ValueError as exc:
            raise ResearchFeatureError(f"invalid factor trade_date: {value!r}") from exc


def _yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")


def _snapshot_hash(frame: pl.DataFrame, manifest: dict[str, object]) -> str:
    payload = {
        "frame": frame.to_dicts(),
        "manifest": manifest,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _zer0factor_lineage(storage: object) -> tuple[str | None, str | None, str | None]:
    configured_root = getattr(storage, "_qresearch_zer0factor_root", None)
    factor_dir = getattr(storage, "_factor_dir", None)
    if configured_root is not None:
        root = Path(configured_root)
    elif factor_dir is not None:
        root = Path(factor_dir).parent.parent
    else:
        return None, None, "zer0factor root is unavailable for revision lookup"

    revision, warning = resolve_repo_revision(root)
    if revision is not None:
        return revision, None, None
    try:
        package_version = version("zer0factor")
    except PackageNotFoundError:
        package_version = "unavailable"
    return None, package_version, warning or "zer0factor git revision is unavailable"


class Zer0FactorFeatureProvider:
    """Freeze exact-session zer0factor values into a feature snapshot."""

    def __init__(self, storage: object, calendar: list[date]) -> None:
        self._storage = storage
        self._calendar = sorted(set(calendar))
        self._session_index = {session: index for index, session in enumerate(self._calendar)}

    def materialize(self, samples: SampleSet, refs: list[FeatureRefConfig]) -> FeatureSnapshot:
        feature_names = [f"features.{ref.name}" for ref in refs]
        if len(feature_names) != len(set(feature_names)):
            raise ResearchFeatureError("feature column collision between zer0factor refs")

        available = list_available_factors(self._storage)
        unknown = [ref.name for ref in refs if ref.name not in available]
        if unknown:
            raise ResearchFeatureError(f"unknown factor: {', '.join(sorted(set(unknown)))}")

        sample_sessions = samples.frame["asof_session"].to_list()
        if not sample_sessions:
            frame = samples.frame.drop("sample_weight")
            manifest = self._manifest([], frame)
            digest = _snapshot_hash(frame, manifest)
            manifest["feature_snapshot_hash"] = digest
            manifest["meta"] = {"feature_snapshot_hash": digest}
            return FeatureSnapshot(frame=frame, manifest=manifest)
        for session in sample_sessions:
            if session not in self._session_index:
                raise ResearchFeatureError(f"sample asof_session absent from trading calendar: {session}")

        max_lag = max((ref.availability_lag_sessions for ref in refs), default=0)
        first_index = min(self._session_index[session] for session in sample_sessions)
        last_index = max(self._session_index[session] for session in sample_sessions)
        read_start = self._calendar[max(0, first_index - max_lag)]
        read_end = self._calendar[last_index]
        frame = samples.frame.drop("sample_weight")
        factor_manifest: list[dict[str, object]] = []

        for ref in refs:
            values = self._read_factor(ref, read_start, read_end)
            feature_column = f"features.{ref.name}"
            value_frame, source_rows, min_date, max_date = self._available_values(ref, values)
            frame = frame.join(value_frame, on=["instrument", "asof_session"], how="left")
            coverage = frame.get_column(feature_column).is_not_null().sum() / frame.height
            factor_dir = Path(getattr(self._storage, "_factor_dir", "")) / ref.name
            factor_manifest.append(
                {
                    "name": ref.name,
                    "declared_lag_sessions": ref.availability_lag_sessions,
                    "rows": source_rows,
                    "coverage": coverage,
                    "min_factor_date": min_date.isoformat() if min_date else None,
                    "max_factor_date": max_date.isoformat() if max_date else None,
                    "factor_directory_fingerprint": fingerprint_dir_glob(factor_dir),
                }
            )

        manifest = self._manifest(factor_manifest, frame)
        digest = _snapshot_hash(frame, manifest)
        manifest["feature_snapshot_hash"] = digest
        manifest["meta"] = {"feature_snapshot_hash": digest}
        return FeatureSnapshot(frame=frame, manifest=manifest)

    def _read_factor(self, ref: FeatureRefConfig, start: date, end: date) -> object:
        try:
            return self._storage.read(  # type: ignore[attr-defined]
                ref.name, start_date=_yyyymmdd(start), end_date=_yyyymmdd(end)
            )
        except FileNotFoundError as exc:
            raise ResearchFeatureError(f"unknown factor: {ref.name}") from exc
        except (AttributeError, OSError, RuntimeError, ValueError) as exc:
            raise Zer0FactorDependencyError(f"could not read zer0factor factor {ref.name!r}") from exc

    def _available_values(
        self, ref: FeatureRefConfig, values: object
    ) -> tuple[pl.DataFrame, int, date | None, date | None]:
        if not hasattr(values, "columns") or not hasattr(values, "to_dict"):
            raise ResearchFeatureError("zer0factor factor response must be a tabular dataframe")
        columns = set(values.columns)
        missing = [column for column in _FACTOR_COLUMNS if column not in columns]
        if missing:
            raise ResearchFeatureError(
                f"zer0factor factor response missing columns: {', '.join(missing)}"
            )
        records = values.loc[:, list(_FACTOR_COLUMNS)].to_dict("records")
        seen: set[tuple[date, str]] = set()
        rows: list[dict[str, object]] = []
        factor_dates: list[date] = []
        for record in records:
            trade_date = _as_date(record["trade_date"])
            instrument = str(record["ts_code"])
            key = (trade_date, instrument)
            if key in seen:
                raise ResearchFeatureError(
                    f"duplicate factor trade_date/ts_code: {trade_date.isoformat()} {instrument}"
                )
            seen.add(key)
            if trade_date not in self._session_index:
                raise ResearchFeatureError(f"factor trade_date absent from trading calendar: {trade_date}")
            factor_dates.append(trade_date)
            available_index = self._session_index[trade_date] + ref.availability_lag_sessions
            if available_index >= len(self._calendar):
                continue
            rows.append(
                {
                    "instrument": instrument,
                    "asof_session": self._calendar[available_index],
                    f"features.{ref.name}": record["value"],
                }
            )
        value_frame = pl.DataFrame(
            rows,
            schema={
                "instrument": pl.Utf8,
                "asof_session": pl.Date,
                f"features.{ref.name}": pl.Float64,
            },
        )
        return value_frame, len(records), min(factor_dates, default=None), max(factor_dates, default=None)

    def _manifest(
        self, factors: list[dict[str, object]], frame: pl.DataFrame
    ) -> dict[str, object]:
        revision, package_version, warning = _zer0factor_lineage(self._storage)
        manifest: dict[str, object] = {
            "feature_provider": "zer0factor",
            "rows": frame.height,
            "factors": factors,
            "zer0factor_repo_revision": revision,
        }
        if package_version is not None:
            manifest["zer0factor_package_version"] = package_version
        if warning is not None:
            manifest["warnings"] = [f"zer0factor revision unavailable: {warning}"]
        return manifest
