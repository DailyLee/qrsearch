"""High-level research pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from qresearch.config import get_settings, load_research_config
from qresearch.engines.analysis.overfit import attach_overfit_metrics
from qresearch.engines.analysis.pit_audit import run_pit_audit
from qresearch.engines.analysis.report import (
    build_conclusion,
    evaluate_gates,
    write_report,
)
from qresearch.engines.backtest.session import run_backtest
from qresearch.engines.data.ingest import load_events
from qresearch.engines.data.panel import load_price_panel
from qresearch.engines.experiment.optimize import run_optuna
from qresearch.engines.experiment.registry import RunWriter
from qresearch.engines.experiment.walkforward import run_walk_forward
from qresearch.engines.factor.ic import compute_ic_table
from qresearch.engines.signal.engine import build_ranked


def pipeline_research(
    events_path: str | Path | list[str],
    config_path: str | Path | None = None,
    *,
    run_id: str | None = None,
    do_ic: bool = True,
    do_wf: bool = True,
    n_trials_assumed: int | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    config = load_research_config(config_path)
    if n_trials_assumed is not None:
        config.gates.n_trials_assumed = int(n_trials_assumed)
    writer = RunWriter(settings.runs_dir, run_id=run_id)
    writer.write_config_snapshot(config)

    events = load_events(events_path, config)
    events.write_parquet(writer.artifact_path("events.parquet"))

    panel = load_price_panel(events, config, cache_dir=settings.cache_dir / "prices")
    ranked = build_ranked(events, config)
    ranked.write_parquet(writer.artifact_path("ranked_events.parquet"))

    bt = run_backtest(ranked, panel, config)
    metrics = attach_overfit_metrics(
        bt.metrics,
        n_trials=int(config.gates.n_trials_assumed or 1),
    )
    bt.metrics = metrics

    pl.DataFrame(bt.equity).write_csv(writer.artifact_path("equity.csv"))
    pl.DataFrame(bt.trades).write_csv(writer.artifact_path("trades.csv"))
    writer.write_json("artifacts/metrics.json", metrics)
    writer.write_json("artifacts/rejects_summary.json", bt.rejects[:5000])

    pit = run_pit_audit(events, panel, config, strict=bool(config.gates.pit_strict))
    writer.write_json("artifacts/pit_audit.json", pit)

    ic_rows = []
    if do_ic:
        feat_cols = [c for c in events.columns if c.startswith("features.")]
        ic_df = compute_ic_table(events, panel, feat_cols[:12], config.ic_horizons)
        if ic_df.height:
            ic_df.write_csv(writer.artifact_path("ic_summary.csv"))
            ic_rows = ic_df.to_dicts()

    wf = None
    n_oos_folds = 0
    if do_wf:
        wf = run_walk_forward(events, panel, config)
        writer.write_json("artifacts/wf_folds.json", wf)
        n_oos_folds = int(wf.get("aggregate", {}).get("n_folds") or 0)

    gates = evaluate_gates(
        metrics,
        config.gates,
        n_oos_folds=n_oos_folds,
        pit_status=str(pit.get("status")),
    )
    conclusion = build_conclusion(
        run_id=writer.run_id,
        config=config,
        metrics=metrics,
        n_events=events.height,
        adjustment_as_of=panel.adjustment_as_of,
        gates_result=gates,
        ic_rows=ic_rows,
        wf=wf,
        pit_audit=pit,
    )
    html_path, json_path = write_report(writer.report, conclusion, run_dir=writer.root)
    zh_html = writer.report / "research_report_zh.html"
    writer.write_meta(
        {
            "command": "pipeline.research",
            "n_events": events.height,
            "adjustment_as_of": panel.adjustment_as_of,
            "data_fingerprint": panel.data_fingerprint,
            "promotable": conclusion["promotable"],
            "metrics": metrics,
            "pit_status": pit.get("status"),
            "n_trials_assumed": config.gates.n_trials_assumed,
        }
    )
    return {
        "run_id": writer.run_id,
        "summary": {
            "promotable": conclusion["promotable"],
            "n_events": events.height,
            "n_trades": metrics.get("n_trades"),
            "metrics": metrics,
            "gates": gates,
            "pit_status": pit.get("status"),
            "information_ratio": metrics.get("information_ratio"),
            "deflated_sharpe": metrics.get("deflated_sharpe"),
        },
        "artifacts": {
            "run_dir": str(writer.root),
            "conclusion_json": str(json_path),
            "conclusion_html": str(html_path),
            "research_report_zh": str(zh_html),
            "metrics_json": str(writer.artifact_path("metrics.json")),
            "pit_audit": str(writer.artifact_path("pit_audit.json")),
            "trades": str(writer.artifact_path("trades.csv")),
            "equity": str(writer.artifact_path("equity.csv")),
            "ic_summary": str(writer.artifact_path("ic_summary.csv")),
        },
        "next_actions": (
            [{"op": "validate.rolling", "reason": "need_more_oos_folds"}]
            if not conclusion["promotable"]
            else [{"op": "promote", "reason": "gates_passed"}]
        ),
    }


def pipeline_optimize(
    events_path: str | Path | list[str],
    config_path: str | Path | None = None,
    *,
    n_trials: int = 20,
    feature: str = "features.box_quality",
) -> dict[str, Any]:
    settings = get_settings()
    config = load_research_config(config_path)
    config.gates.n_trials_assumed = max(int(config.gates.n_trials_assumed or 1), int(n_trials))
    writer = RunWriter(settings.runs_dir)
    writer.write_config_snapshot(config)
    events = load_events(events_path, config)
    panel = load_price_panel(events, config, cache_dir=settings.cache_dir / "prices")
    opt = run_optuna(events, panel, config, n_trials=n_trials, feature=feature)
    writer.write_json(
        "artifacts/optuna_trials.json",
        {**opt, "n_trials": n_trials, "n_trials_assumed": config.gates.n_trials_assumed},
    )
    writer.write_meta(
        {
            "command": "pipeline.optimize",
            "best": opt.get("best_params"),
            "n_trials": n_trials,
            "n_trials_assumed": config.gates.n_trials_assumed,
        }
    )
    return {
        "run_id": writer.run_id,
        "summary": {
            "best_params": opt.get("best_params"),
            "best_value": opt.get("best_value"),
            "n_trials": n_trials,
            "n_trials_assumed": config.gates.n_trials_assumed,
        },
        "artifacts": {
            "run_dir": str(writer.root),
            "optuna_trials": str(writer.artifact_path("optuna_trials.json")),
        },
        "next_actions": [
            {
                "op": "pipeline.research",
                "reason": "apply_best_params",
                "n_trials_assumed": config.gates.n_trials_assumed,
            }
        ],
    }
