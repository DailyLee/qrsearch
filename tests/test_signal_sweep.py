from __future__ import annotations

import pytest

from qresearch.config.models import FilterRule, ResearchConfig, SignalsConfig
from qresearch.engines.experiment.sweep import SweepError, parse_set_spec, run_signal_sweep


def test_parse_set_spec_value_and_op():
    d = parse_set_spec("signals.filters[field=features.pre_r1].value=-1.5,-1.0")
    assert d["field"] == "features.pre_r1"
    assert d["attr"] == "value"
    assert d["values"] == [-1.5, -1.0]
    o = parse_set_spec("signals.filters[field=features.pre_r1].op=le,lt")
    assert o["values"] == ["le", "lt"]
    with pytest.raises(SweepError):
        parse_set_spec("portfolio.max_weight=0.2,0.3")


def test_sweep_grid_and_max_grid(panel, events, base_config):
    cfg = base_config.model_copy(deep=True)
    cfg.signals = SignalsConfig(
        filters=[
            FilterRule(field="features.box_quality", op="ge", value=0.9),
            FilterRule(field="features.bandwidth_percent", op="le", value=25.0),
        ]
    )
    # enrich events already have both features
    out = run_signal_sweep(
        events,
        panel,
        cfg,
        set_specs=[
            "signals.filters[field=features.box_quality].value=0.9,0.95",
            "signals.filters[field=features.bandwidth_percent].value=15,25",
        ],
        max_grid=64,
    )
    assert out["n_grid"] == 4
    assert out["best_params"]["patches"]
    assert out["best_params"]["source"] == "pipeline.sweep"

    truncated = run_signal_sweep(
        events,
        panel,
        cfg,
        set_specs=[
            "signals.filters[field=features.box_quality].value=0.9,0.95",
            "signals.filters[field=features.bandwidth_percent].value=15,25",
        ],
        max_grid=2,
    )
    assert truncated["n_grid"] == 2
    assert truncated["truncated"] is True


def test_sweep_missing_field_errors(panel, events, base_config):
    cfg = base_config.model_copy(deep=True)
    cfg.signals = SignalsConfig(
        filters=[FilterRule(field="features.box_quality", op="ge", value=0.9)]
    )
    with pytest.raises(SweepError, match="not in config"):
        run_signal_sweep(
            events,
            panel,
            cfg,
            set_specs=["signals.filters[field=features.pre_r1].value=-1,-0.5"],
        )
