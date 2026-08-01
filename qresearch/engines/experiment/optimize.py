"""Optuna optimization over signal thresholds (shared panel)."""

from __future__ import annotations

from typing import Any

import optuna
import polars as pl

from qresearch.config.models import FilterRule, ResearchConfig
from qresearch.engines.data.panel import PricePanel
from qresearch.engines.experiment.walkforward import run_walk_forward
from qresearch.engines.signal.engine import build_ranked
from qresearch.engines.backtest.session import run_backtest


def run_optuna(
    events: pl.DataFrame,
    panel: PricePanel,
    base_config: ResearchConfig,
    *,
    n_trials: int = 20,
    feature: str = "features.box_quality",
    study_name: str = "qresearch",
) -> dict[str, Any]:
    trials_log: list[dict] = []

    def objective(trial: optuna.Trial) -> float:
        cfg = base_config.model_copy(deep=True)
        lo = trial.suggest_float("min_feat", 0.90, 0.99)
        hi = trial.suggest_float("max_feat", lo, 1.0)
        cfg.signals.filters = [
            FilterRule(field=feature, op="between", value=lo, value_max=hi)
        ]
        # use full sample backtest for speed when <2 years; else WF aggregate
        years = {str(d)[:4] for d in events["entry_intent_date"].to_list()}
        if len(years) >= 2:
            wf = run_walk_forward(events, panel, cfg)
            score = float(wf["aggregate"]["sharpe"])
            n_trades = int(wf["aggregate"].get("total_trades") or 0)
        else:
            ranked = build_ranked(events, cfg)
            res = run_backtest(ranked, panel, cfg)
            score = float(res.metrics.get("sharpe") or 0.0)
            n_trades = int(res.metrics.get("n_trades") or 0)
        if n_trades < cfg.walk_forward.min_trades:
            score = -1e6
        trials_log.append(
            {"number": trial.number, "params": trial.params, "value": score, "n_trades": n_trades}
        )
        return score

    study = optuna.create_study(direction="maximize", study_name=study_name)
    study.optimize(objective, n_trials=n_trials, n_jobs=1)
    return {
        "best_params": study.best_params,
        "best_value": study.best_value,
        "trials": trials_log,
    }
