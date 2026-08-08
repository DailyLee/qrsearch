from __future__ import annotations

from conftest import research_config

from datetime import date, timedelta

import polars as pl

from qresearch.config.models import BenchmarkConfig, ResearchConfig
from qresearch.engines.analysis.metrics import (
    compute_extended_metrics,
    relative_metrics,
    turnover_metrics,
    yearly_breakdown,
)
from qresearch.engines.data.panel import PricePanel


def _panel_with_bench() -> PricePanel:
    start = date(2025, 1, 2)
    sessions = [start + timedelta(days=i) for i in range(6)]
    # weekdays only simplification: use consecutive dates as sessions
    rows = []
    for i, s in enumerate(sessions):
        rows.append(
            {
                "instrument": "AAA001.SZ",
                "trade_date": s,
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0 + i * 0.1,
                "vol": 1_000_000.0,
                "amount": 10_000_000.0,
            }
        )
        # benchmark grows slower
        rows.append(
            {
                "instrument": "000852.SH",
                "trade_date": s,
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0 + i * 0.05,
                "vol": 1.0,
                "amount": 1.0,
            }
        )
    bars = pl.DataFrame(rows)
    panel = PricePanel(
        bars=bars,
        calendar=sessions,
        adjustment_as_of="20250107",
        data_fingerprint="test",
        start=sessions[0],
        end=sessions[-1],
        instruments=["000852.SH", "AAA001.SZ"],
    )
    panel.build_index()
    return panel


def test_turnover_and_relative_ir():
    panel = _panel_with_bench()
    sessions = panel.calendar
    # equity: +1% then flat-ish
    equity = []
    nav = 1_000_000.0
    for i, s in enumerate(sessions):
        if i > 0:
            nav *= 1.01
        equity.append({"session": str(s), "cash": nav, "nav": nav, "n_positions": 1})
    trades = [
        {
            "session": str(sessions[1]),
            "instrument": "AAA001.SZ",
            "side": "buy",
            "qty": 1000,
            "price": 10.0,
            "fee": 5.0,
            "reason": "entry",
            "pnl": None,
        },
        {
            "session": str(sessions[3]),
            "instrument": "AAA001.SZ",
            "side": "sell",
            "qty": 1000,
            "price": 10.5,
            "fee": 6.0,
            "reason": "exit_intent",
            "pnl": 400.0,
        },
    ]
    turn = turnover_metrics(equity, trades)
    assert turn["avg_daily_turnover"] > 0
    assert turn["ann_turnover"] == turn["avg_daily_turnover"] * 252

    rel = relative_metrics(equity, panel, "000852.SH")
    assert rel["benchmark_available"] is True
    assert rel["information_ratio"] is not None

    cfg = research_config(benchmark=BenchmarkConfig(instrument="000852.SH"))
    m = compute_extended_metrics(equity, trades, 1_000_000.0, panel=panel, config=cfg)
    assert m["sharpe"] != 0.0 or m["n_sessions"] > 0
    assert m["capacity"] in ("heuristic_adv", "unavailable")
    if m["capacity"] == "heuristic_adv":
        assert m["median_participation"] is not None
    assert m.get("benchmark_nav_series")
    assert len(m["benchmark_nav_series"]) == len(equity)
    y = yearly_breakdown(equity, trades, panel=panel, benchmark="000852.SH")
    assert y
    assert "ann_return" in y[0]
