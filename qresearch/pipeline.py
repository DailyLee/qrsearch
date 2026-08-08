"""Market-only orchestration entry points for strategy research."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from qresearch.config import get_settings
from qresearch.engines.data.panel import load_price_panel
from qresearch.engines.experiment.optimize import run_signal_threshold_search
from qresearch.engines.experiment.sensitivity import (
    parse_sensitivity_args,
    parse_sensitivity_extended,
    run_sensitivity_grid,
)
from qresearch.engines.experiment.sweep import run_signal_sweep
from qresearch.engines.signal.engine import build_ranked
from qresearch.research.pipeline import (
    _calendar_for,
    load_frozen_strategy_run,
    run_research_strategy,
)
from qresearch.research.strategy import build_market_signal_frame


def pipeline_research(
    config_path: str | Path, *, run_id: str, role: str, n_trials_assumed: int | None = None
) -> dict[str, object]:
    return run_research_strategy(
        config_path, run_id=run_id, role=role, n_trials_assumed=n_trials_assumed
    )


def _frozen_strategy_inputs(
    config_path: str | Path, *, run_id: str, role: str = "train"
) -> tuple[object, object, object, Path]:
    if role != "train":
        raise ValueError("pipeline search commands only permit role=train")
    config, _, dataset, _, run_dir = load_frozen_strategy_run(
        config_path, run_id=run_id, role=role
    )
    frame = build_market_signal_frame(dataset, config, _calendar_for(config))
    panel = load_price_panel(frame, config, cache_dir=Path(get_settings().cache_dir) / "prices")
    return config, frame, panel, run_dir


def pipeline_optimize(config_path: str | Path, *, run_id: str, role: str = "train", feature: str | None = None, side: str = "auto", keep_frac: str = "0.1,0.2,0.3,0.4", n_trials: int | None = None) -> dict[str, object]:
    config, frame, panel, run_dir = _frozen_strategy_inputs(config_path, run_id=run_id, role=role)
    result = run_signal_threshold_search(frame, panel, config, feature=feature, side=side, keep_fracs=keep_frac, max_grid=n_trials)
    return _persist_grid(run_dir, "optimize", result)


def pipeline_sweep(config_path: str | Path, *, run_id: str, role: str = "train", set_specs: list[str], metric: str = "sharpe", max_grid: int = 64) -> dict[str, object]:
    config, frame, panel, run_dir = _frozen_strategy_inputs(config_path, run_id=run_id, role=role)
    return _persist_grid(run_dir, "sweep", run_signal_sweep(frame, panel, config, set_specs=set_specs, metric=metric, max_grid=max_grid))


def pipeline_sensitivity(config_path: str | Path, *, run_id: str, role: str = "train", cost_mult: str = "1,1.5,2", stop: str = "-0.05,-0.086,-0.12", take: str = "0.10,0.158,0.20", max_hold: str | None = None, max_weight: str | None = None, max_new: str | None = None, sizing_base: str | None = None, max_names_per_industry: str | None = None, max_new_per_industry: str | None = None, max_grid: int = 64) -> dict[str, object]:
    config, frame, panel, run_dir = _frozen_strategy_inputs(config_path, run_id=run_id, role=role)
    costs, stops, takes = parse_sensitivity_args(cost_mult, stop, take)
    extra = parse_sensitivity_extended(max_hold=max_hold, max_weight=max_weight, max_new=max_new, sizing_base=sizing_base, max_names_per_industry=max_names_per_industry, max_new_per_industry=max_new_per_industry)
    return _persist_grid(run_dir, "sensitivity", run_sensitivity_grid(frame, panel, config, cost_mult=costs, stops=stops, takes=takes, max_grid=max_grid, **extra))


def _persist_grid(run_dir: Path, name: str, result: dict[str, Any]) -> dict[str, object]:
    from qresearch.research.pipeline import _write_json

    path = run_dir / "artifacts" / f"{name}_summary.json"
    _write_json(path, result)
    return {"run_id": run_dir.name, "summary": result, "artifacts": {f"{name}_summary": str(path.resolve())}}
