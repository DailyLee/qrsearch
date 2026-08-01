from __future__ import annotations

from qresearch.config.models import CostsConfig, ResearchConfig, RiskConfig
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
