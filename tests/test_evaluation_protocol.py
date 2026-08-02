from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from qresearch.config.models import (
    AppSettings,
    EvaluationConfig,
    GatesConfig,
    HoldoutWindow,
    ResearchConfig,
)
from qresearch.engines.analysis.evaluation_check import check_evaluation_years
from qresearch.engines.analysis.invested import mean_invested_from_equity
from qresearch.engines.analysis.report import (
    build_conclusion,
    evaluate_gates,
    render_html,
    write_report_from_run,
)
from qresearch.engines.analysis.split_comparison import (
    build_split_comparison,
    latest_run_ids_from_decisions,
)
from qresearch.engines.experiment.best_params import apply_best_to_yaml
from qresearch.pipeline import pipeline_research


def test_mean_invested_in_metrics_shape():
    equity = [
        {"cash": 40.0, "nav": 100.0},
        {"cash": 100.0, "nav": 100.0},
    ]
    inv = mean_invested_from_equity(equity)
    assert inv["mean_invested"] == pytest.approx(0.3)
    assert 0.0 <= inv["empty_cash_share"] <= 1.0


def test_gates_primary_absolute_vs_excess():
    metrics = {
        "sharpe": -0.2,
        "max_dd": -0.1,
        "n_trades": 50,
        "n_trials": 1,
        "ann_excess": 0.05,
        "information_ratio": 0.8,
    }
    g_abs = GatesConfig(
        min_oos_folds=0,
        min_trades=10,
        min_oos_sharpe=0.0,
        max_oos_drawdown=0.35,
        primary_metric="absolute",
    )
    r1 = evaluate_gates(metrics, g_abs, n_oos_folds=2)
    assert r1["economic_passed"] is False
    assert r1["absolute_ok"] is False
    assert r1["primary_metric"] == "absolute"

    g_ex = GatesConfig(
        min_oos_folds=0,
        min_trades=10,
        min_oos_sharpe=0.0,
        max_oos_drawdown=0.35,
        primary_metric="excess",
        min_information_ratio=0.0,
        min_ann_excess=0.0,
    )
    r2 = evaluate_gates(metrics, g_ex, n_oos_folds=2)
    assert r2["economic_passed"] is True
    assert r2["excess_ok"] is True
    assert "sharpe_below_min" in r2["disclosed_absolute"]
    assert r2["absolute_ok"] is False

    g_ex_fail = GatesConfig(
        min_oos_folds=0,
        min_trades=10,
        primary_metric="excess",
        min_information_ratio=1.5,
        max_oos_drawdown=0.35,
    )
    r3 = evaluate_gates(metrics, g_ex_fail, n_oos_folds=2)
    assert r3["economic_passed"] is False
    assert "information_ratio_below_min" in r3["economic_reasons"]


def test_gates_absolute_plus_optional_ir_extra():
    """primary=absolute still applies optional IR/ann_excess when configured."""
    metrics = {
        "sharpe": 1.0,
        "max_dd": -0.1,
        "n_trades": 50,
        "n_trials": 1,
        "ann_excess": -0.01,
        "information_ratio": -0.5,
    }
    g = GatesConfig(
        min_oos_folds=0,
        min_trades=10,
        min_oos_sharpe=0.0,
        max_oos_drawdown=0.35,
        primary_metric="absolute",
        min_information_ratio=0.0,
        min_ann_excess=0.0,
    )
    res = evaluate_gates(metrics, g, n_oos_folds=2)
    assert res["absolute_ok"] is True
    assert res["excess_ok"] is False
    assert res["economic_passed"] is False
    assert "information_ratio_below_min" in res["economic_reasons"]
    assert "ann_excess_below_min" in res["economic_reasons"]


def test_evaluation_syncs_primary_metric():
    cfg = ResearchConfig(
        evaluation=EvaluationConfig(primary_metric="excess"),
        gates=GatesConfig(primary_metric="absolute"),
    )
    assert cfg.gates.primary_metric == "excess"


def test_evaluation_years_warn():
    cfg = ResearchConfig(
        evaluation=EvaluationConfig(
            train_years=["2019", "2020"],
            holdouts=[HoldoutWindow(years=["2025"], role="stress")],
        )
    )
    profile = {"years": {"2019": 1, "2021": 1}}
    out = check_evaluation_years(cfg, profile)
    assert out["status"] == "warn"
    assert "2021" in out["unexpected_years"]


def test_split_roles_validate_and_stress(tmp_path: Path):
    runs = tmp_path / "runs"
    for rid, years in [
        ("t1", {"2019": 1}),
        ("v1", {"2024": 1}),
        ("h1", {"2024": 1}),
        ("s1", {"2025": 1}),
        ("f1", {"2019": 1, "2025": 1}),
    ]:
        root = runs / rid
        (root / "artifacts").mkdir(parents=True)
        (root / "report").mkdir(parents=True)
        (root / "meta.json").write_text(json.dumps({"run_id": rid}), encoding="utf-8")
        (root / "artifacts" / "metrics.json").write_text(
            json.dumps(
                {
                    "sharpe": 0.1,
                    "total_return": 0.1,
                    "ann_return": 0.1,
                    "max_dd": -0.05,
                    "n_trades": 5,
                    "mean_invested": 0.2,
                    "ann_excess": 0.01,
                    "information_ratio": 0.3,
                }
            ),
            encoding="utf-8",
        )
        (root / "artifacts" / "sample_profile.json").write_text(
            json.dumps({"years": years}), encoding="utf-8"
        )

    decisions = [
        {"stage": "backtest_train", "run_id": "t1", "created_at": "2026-01-01"},
        {"stage": "backtest_validate", "run_id": "v1", "created_at": "2026-01-02"},
        {"stage": "holdout", "run_id": "h1", "created_at": "2026-01-03"},
        {"stage": "holdout_stress", "run_id": "s1", "created_at": "2026-01-04"},
        {"stage": "full_sample", "run_id": "f1", "created_at": "2026-01-05"},
    ]
    got = latest_run_ids_from_decisions(decisions)
    assert got["validate"] == "v1"
    assert got["holdout_stress"] == "s1"
    table = build_split_comparison(runs_dir=runs, decisions=decisions)
    assert table is not None
    roles = [r["role"] for r in table["rows"] if not r["missing"]]
    assert roles == ["train", "validate", "holdout", "holdout_stress", "full"]
    assert table["rows"][0]["metrics"]["mean_invested"] == 0.2


def test_apply_best_yaml_no_bom(tmp_path: Path):
    run = tmp_path / "run1"
    art = run / "artifacts"
    art.mkdir(parents=True)
    snap = {"signals": {"filters": [{"field": "features.a", "op": "ge", "value": 0.0}]}}
    (run / "config.snapshot.yaml").write_text(yaml.safe_dump(snap), encoding="utf-8")
    (art / "sweep_summary.json").write_text(
        json.dumps(
            {
                "best_params": {
                    "patches": [
                        {
                            "path": "signals.filters",
                            "match": {"field": "features.a"},
                            "set": {"value": 1.0},
                        }
                    ],
                    "source": "pipeline.sweep",
                }
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out.yaml"
    apply_best_to_yaml(run_dir=run, out_path=out, examples_dir=tmp_path / "examples")
    raw = out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert loaded["signals"]["filters"][0]["value"] == 1.0


def test_pipeline_research_writes_invested_and_eval_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, panel, events, base_config
):
    runs = tmp_path / "runs"
    cache = tmp_path / "cache"
    cfg = base_config.model_copy(deep=True)
    cfg.gates = GatesConfig(min_oos_folds=0, min_trades=1, min_oos_sharpe=None)
    cfg.evaluation = EvaluationConfig(
        train_years=["2024"],
        holdouts=[HoldoutWindow(years=["2025"], role="stress", label="bull_stress")],
    )
    cfg.benchmark.instrument = ""

    monkeypatch.setattr(
        "qresearch.pipeline.get_settings",
        lambda: AppSettings(runs_dir=runs, cache_dir=cache, studies_dir=tmp_path / "studies"),
    )
    monkeypatch.setattr(
        "qresearch.pipeline.load_research_config",
        lambda _path=None, overrides=None: cfg,
    )
    monkeypatch.setattr("qresearch.pipeline.load_events", lambda *_a, **_k: events)
    monkeypatch.setattr("qresearch.pipeline.load_price_panel", lambda *_a, **_k: panel)

    result = pipeline_research(
        "dummy.csv",
        run_id="eval_research_demo",
        do_ic=False,
        do_wf=False,
    )
    assert result["run_id"] == "eval_research_demo"
    root = Path(result["artifacts"]["run_dir"])
    metrics_path = root / "artifacts" / "metrics.json"
    assert metrics_path.exists()
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics.get("mean_invested") is not None
    assert 0.0 <= float(metrics["mean_invested"]) <= 1.0
    assert "empty_cash_share" in metrics
    assert result["summary"].get("mean_invested") == metrics["mean_invested"]

    eval_path = root / "artifacts" / "evaluation_check.json"
    assert eval_path.exists()
    eval_check = json.loads(eval_path.read_text(encoding="utf-8"))
    # sample is 2024-only; evaluation declares 2025 holdout → warn or ok subset check
    assert eval_check["status"] in ("ok", "warn")
    assert result["summary"]["evaluation_check"]["status"] == eval_check["status"]
    assert "absolute_ok" in (result["summary"].get("gates") or {})
    assert "excess_ok" in (result["summary"].get("gates") or {})


def test_report_html_includes_invested_and_rebuild_from_equity(tmp_path: Path):
    cfg = ResearchConfig()
    gates = evaluate_gates(
        {
            "n_trades": 20,
            "sharpe": 1.0,
            "max_dd": -0.1,
            "ann_excess": 0.02,
            "information_ratio": 0.4,
            "mean_invested": 0.35,
            "empty_cash_share": 0.4,
        },
        cfg.gates,
        n_oos_folds=2,
    )
    conclusion = build_conclusion(
        run_id="inv_html",
        config=cfg,
        metrics={
            "n_trades": 2,
            "n_sessions": 10,
            "total_return": 0.05,
            "ann_return": 0.12,
            "sharpe": 1.0,
            "max_dd": -0.1,
            "end_nav": 105000.0,
            "mean_invested": 0.35,
            "empty_cash_share": 0.4,
            "invested_definition": "1 - cash/nav",
            "ann_excess": 0.02,
            "information_ratio": 0.4,
            "benchmark_available": True,
            "excess_return": 0.01,
        },
        n_events=3,
        adjustment_as_of="20251231",
        gates_result=gates,
    )
    html = render_html(conclusion)
    assert "mean_invested" in html or "平均仓位" in html
    assert "空仓天数占比" in html
    assert "信息比率" in html or "IR" in html

    # rebuild path: metrics without invested → filled from equity.csv
    run = tmp_path / "run_rebuild"
    art = run / "artifacts"
    report = run / "report"
    art.mkdir(parents=True)
    report.mkdir(parents=True)
    (run / "meta.json").write_text(
        json.dumps({"run_id": "run_rebuild", "gates": gates}), encoding="utf-8"
    )
    (run / "config.snapshot.yaml").write_text(yaml.safe_dump(cfg.model_dump()), encoding="utf-8")
    (art / "metrics.json").write_text(
        json.dumps({"sharpe": 1.0, "max_dd": -0.1, "n_trades": 5, "total_return": 0.1}),
        encoding="utf-8",
    )
    (art / "equity.csv").write_text(
        "session,cash,nav,n_positions\n2024-01-02,50,100,1\n2024-01-03,100,100,0\n",
        encoding="utf-8",
    )
    (report / "conclusion.json").write_text("{}", encoding="utf-8")
    html_path, json_path = write_report_from_run(run)
    assert html_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    mi = payload["metrics"]["mean_invested"]
    assert mi == pytest.approx(0.25)
    assert "平均仓位" in html_path.read_text(encoding="utf-8")
