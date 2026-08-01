from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from qresearch.config.models import ResearchConfig, WalkForwardConfig
from qresearch.engines.backtest.session import BacktestResult
from qresearch.engines.experiment.promote import promote_run
from qresearch.engines.experiment.registry import RunWriter, archive_run, list_runs, load_run_meta
from qresearch.engines.experiment.walkforward import run_walk_forward


def test_run_writer_list_and_archive(tmp_path: Path):
    runs = tmp_path / "runs"
    w = RunWriter(runs, run_id="run_demo")
    w.write_meta({"note": "x", "status": "ok"})
    w.write_config_snapshot(ResearchConfig())
    w.write_json("report/conclusion.json", {"promotable": False, "metrics": {"sharpe": 0.1}})

    rows = list_runs(runs)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run_demo"
    meta = load_run_meta(runs, "run_demo")
    assert meta["note"] == "x"

    zipped = archive_run(runs, "run_demo", tmp_path / "out" / "run_demo.zip")
    assert zipped.exists()
    assert zipped.suffix == ".zip"


def test_promote_run_requires_promotable_or_force(tmp_path: Path):
    runs = tmp_path / "runs"
    packages = tmp_path / "packages"
    run_id = "run_p"
    root = runs / run_id
    (root / "report").mkdir(parents=True)
    (root / "config.snapshot.yaml").write_text("portfolio:\n  starting_cash: 1\n", encoding="utf-8")
    conclusion = {
        "promotable": False,
        "metrics": {"sharpe": 0.2, "n_trades": 3},
        "gates": {"passed": False},
    }
    (root / "report" / "conclusion.json").write_text(
        json.dumps(conclusion), encoding="utf-8"
    )

    with pytest.raises(PermissionError):
        promote_run(runs, packages, run_id, "m1", "0.1.0", force=False)

    dest = promote_run(runs, packages, run_id, "m1", "0.1.0", force=True)
    assert dest.exists()
    prov = json.loads((dest / "provenance.json").read_text(encoding="utf-8"))
    assert prov["forced"] is True
    assert (dest / "spec.yaml").exists()
    assert (dest / "metrics_oos.json").exists()


def test_run_walk_forward_aggregate_with_stub(panel, sessions):
    events = pl.DataFrame(
        {
            "instrument": ["AAA001.SZ", "AAA001.SZ", "AAA002.SZ"],
            "decision_date": [date(2023, 1, 3), date(2024, 1, 3), date(2025, 1, 3)],
            "entry_intent_date": [date(2023, 1, 3), date(2024, 1, 3), date(2025, 1, 3)],
            "exit_intent_date": [date(2023, 1, 10), date(2024, 1, 10), date(2025, 1, 10)],
            "features.box_quality": [0.95, 0.96, 0.97],
            "rank_score": [0.0, 1.0, 2.0],
        }
    )
    # calendar years must exist in panel for BT; stub avoids panel date mismatch
    calls = {"n": 0}

    def stub_bt(ranked, _panel, _cfg):
        calls["n"] += 1
        n = max(ranked.height, 1)
        return BacktestResult(
            metrics={"sharpe": float(n), "n_trades": n, "n_return_obs": 10},
            trades=[],
            equity=[],
            rejects=[],
        )

    cfg = ResearchConfig(
        walk_forward=WalkForwardConfig(mode="expanding", objective="trade_weighted_sharpe")
    )
    out = run_walk_forward(events, panel, cfg, backtest_fn=stub_bt)
    assert calls["n"] >= 1
    assert out["aggregate"]["n_folds"] == len(out["folds"])
    assert "deflated_sharpe" in out["aggregate"]
    assert out["aggregate"]["total_trades"] >= 1

    cfg_mean = ResearchConfig(
        walk_forward=WalkForwardConfig(mode="expanding", objective="mean_sharpe", min_trades=1)
    )
    out2 = run_walk_forward(events, panel, cfg_mean, backtest_fn=stub_bt)
    assert out2["aggregate"]["n_folds"] >= 1
