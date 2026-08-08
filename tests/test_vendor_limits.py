"""Stock daily-loader contract for historical exchange price limits."""

from __future__ import annotations

from datetime import date

import pandas as pd
import polars as pl

from qresearch.engines.data import vendor


def test_load_daily_long_merges_historical_limits_without_future_factor_fill(monkeypatch, tmp_path):
    """Catches losing daily limits or backfilling a factor from a future session."""

    class FakePro:
        def __init__(self) -> None:
            self.daily_data = pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20240102", "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.1, "vol": 100.0, "amount": 1_000.0},
                    {"ts_code": "000001.SZ", "trade_date": "20240103", "open": 10.1, "high": 10.3, "low": 9.9, "close": 10.2, "vol": 110.0, "amount": 1_100.0},
                    {"ts_code": "000002.SZ", "trade_date": "20240102", "open": 20.0, "high": 20.2, "low": 19.8, "close": 20.1, "vol": 200.0, "amount": 2_000.0},
                    {"ts_code": "000002.SZ", "trade_date": "20240103", "open": 20.1, "high": 20.3, "low": 19.9, "close": 20.2, "vol": 210.0, "amount": 2_100.0},
                ]
            )
            self.adj_data = pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20240103", "adj_factor": 1.2},
                    {"ts_code": "000002.SZ", "trade_date": "20240102", "adj_factor": 2.0},
                ]
            )
            self.limit_data = pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20240102", "up_limit": 11.0, "down_limit": 9.0},
                    {"ts_code": "000001.SZ", "trade_date": "20240103", "up_limit": 12.0, "down_limit": 10.0},
                    {"ts_code": "000002.SZ", "trade_date": "20240102", "up_limit": 22.0, "down_limit": 18.0},
                ]
            )
            self.stk_limit_calls: list[dict[str, object]] = []

        def daily(self, *, ts_code: str, **_kwargs):
            return self.daily_data[self.daily_data["ts_code"] == ts_code].copy()

        def adj_factor(self, *, ts_code: str, **_kwargs):
            return self.adj_data[self.adj_data["ts_code"] == ts_code].copy()

        def stk_limit(self, **kwargs):
            self.stk_limit_calls.append(kwargs)
            return self.limit_data.copy()

    pro = FakePro()
    expected_paths = []
    for table in ("daily_kline", "adj_factor", "stk_limit"):
        path = tmp_path / "stock" / table / "date=20240102" / "data.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        expected_paths.append(path)
    fingerprint_paths: list[object] = []
    monkeypatch.setattr(vendor, "get_local_pro", lambda: pro)
    monkeypatch.setenv("ZER0SHARE_DATA", str(tmp_path))
    monkeypatch.setattr(
        vendor,
        "fingerprint_paths",
        lambda paths: fingerprint_paths.extend(paths) or "fixture-fingerprint",
    )

    bars, fingerprint = vendor.load_daily_long(
        ["000001.SZ", "000002.SZ"], date(2024, 1, 2), date(2024, 1, 3)
    )

    assert fingerprint == "fixture-fingerprint"
    assert bars.select("instrument", "trade_date", "adj_factor", "up_limit", "down_limit").to_dicts() == [
        {
            "instrument": "000001.SZ",
            "trade_date": date(2024, 1, 3),
            "adj_factor": 1.2,
            "up_limit": 12.0,
            "down_limit": 10.0,
        },
        {
            "instrument": "000002.SZ",
            "trade_date": date(2024, 1, 2),
            "adj_factor": 2.0,
            "up_limit": 22.0,
            "down_limit": 18.0,
        },
        {
            "instrument": "000002.SZ",
            "trade_date": date(2024, 1, 3),
            "adj_factor": 2.0,
            "up_limit": None,
            "down_limit": None,
        },
    ]
    assert pro.stk_limit_calls == [
        {
            "start_date": "20240102",
            "end_date": "20240103",
            "fields": "ts_code,trade_date,up_limit,down_limit",
        }
    ]
    assert set(fingerprint_paths) == set(expected_paths)


def test_empty_and_index_bars_leave_non_applicable_limits_null(monkeypatch):
    """Catches inventing a stock-style limit for an index or omitting the empty schema."""

    class FakePro:
        def index_daily(self, **_kwargs):
            return pd.DataFrame(
                [
                    {
                        "ts_code": "000300.SH",
                        "trade_date": "20240102",
                        "open": 3.0,
                        "high": 3.1,
                        "low": 2.9,
                        "close": 3.05,
                        "vol": 1_000.0,
                        "amount": 10_000.0,
                    }
                ]
            )

    empty, _ = vendor.load_daily_long(["000300.SH"], date(2024, 1, 2), date(2024, 1, 2))
    assert empty.schema["up_limit"] == pl.Float64
    assert empty.schema["down_limit"] == pl.Float64

    monkeypatch.setattr(vendor, "get_local_pro", lambda: FakePro())
    bars = vendor.load_index_daily("000300.SH", date(2024, 1, 2), date(2024, 1, 2))

    assert bars.select("instrument", "up_limit", "down_limit").to_dicts() == [
        {"instrument": "000300.SH", "up_limit": None, "down_limit": None}
    ]
    assert bars.schema["up_limit"] == pl.Float64
    assert bars.schema["down_limit"] == pl.Float64
