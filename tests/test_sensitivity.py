from __future__ import annotations

from qresearch.config.models import CostsConfig, RiskConfig
from qresearch.engines.experiment.sensitivity import parse_sensitivity_args, run_sensitivity_grid


def test_parse_sensitivity_args():
    cms, stops, takes = parse_sensitivity_args("1,2", "-0.05,none", "0.1,0.2")
    assert cms == [1.0, 2.0]
    assert stops == [-0.05, None]
    assert takes == [0.1, 0.2]


def test_sensitivity_grid_size_and_cost_hurt(panel, events, base_config):
    cfg = base_config.model_copy(deep=True)
    cfg.costs = CostsConfig(
        commission_rate=0.001, commission_min=0.0, stamp_duty_rate=0.001, slippage_bps=50.0
    )
    cfg.risk = RiskConfig(stop_loss=-0.5, take_profit=0.5)
    out = run_sensitivity_grid(
        events,
        panel,
        cfg,
        cost_mult=[1.0, 3.0],
        stops=[-0.5],
        takes=[0.5],
        max_grid=10,
    )
    assert out["n_grid"] == 2
    by_cm = {r["cost_mult"]: r for r in out["rows"]}
    assert by_cm[3.0]["total_return"] <= by_cm[1.0]["total_return"] + 1e-9


def test_sensitivity_max_new_and_mean_invested(panel, events, base_config, sessions):
    import polars as pl

    cfg = base_config.model_copy(deep=True)
    cfg.risk = RiskConfig(stop_loss=-0.5, take_profit=0.5)
    # two names same entry day so max_new=1 binds
    day = sessions[1]
    ev = pl.DataFrame(
        {
            "instrument": ["AAA001.SZ", "AAA002.SZ"],
            "decision_date": [day, day],
            "entry_intent_date": [day, day],
            "exit_intent_date": [sessions[5], sessions[6]],
            "features.box_quality": [0.97, 0.96],
            "features.bandwidth_percent": [20.0, 15.0],
            "rank_score": [0.0, 1.0],
            "source_file": ["t", "t"],
        }
    )
    out = run_sensitivity_grid(
        ev,
        panel,
        cfg,
        cost_mult=[1.0],
        stops=[-0.5],
        takes=[0.5],
        max_new=[1, 2],
        max_grid=10,
    )
    assert out["n_grid"] == 2
    by_mn = {r["max_new_entries_per_day"]: r for r in out["rows"]}
    # same-day cap may defer via GTD; mean_invested still rises when more fill same day
    assert float(by_mn[1]["mean_invested"] or 0) < float(by_mn[2]["mean_invested"] or 0)
    for r in out["rows"]:
        mi = r["mean_invested"]
        assert mi is None or (0.0 <= float(mi) <= 1.0)
    assert out["best_params"] and out["best_params"]["patches"]
    paths = {p["path"] for p in out["best_params"]["patches"]}
    assert "portfolio.max_new_entries_per_day" in paths
