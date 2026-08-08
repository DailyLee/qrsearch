from __future__ import annotations

import hashlib
from datetime import date

import polars as pl
import pytest

from qresearch.config.models import ResearchConfig, RiskConfig, WalkForwardConfig
from qresearch.engines.analysis.metrics import absolute_metrics
from qresearch.engines.data import vendor
from qresearch.engines.data.panel import derive_panel_range, load_price_panel
from qresearch.engines.experiment.walkforward import build_folds, year_bounds
from qresearch.io.envelope import ExitCode, fail_envelope


def test_panel_prior_close_and_next_session(panel, sessions):
    s1, s2 = sessions[1], sessions[2]
    bar1 = panel.get("AAA001.SZ", s1)
    assert bar1 is not None
    assert panel.prior_close("AAA001.SZ", s2) == float(bar1["close"])
    assert panel.prior_close("AAA001.SZ", sessions[0]) is None
    assert panel.next_session(s1, 1) == s2
    assert panel.next_session(sessions[-1], 1) is None
    assert panel.next_session(sessions[0], 0) == sessions[0]


def test_derive_panel_range_extends_buffers():
    events = pl.DataFrame(
        {
            "entry_intent_date": [date(2024, 1, 10), date(2024, 2, 1)],
            "exit_intent_date": [date(2024, 1, 20), date(2024, 2, 15)],
            "instrument": ["A", "B"],
        }
    )
    cfg = ResearchConfig(risk=RiskConfig(max_hold_sessions=10))
    start, end = derive_panel_range(events, cfg)
    assert start < date(2024, 1, 10)
    assert end >= date(2024, 2, 15)


def test_load_price_panel_ignores_prior_limit_schema_cache(monkeypatch, tmp_path):
    """Catches reusing a v1 cache that cannot carry historical price limits."""
    events = pl.DataFrame(
        {
            "entry_intent_date": [date(2024, 1, 10)],
            "exit_intent_date": [date(2024, 1, 12)],
            "instrument": ["000001.SZ"],
        }
    )
    config = ResearchConfig(adjustment={"mode": "none"}, benchmark={"instrument": ""})
    start, end = derive_panel_range(events, config)
    universe = hashlib.sha1(b"000001.SZ").hexdigest()[:12]
    stale_path = tmp_path / f"pit_raw_v1_none_{start.isoformat()}_{end.isoformat()}_{universe}.parquet"
    stale_bars = pl.DataFrame(
        {
            "instrument": ["000001.SZ"],
            "trade_date": [date(2024, 1, 10)],
            "open": [10.0],
            "high": [10.2],
            "low": [9.8],
            "close": [10.1],
            "vol": [100.0],
            "amount": [1_000.0],
            "adj_factor": [1.0],
        }
    )
    stale_bars.write_parquet(stale_path)
    fresh_bars = stale_bars.with_columns(
        pl.lit(11.0).alias("up_limit"), pl.lit(9.0).alias("down_limit")
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        vendor,
        "load_daily_long",
        lambda instruments, *_args, **_kwargs: (calls.append(instruments) or (fresh_bars, "fresh")),
    )
    monkeypatch.setattr(vendor, "load_trade_calendar", lambda *_args: [date(2024, 1, 10)])

    panel = load_price_panel(events, config, cache_dir=tmp_path)

    assert calls == [["000001.SZ"]]
    assert panel.get("000001.SZ", date(2024, 1, 10))["up_limit"] == 11.0


def test_build_folds_rolling_and_year_bounds():
    ev = pl.DataFrame(
        {
            "entry_intent_date": [
                date(2019, 1, 1),
                date(2020, 1, 1),
                date(2021, 1, 1),
                date(2022, 1, 1),
            ],
            "instrument": ["a", "b", "c", "d"],
        }
    )
    assert year_bounds(ev) == [2019, 2020, 2021, 2022]
    folds = build_folds(ev, WalkForwardConfig(mode="rolling", rolling_is_years=2))
    assert len(folds) == 2
    assert folds[0]["is_years"] == [2019, 2020]
    assert folds[0]["oos_years"] == [2021]
    assert folds[1]["is_years"] == [2020, 2021]
    assert folds[1]["oos_years"] == [2022]


def test_absolute_metrics_empty_and_growth():
    empty = absolute_metrics([], [], starting_cash=100_000.0)
    assert empty["n_trades"] == 0
    assert empty["end_nav"] == 100_000.0
    assert empty["total_return"] == 0.0

    equity = [
        {"session": "2024-01-02", "nav": 100_000.0},
        {"session": "2024-01-03", "nav": 101_000.0},
        {"session": "2024-01-04", "nav": 102_000.0},
    ]
    trades = [{"side": "buy"}, {"side": "sell"}, {"side": "buy"}]
    m = absolute_metrics(equity, trades, starting_cash=100_000.0)
    assert m["n_trades"] == 2
    assert m["total_return"] == pytest.approx(0.02)
    assert m["max_dd"] <= 0.0
    assert m["sharpe"] != 0.0


def test_fail_envelope_blocked_status():
    env, code = fail_envelope(
        "pipeline.research",
        "t0",
        code="gate_blocked",
        message="oos folds insufficient",
        exit_code=ExitCode.BLOCKED,
        run_id="r1",
    )
    assert code == ExitCode.BLOCKED
    assert env.ok is False
    assert env.status == "blocked"
    assert env.error is not None
    assert env.error.code == "gate_blocked"
    assert env.run_id == "r1"
