from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from qresearch.config.models import IngestConfig, ResearchConfig
from qresearch.engines.data.fingerprint import fingerprint_paths
from qresearch.engines.data.ingest import (
    IngestError,
    _coalesce_numeric,
    load_events,
    parse_date,
    resolve_event_paths,
    to_ts_code,
    validate_events,
)


def test_to_ts_code_variants():
    assert to_ts_code("sz.000001") == "000001.SZ"
    assert to_ts_code("sh.600000") == "600000.SH"
    assert to_ts_code("bj.430047") == "430047.BJ"
    assert to_ts_code("000001.SZ") == "000001.SZ"
    assert to_ts_code("600000.SH") == "600000.SH"
    assert to_ts_code("600519") == "600519.SH"  # bare SH-like
    assert to_ts_code("000001") == "000001.SZ"  # bare SZ-like
    with pytest.raises(IngestError):
        to_ts_code("not-a-code")


def test_parse_date_formats():
    formats = ["%Y/%m/%d", "%Y-%m-%d", "%Y%m%d"]
    assert parse_date("2025/1/6", formats) == date(2025, 1, 6)
    assert parse_date("2025-01-06", formats) == date(2025, 1, 6)
    assert parse_date("20250106", formats) == date(2025, 1, 6)
    assert parse_date(date(2024, 3, 1), formats) == date(2024, 3, 1)
    assert parse_date(datetime(2024, 3, 1, 12, 0), formats) == date(2024, 3, 1)
    with pytest.raises(IngestError):
        parse_date(None, formats)
    with pytest.raises(IngestError):
        parse_date("not-a-date", formats)


def test_coalesce_numeric_policies():
    assert _coalesce_numeric("1.5,2.5,3.5", "last") == 3.5
    assert _coalesce_numeric("1.5,2.5,3.5", "first") == 1.5
    assert _coalesce_numeric("1.5,2.5,3.5", "max") == 3.5
    assert _coalesce_numeric("42", "last") == 42.0
    assert _coalesce_numeric("", "last") is None
    assert _coalesce_numeric("a,b", "last") is None


def test_resolve_event_paths_and_missing(tmp_path: Path):
    f1 = tmp_path / "a.csv"
    f2 = tmp_path / "b.csv"
    f1.write_text("x\n", encoding="utf-8")
    f2.write_text("x\n", encoding="utf-8")
    paths = resolve_event_paths([f1, f2])
    assert paths == [f1, f2]

    globbed = resolve_event_paths(str(tmp_path / "*.csv"))
    assert len(globbed) == 2

    with pytest.raises(IngestError, match="not found"):
        resolve_event_paths(tmp_path / "missing.csv")


def test_load_and_validate_synthetic_events(tmp_path: Path):
    csv = tmp_path / "events.csv"
    csv.write_text(
        "code,buy_date,sell_date,box_quality,%B,bandwidth_percent\n"
        "sz.000001,2024/1/2,2024/1/10,0.95,-0.1,20.5\n"
        "sh.600000,2024-01-03,2024-01-12,0.96,0.2,15.0\n"
        "sz.000001,2024/1/2,2024/1/11,0.99,0.0,10.0\n",  # dup key -> keep first
        encoding="utf-8",
    )
    cfg = ResearchConfig(ingest=IngestConfig())
    df = load_events(csv, cfg)
    assert df.height == 2  # deduped
    assert set(df["instrument"].to_list()) == {"000001.SZ", "600000.SH"}
    assert "features.box_quality" in df.columns
    assert "features.pct_b" in df.columns

    summary = validate_events(csv, cfg)
    assert summary["n_events"] == 2
    assert summary["n_instruments"] == 2
    assert "features.box_quality" in summary["feature_cols"]


def test_fingerprint_paths(tmp_path: Path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("hello", encoding="utf-8")
    b.write_text("world", encoding="utf-8")
    fp1 = fingerprint_paths([a, b])
    fp2 = fingerprint_paths([b, a])  # order-independent
    assert fp1 == fp2
    assert fp1 != "unavailable"
    assert len(fp1) == 40
    assert fingerprint_paths([]) == "unavailable"
