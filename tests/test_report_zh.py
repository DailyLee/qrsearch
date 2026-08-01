from __future__ import annotations

import json
from pathlib import Path

from qresearch.config.models import ResearchConfig
from qresearch.engines.analysis.report import (
    build_conclusion,
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
    gates = evaluate_gates({"n_trades": 2, "sharpe": 1.0, "max_dd": -0.1}, cfg.gates, n_oos_folds=0)
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
        {"session": "2025-01-10", "cash": 90000, "nav": 105000, "n_positions": 1},
    ]
    enriched = enrich_conclusion(
        conclusion,
        trades=trades,
        equity=equity,
        rejects=[{"session": "2025-01-02", "instrument": "000003.SZ", "reason": "limit_up"}],
        artifact_links={"trades": "artifacts/trades.csv"},
    )
    html = render_html(enriched)
    assert "量化研究报告" in html
    assert "因子分析" in html
    assert "策略详情" in html
    assert "回测绩效" in html
    assert "交易统计" in html
    assert "止盈" in html

    html_path, json_path = write_report(tmp_path / "report", conclusion)
    assert html_path.exists()
    assert json_path.exists()
    assert (tmp_path / "report" / "research_report_zh.html").exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["locale"] == "zh-CN"
    assert "equity_svg" not in payload
