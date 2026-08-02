from __future__ import annotations

import json
from pathlib import Path

from qresearch.config.models import ResearchConfig
from qresearch.engines.analysis.report import (
    build_conclusion,
    build_drawdown_chart,
    build_equity_chart,
    build_yearly_chart,
    enrich_conclusion,
    evaluate_gates,
    render_html,
    summarize_trades,
    write_report,
)


def test_summarize_trades_and_chinese_html(tmp_path: Path):
    trades = [
        {
            "session": "2025-01-06",
            "instrument": "000001.SZ",
            "side": "buy",
            "qty": 100,
            "price": 10.0,
            "fee": 5.0,
            "reason": "entry",
            "pnl": None,
        },
        {
            "session": "2025-01-10",
            "instrument": "000001.SZ",
            "side": "sell",
            "qty": 100,
            "price": 11.0,
            "fee": 6.0,
            "reason": "take_profit",
            "pnl": 90.0,
        },
        {
            "session": "2025-01-12",
            "instrument": "000002.SZ",
            "side": "sell",
            "qty": 200,
            "price": 9.0,
            "fee": 6.0,
            "reason": "stop",
            "pnl": -40.0,
        },
    ]
    stats = summarize_trades(trades)
    assert stats is not None
    assert stats["n_sells"] == 2
    assert 0 < stats["win_rate"] < 1

    cfg = ResearchConfig()
    gates = evaluate_gates(
        {"n_trades": 20, "sharpe": 1.0, "max_dd": -0.1}, cfg.gates, n_oos_folds=2
    )
    assert gates["promotable"] is True
    conclusion = build_conclusion(
        run_id="demo_run",
        config=cfg,
        metrics={
            "n_trades": 2,
            "n_sessions": 10,
            "total_return": 0.05,
            "ann_return": 0.12,
            "sharpe": 1.0,
            "max_dd": -0.1,
            "end_nav": 105000.0,
        },
        n_events=3,
        adjustment_as_of="20251231",
        gates_result=gates,
        ic_rows=[
            {"feature": "features.box_quality", "horizon": 5, "n": 100, "rank_ic": 0.05},
            {"feature": "features.pct_b", "horizon": 5, "n": 100, "rank_ic": -0.02},
        ],
    )
    equity = [
        {"session": "2025-01-01", "cash": 100000, "nav": 100000, "n_positions": 0},
        {"session": "2025-01-05", "cash": 95000, "nav": 102000, "n_positions": 1},
        {"session": "2025-01-10", "cash": 90000, "nav": 105000, "n_positions": 1},
        {"session": "2025-01-15", "cash": 88000, "nav": 103500, "n_positions": 1},
    ]
    conclusion["metrics"]["yearly"] = [
        {"year": 2024, "ann_return": 0.12, "ann_excess": 0.03},
        {"year": 2025, "ann_return": -0.05, "ann_excess": -0.02},
    ]
    enriched = enrich_conclusion(
        conclusion,
        trades=trades,
        equity=equity,
        rejects=[{"session": "2025-01-02", "instrument": "000003.SZ", "reason": "limit_up"}],
        artifact_links={"trades": "artifacts/trades.csv"},
        instrument_names={"000001.SZ": "平安银行", "000002.SZ": "万科A"},
    )
    assert enriched["sample_trades"][-1].get("name") in ("平安银行", "万科A")
    assert enriched.get("equity_chart") and enriched["equity_chart"]["type"] == "line"
    assert enriched.get("drawdown_chart")
    assert enriched.get("yearly_chart") and enriched["yearly_chart"]["type"] == "bar"
    html = render_html(enriched)
    assert "量化研究报告" in html
    assert "因子分析" in html
    assert "策略详情" in html
    assert "回测绩效" in html
    assert "交易统计" in html
    assert "止盈" in html
    assert "平安银行" in html or "万科A" in html
    assert "<th>名称</th>" in html
    assert 'id="chart-data-equity"' in html
    assert "chart-host" in html
    assert "qr-charts" not in html  # inline IIFE, no external lib
    assert "mousemove" in html

    html_path, json_path = write_report(tmp_path / "report", conclusion)
    assert html_path.exists()
    assert json_path.exists()
    assert (tmp_path / "report" / "research_report_zh.html").exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["locale"] == "zh-CN"
    assert "equity_svg" not in payload
    assert "equity_chart" not in payload

    # Pre-enriched conclusion keeps charts through write_report; heavy keys stay out of JSON.
    html_full, json_full = write_report(tmp_path / "report_full", enriched)
    full_payload = json.loads(json_full.read_text(encoding="utf-8"))
    assert "equity_chart" not in full_payload
    assert "chart-host" in html_full.read_text(encoding="utf-8")


def test_chart_payload_builders():
    equity = [
        {"session": f"2024-01-{d:02d}", "nav": 100000 + d * 100}
        for d in range(1, 12)
    ]
    eq = build_equity_chart(equity)
    assert eq is not None
    assert len(eq["labels"]) == 11
    assert eq["y_format"] == "nav"
    dd = build_drawdown_chart(equity)
    assert dd is not None
    assert dd["y_format"] == "pct"
    y = build_yearly_chart(
        [{"year": 2023, "ann_return": 0.1, "ann_excess": None}, {"year": 2024, "ann_return": 0.2, "ann_excess": 0.05}]
    )
    assert y is not None
    assert len(y["series"]) == 2
