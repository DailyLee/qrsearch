from __future__ import annotations

from qresearch.config.models import GatesConfig
from qresearch.engines.analysis.overfit import attach_overfit_metrics, deflated_sharpe
from qresearch.engines.analysis.report import evaluate_gates


def test_deflated_sharpe_n_trials_one_near_observed():
    info = deflated_sharpe(1.5, n_obs=250, n_trials=1)
    assert abs(info["deflated_sharpe"] - 1.5) < 1e-9
    assert info["sr_null_max"] == 0.0
    assert 0.0 < info["dsr_prob"] <= 1.0


def test_deflated_sharpe_penalizes_many_trials():
    one = deflated_sharpe(1.0, n_obs=250, n_trials=1)
    many = deflated_sharpe(1.0, n_obs=250, n_trials=50)
    assert many["deflated_sharpe"] < one["deflated_sharpe"]
    assert many["sr_null_max"] > 0


def test_gates_deflated_sharpe():
    metrics = attach_overfit_metrics(
        {"sharpe": 0.2, "n_return_obs": 100, "n_trades": 20, "max_dd": -0.05},
        n_trials=30,
    )
    gates = GatesConfig(min_oos_folds=0, min_trades=10, min_deflated_sharpe=0.5)
    res = evaluate_gates(metrics, gates, n_oos_folds=2)
    assert res["structural_passed"] is True
    assert res["passed"] is True  # structural ok
    assert res["promotable"] is False
    assert "deflated_sharpe_below_min" in res["economic_reasons"]

    gates2 = GatesConfig(min_oos_folds=0, min_trades=10, min_deflated_sharpe=None)
    res2 = evaluate_gates(metrics, gates2, n_oos_folds=2)
    assert res2["passed"] is True
    assert res2["promotable"] is True
