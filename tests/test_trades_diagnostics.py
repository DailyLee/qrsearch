from __future__ import annotations

import json
from pathlib import Path

import pytest

from qresearch.engines.analysis.invested import mean_invested_from_equity
from qresearch.engines.analysis.trades_diagnostics import (
    analyze_trades_run,
    exit_reason_pnl_groups,
)


def test_mean_invested_definition():
    equity = [
        {"session": "2024-01-02", "cash": 50.0, "nav": 100.0},
        {"session": "2024-01-03", "cash": 100.0, "nav": 100.0},
        {"session": "2024-01-04", "cash": 0.0, "nav": 100.0},
    ]
    out = mean_invested_from_equity(equity)
    assert out["mean_invested"] == pytest.approx((0.5 + 0.0 + 1.0) / 3)
    assert out["empty_cash_share"] == pytest.approx(1 / 3)


def test_exit_reason_groups():
    trades = [
        {"side": "sell", "reason": "stop", "pnl": -1.0},
        {"side": "sell", "reason": "stop", "pnl": 2.0},
        {"side": "sell", "reason": "take_profit", "pnl": 3.0},
        {"side": "buy", "reason": "entry", "pnl": None},
    ]
    g = {r["reason"]: r for r in exit_reason_pnl_groups(trades)}
    assert g["stop"]["n"] == 2
    assert g["stop"]["win_rate"] == 0.5
    assert g["take_profit"]["n"] == 1


def test_analyze_trades_run_writes_artifact(tmp_path: Path):
    art = tmp_path / "artifacts"
    art.mkdir()
    (art / "equity.csv").write_text(
        "session,cash,nav,n_positions\n2024-01-02,40,100,1\n2024-01-03,100,100,0\n",
        encoding="utf-8",
    )
    (art / "trades.csv").write_text(
        "side,reason,pnl,fee\nbuy,entry,,1\nsell,stop,-2,1\nsell,take_profit,4,1\n",
        encoding="utf-8",
    )
    (art / "rejects_summary.json").write_text(
        json.dumps([{"reason": "limit_up"}, {"reason": "limit_up"}, {"reason": "cash"}]),
        encoding="utf-8",
    )
    (art / "yearly_metrics.json").write_text(
        json.dumps({"2024": {"sharpe": 0.5, "ann_return": 0.1}}),
        encoding="utf-8",
    )
    out = analyze_trades_run(tmp_path)
    assert 0.0 <= out["summary"]["mean_invested"] <= 1.0
    assert out["summary"]["reject_top"][0]["reason"] == "limit_up"
    assert (tmp_path / "artifacts" / "trades_diagnostics.json").exists()
