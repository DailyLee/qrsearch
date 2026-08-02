"""High-level research pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from qresearch.config import get_settings, load_research_config
from qresearch.engines.analysis.evaluation_check import check_evaluation_years
from qresearch.engines.analysis.invested import mean_invested_from_equity
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
from qresearch.engines.experiment.best_params import optimize_params_to_patches
from qresearch.engines.experiment.optimize import OptimizeError, run_signal_threshold_search
from qresearch.engines.experiment.registry import RunWriter
from qresearch.engines.experiment.sensitivity import (
    parse_sensitivity_args,
    parse_sensitivity_extended,
    run_sensitivity_grid,
)
from qresearch.engines.experiment.sweep import run_signal_sweep
from qresearch.engines.experiment.walkforward import run_walk_forward
from qresearch.engines.factor.band_ic import run_band_ic
from qresearch.engines.factor.diagnostics import (
    corr_top_pairs,
    feature_corr_matrix,
    quantile_monotonicity,
    reject_near_constant_features,
)
from qresearch.engines.factor.ic import (
    compute_alpha_beta_table,
    compute_ic_table,
    compute_icir_table,
    compute_quantile_returns,
)
from qresearch.engines.factor.preprocess import apply_factor_preprocess
from qresearch.engines.factor.sample_profile import build_sample_profile
from qresearch.engines.factor.universe import resolve_feature_cols
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

    feat_cols = resolve_feature_cols(events, config.factors)
    profile = build_sample_profile(events, feat_cols)
    writer.write_json("artifacts/sample_profile.json", profile)

    panel = load_price_panel(events, config, cache_dir=settings.cache_dir / "prices")
    ranked = build_ranked(events, config)
    ranked.write_parquet(writer.artifact_path("ranked_events.parquet"))

    bt = run_backtest(ranked, panel, config)
    metrics = attach_overfit_metrics(
        bt.metrics,
        n_trials=int(config.gates.n_trials_assumed or 1),
    )
    inv = mean_invested_from_equity(bt.equity)
    metrics["mean_invested"] = inv.get("mean_invested")
    metrics["empty_cash_share"] = inv.get("empty_cash_share")
    metrics["invested_definition"] = inv.get("definition")
    bt.metrics = metrics

    pl.DataFrame(bt.equity).write_csv(writer.artifact_path("equity.csv"))
    pl.DataFrame(bt.trades).write_csv(writer.artifact_path("trades.csv"))
    writer.write_json("artifacts/metrics.json", metrics)
    if metrics.get("yearly"):
        writer.write_json("artifacts/yearly_metrics.json", metrics["yearly"])
    writer.write_json("artifacts/rejects_summary.json", bt.rejects[:5000])

    eval_check = check_evaluation_years(config, profile)
    writer.write_json("artifacts/evaluation_check.json", eval_check)

    pit = run_pit_audit(events, panel, config, strict=bool(config.gates.pit_strict))
    writer.write_json("artifacts/pit_audit.json", pit)

    ic_rows = []
    icir_top = []
    alpha_top = []
    excess_ic_top = []
    preprocess_report: dict[str, Any] | None = None
    if do_ic and feat_cols:
        ic_events = events
        ic_feats = feat_cols
        if config.factors.preprocess.enabled:
            ic_events, preprocess_report = apply_factor_preprocess(
                events, feat_cols, config.factors.preprocess
            )
            writer.write_json("artifacts/preprocess_report.json", preprocess_report)
            ic_feats = list(preprocess_report.get("output_features") or [])
            if ic_feats:
                ic_events.write_parquet(writer.artifact_path("events_preprocessed.parquet"))
        ic_df = compute_ic_table(ic_events, panel, ic_feats, config.ic_horizons)
        if ic_df.height:
            ic_df.write_csv(writer.artifact_path("ic_summary.csv"))
            ic_rows = ic_df.to_dicts()
        icir_df = compute_icir_table(
            ic_events,
            panel,
            ic_feats,
            config.ic_horizons,
            min_periods=int(config.factors.icir_min_periods),
        )
        if icir_df.height:
            icir_df.write_csv(writer.artifact_path("icir_summary.csv"))
            # top by |icir| at horizon 5 if present else first horizon
            prefer_h = 5 if 5 in config.ic_horizons else config.ic_horizons[0]
            sub = icir_df.filter(pl.col("horizon") == prefer_h).drop_nulls(subset=["icir"])
            if sub.height:
                icir_top = (
                    sub.with_columns(pl.col("icir").abs().alias("_a"))
                    .sort("_a", descending=True)
                    .drop("_a")
                    .head(5)
                    .to_dicts()
                )
        q_df = compute_quantile_returns(
            ic_events,
            panel,
            ic_feats[: min(12, len(ic_feats))],
            horizon=int(config.factors.quantile_horizon),
            n_quantiles=int(config.factors.n_quantiles),
            benchmark=config.benchmark.instrument,
        )
        if q_df.height:
            q_df.write_csv(writer.artifact_path("quantile_returns.csv"))
        ab_df = compute_alpha_beta_table(
            ic_events,
            panel,
            ic_feats,
            config.ic_horizons,
            benchmark=config.benchmark.instrument,
        )
        if ab_df.height:
            ab_df.write_csv(writer.artifact_path("alpha_beta_summary.csv"))
            prefer_h_ab = 5 if 5 in config.ic_horizons else config.ic_horizons[0]
            sub_ab = ab_df.filter(pl.col("horizon") == prefer_h_ab)
            if sub_ab.height:
                alpha_top = (
                    sub_ab.with_columns(pl.col("top_bottom_excess").abs().alias("_a"))
                    .sort("_a", descending=True)
                    .drop("_a")
                    .head(5)
                    .to_dicts()
                )
                excess_ic_top = (
                    sub_ab.with_columns(pl.col("rank_ic_excess").abs().alias("_a"))
                    .sort("_a", descending=True)
                    .drop("_a")
                    .head(5)
                    .to_dicts()
                )

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
    metrics_meta = {
        k: v for k, v in metrics.items() if k not in ("benchmark_nav_series",)
    }
    writer.write_meta(
        {
            "command": "pipeline.research",
            "n_events": events.height,
            "adjustment_as_of": panel.adjustment_as_of,
            "data_fingerprint": panel.data_fingerprint,
            "promotable": conclusion["promotable"],
            "metrics": metrics_meta,
            "pit_status": pit.get("status"),
            "n_trials_assumed": config.gates.n_trials_assumed,
            "hypothesis": config.hypothesis.model_dump(),
            "evaluation": config.evaluation.model_dump(),
            "evaluation_check": eval_check,
            "study_id": config.hypothesis.study_id,
            "gates": gates,
            "n_factor_cols": len(feat_cols),
        }
    )
    next_actions: list[dict[str, Any]]
    if conclusion["promotable"]:
        next_actions = [{"op": "promote", "reason": "gates_passed"}]
    elif gates.get("structural_passed") and not gates.get("economic_passed"):
        next_actions = [{"op": "revise_strategy", "reason": "economic_gates_failed"}]
    else:
        next_actions = [{"op": "validate.rolling", "reason": "need_more_oos_folds"}]

    return {
        "run_id": writer.run_id,
        "summary": {
            "promotable": conclusion["promotable"],
            "n_events": events.height,
            "n_trades": metrics.get("n_trades"),
            "metrics": metrics,
            "gates": gates,
            "hypothesis": config.hypothesis.model_dump(),
            "evaluation": config.evaluation.model_dump(),
            "evaluation_check": eval_check,
            "mean_invested": metrics.get("mean_invested"),
            "empty_cash_share": metrics.get("empty_cash_share"),
            "ann_excess": metrics.get("ann_excess"),
            "sample_profile": {
                "n_events": profile.get("n_events"),
                "n_instruments": profile.get("n_instruments"),
                "years": profile.get("years"),
            },
            "icir_top": icir_top,
            "alpha_top": alpha_top,
            "excess_ic_top": excess_ic_top,
            "preprocess": preprocess_report,
            "features_prepped": (preprocess_report or {}).get("output_features")
            if preprocess_report
            else [],
            "pit_status": pit.get("status"),
            "information_ratio": metrics.get("information_ratio"),
            "deflated_sharpe": metrics.get("deflated_sharpe"),
            "benchmark": config.benchmark.instrument,
        },
        "artifacts": {
            "run_dir": str(writer.root),
            "conclusion_json": str(json_path),
            "conclusion_html": str(html_path),
            "research_report_zh": str(zh_html),
            "metrics_json": str(writer.artifact_path("metrics.json")),
            "pit_audit": str(writer.artifact_path("pit_audit.json")),
            "sample_profile": str(writer.artifact_path("sample_profile.json")),
            "trades": str(writer.artifact_path("trades.csv")),
            "equity": str(writer.artifact_path("equity.csv")),
            "ic_summary": str(writer.artifact_path("ic_summary.csv")),
            "icir_summary": str(writer.artifact_path("icir_summary.csv")),
            "quantile_returns": str(writer.artifact_path("quantile_returns.csv")),
            "alpha_beta_summary": str(writer.artifact_path("alpha_beta_summary.csv")),
            "preprocess_report": str(writer.artifact_path("preprocess_report.json")),
            "events_preprocessed": str(writer.artifact_path("events_preprocessed.parquet")),
        },
        "next_actions": next_actions,
    }


def pipeline_factor_compare(
    events_path: str | Path | list[str],
    config_path: str | Path | None = None,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Factor IC / ICIR / quantile / alpha-beta only; persist artifacts + run_id (no backtest)."""
    settings = get_settings()
    config = load_research_config(config_path)
    writer = RunWriter(settings.runs_dir, run_id=run_id)
    writer.write_config_snapshot(config)

    events = load_events(events_path, config)
    events.write_parquet(writer.artifact_path("events.parquet"))
    feats = resolve_feature_cols(events, config.factors)
    profile = build_sample_profile(events, feats)
    writer.write_json("artifacts/sample_profile.json", profile)

    panel = load_price_panel(events, config, cache_dir=settings.cache_dir / "prices")
    preprocess_report: dict[str, Any] | None = None
    ic_events = events
    ic_feats = feats
    if config.factors.preprocess.enabled:
        ic_events, preprocess_report = apply_factor_preprocess(
            events, feats, config.factors.preprocess
        )
        writer.write_json("artifacts/preprocess_report.json", preprocess_report)
        ic_feats = list(preprocess_report.get("output_features") or [])
        if ic_feats:
            ic_events.write_parquet(writer.artifact_path("events_preprocessed.parquet"))

    ic = compute_ic_table(ic_events, panel, ic_feats, config.ic_horizons)
    if ic.height:
        ic.write_csv(writer.artifact_path("ic_summary.csv"))
    icir = compute_icir_table(
        ic_events,
        panel,
        ic_feats,
        config.ic_horizons,
        min_periods=int(config.factors.icir_min_periods),
    )
    if icir.height:
        icir.write_csv(writer.artifact_path("icir_summary.csv"))
    bench = config.benchmark.instrument
    quant = compute_quantile_returns(
        ic_events,
        panel,
        ic_feats[: min(12, len(ic_feats))],
        horizon=int(config.factors.quantile_horizon),
        n_quantiles=int(config.factors.n_quantiles),
        benchmark=bench,
    )
    if quant.height:
        quant.write_csv(writer.artifact_path("quantile_returns.csv"))

    rejected_constant = reject_near_constant_features(ic_events, ic_feats)
    keep_feats = [f for f in ic_feats if f not in {r["feature"] for r in rejected_constant}]
    corr_df = feature_corr_matrix(ic_events, keep_feats, method="spearman")
    if corr_df.height:
        corr_df.write_csv(writer.artifact_path("factor_corr.csv"))
    top_pairs = corr_top_pairs(corr_df, top_n=10)
    mono = quantile_monotonicity(quant)
    writer.write_json(
        "artifacts/factor_diagnostics.json",
        {
            "rejected_constant": rejected_constant,
            "corr_top_pairs": top_pairs,
            "monotonicity": mono,
        },
    )

    ab = compute_alpha_beta_table(
        ic_events, panel, ic_feats, config.ic_horizons, benchmark=bench
    )
    if ab.height:
        ab.write_csv(writer.artifact_path("alpha_beta_summary.csv"))

    prefer_h = 5 if 5 in config.ic_horizons else (config.ic_horizons[0] if config.ic_horizons else 5)
    icir_top: list[dict[str, Any]] = []
    if icir.height:
        sub = icir.filter(pl.col("horizon") == prefer_h)
        if sub.height:
            icir_top = (
                sub.with_columns(pl.col("icir").abs().alias("_a"))
                .sort("_a", descending=True)
                .drop("_a")
                .head(8)
                .to_dicts()
            )
    alpha_top: list[dict[str, Any]] = []
    excess_ic_top: list[dict[str, Any]] = []
    if ab.height:
        sub_ab = ab.filter(pl.col("horizon") == prefer_h)
        if sub_ab.height:
            alpha_top = (
                sub_ab.with_columns(pl.col("top_bottom_excess").abs().alias("_a"))
                .sort("_a", descending=True)
                .drop("_a")
                .head(8)
                .to_dicts()
            )
            excess_ic_top = (
                sub_ab.with_columns(pl.col("rank_ic_excess").abs().alias("_a"))
                .sort("_a", descending=True)
                .drop("_a")
                .head(8)
                .to_dicts()
            )

    writer.write_meta(
        {
            "command": "factor.compare",
            "n_events": events.height,
            "n_factor_cols": len(feats),
            "adjustment_as_of": panel.adjustment_as_of,
            "data_fingerprint": panel.data_fingerprint,
            "benchmark": bench,
            "hypothesis": config.hypothesis.model_dump(),
            "study_id": config.hypothesis.study_id,
            "promotable": False,
        }
    )
    return {
        "run_id": writer.run_id,
        "summary": {
            "n_features": len(feats),
            "features": feats,
            "features_prepped": (preprocess_report or {}).get("output_features") or [],
            "preprocess": preprocess_report,
            "benchmark": bench,
            "sample_profile": {
                "n_events": profile.get("n_events"),
                "n_instruments": profile.get("n_instruments"),
                "years": profile.get("years"),
                "duplicate_keys": profile.get("duplicate_keys"),
            },
            "icir_top": icir_top,
            "alpha_top": alpha_top,
            "excess_ic_top": excess_ic_top,
            "corr_top_pairs": top_pairs,
            "monotonicity": mono,
            "rejected_constant": rejected_constant,
            "promotable": False,
        },
        "artifacts": {
            "run_dir": str(writer.root),
            "sample_profile": str(writer.artifact_path("sample_profile.json")),
            "ic_summary": str(writer.artifact_path("ic_summary.csv")),
            "icir_summary": str(writer.artifact_path("icir_summary.csv")),
            "quantile_returns": str(writer.artifact_path("quantile_returns.csv")),
            "factor_corr": str(writer.artifact_path("factor_corr.csv")),
            "factor_diagnostics": str(writer.artifact_path("factor_diagnostics.json")),
            "alpha_beta_summary": str(writer.artifact_path("alpha_beta_summary.csv")),
            "preprocess_report": str(writer.artifact_path("preprocess_report.json")),
            "events_preprocessed": str(writer.artifact_path("events_preprocessed.parquet")),
            "events": str(writer.artifact_path("events.parquet")),
        },
        "next_actions": [
            {"op": "strategy_design", "reason": "freeze_candidate_pool_into_yaml"},
            {"op": "study.decision", "reason": "archive_factor_analysis", "stage": "factor_analysis"},
        ],
    }


def pipeline_optimize(
    events_path: str | Path | list[str],
    config_path: str | Path | None = None,
    *,
    feature: str | None = None,
    side: str = "auto",
    keep_frac: str = "0.1,0.2,0.3,0.4",
    n_trials: int | None = None,
) -> dict[str, Any]:
    """Direction-aware signal threshold grid (not absolute high-quantile Optuna)."""
    settings = get_settings()
    config = load_research_config(config_path)
    writer = RunWriter(settings.runs_dir)
    writer.write_config_snapshot(config)
    events = load_events(events_path, config)
    panel = load_price_panel(events, config, cache_dir=settings.cache_dir / "prices")
    opt = run_signal_threshold_search(
        events,
        panel,
        config,
        feature=feature,
        side=side,
        keep_fracs=keep_frac,
        max_grid=n_trials,
    )
    n_grid = int(opt.get("n_grid") or 0)
    config.gates.n_trials_assumed = max(int(config.gates.n_trials_assumed or 1), n_grid)
    unified = optimize_params_to_patches(
        dict(opt.get("best_params") or {}),
        metric="sharpe",
        best_value=opt.get("best_value"),
    )
    opt_out = {
        **opt,
        "best_params": {**(opt.get("best_params") or {}), "patches": unified["patches"]},
        "best_params_unified": unified,
        "n_trials_assumed": config.gates.n_trials_assumed,
    }
    writer.write_json(
        "artifacts/signal_threshold_trials.json",
        opt_out,
    )
    # legacy alias path for older readers
    writer.write_json("artifacts/optuna_trials.json", opt_out)
    writer.write_meta(
        {
            "command": "pipeline.optimize",
            "method": "signal_quantile_grid",
            "best": opt_out.get("best_params"),
            "side": opt.get("side"),
            "feature": opt.get("feature"),
            "n_grid": n_grid,
            "n_trials_assumed": config.gates.n_trials_assumed,
        }
    )
    return {
        "run_id": writer.run_id,
        "summary": {
            "best_params": opt_out.get("best_params"),
            "best_params_unified": unified,
            "best_value": opt.get("best_value"),
            "side": opt.get("side"),
            "feature": opt.get("feature"),
            "method": "signal_quantile_grid",
            "n_grid": n_grid,
            "n_trials": n_grid,
            "n_trials_assumed": config.gates.n_trials_assumed,
        },
        "artifacts": {
            "run_dir": str(writer.root),
            "signal_threshold_trials": str(
                writer.artifact_path("signal_threshold_trials.json")
            ),
            "optuna_trials": str(writer.artifact_path("optuna_trials.json")),
        },
        "next_actions": [
            {
                "op": "pipeline.research",
                "reason": "apply_best_params_to_new_yaml",
                "n_trials_assumed": config.gates.n_trials_assumed,
            }
        ],
    }


def pipeline_sweep(
    events_path: str | Path | list[str],
    config_path: str | Path | None = None,
    *,
    set_specs: list[str],
    metric: str = "sharpe",
    max_grid: int = 64,
) -> dict[str, Any]:
    """Multi-filter signal grid (--set); not joint with sensitivity knobs."""
    settings = get_settings()
    config = load_research_config(config_path)
    writer = RunWriter(settings.runs_dir)
    writer.write_config_snapshot(config)
    events = load_events(events_path, config)
    panel = load_price_panel(events, config, cache_dir=settings.cache_dir / "prices")
    out = run_signal_sweep(
        events,
        panel,
        config,
        set_specs=set_specs,
        metric=metric,
        max_grid=max_grid,
    )
    n_grid = int(out.get("n_grid") or 0)
    config.gates.n_trials_assumed = max(int(config.gates.n_trials_assumed or 1), n_grid)
    # grid CSV without nested patches
    grid_rows = []
    for r in out["rows"]:
        flat = {k: v for k, v in r.items() if k not in ("patches", "assignments")}
        grid_rows.append(flat)
    if grid_rows:
        pl.DataFrame(grid_rows).write_csv(writer.artifact_path("sweep_grid.csv"))
    summary = {
        "n_grid": n_grid,
        "truncated": out.get("truncated"),
        "metric": metric,
        "best_params": out.get("best_params"),
        "best_value": out.get("best_value"),
        "method": "signal_sweep",
        "n_trials_assumed": config.gates.n_trials_assumed,
    }
    writer.write_json("artifacts/sweep_summary.json", summary)
    writer.write_meta(
        {
            "command": "pipeline.sweep",
            "method": "signal_sweep",
            "n_grid": n_grid,
            "truncated": out.get("truncated"),
            "n_trials_assumed": config.gates.n_trials_assumed,
            "best": out.get("best_params"),
        }
    )
    return {
        "run_id": writer.run_id,
        "summary": summary,
        "artifacts": {
            "run_dir": str(writer.root),
            "sweep_grid": str(writer.artifact_path("sweep_grid.csv")),
            "sweep_summary": str(writer.artifact_path("sweep_summary.json")),
        },
        "next_actions": [
            {
                "op": "config.apply-best",
                "reason": "write_patched_yaml_then_research",
                "n_trials_assumed": config.gates.n_trials_assumed,
            }
        ],
    }


def pipeline_sensitivity(
    events_path: str | Path | list[str],
    config_path: str | Path | None = None,
    *,
    cost_mult: str = "1,1.5,2",
    stop: str = "-0.05,-0.086,-0.12",
    take: str = "0.10,0.158,0.20",
    max_hold: str | None = None,
    max_weight: str | None = None,
    max_new: str | None = None,
    sizing_base: str | None = None,
    max_names_per_industry: str | None = None,
    max_new_per_industry: str | None = None,
    max_grid: int = 64,
) -> dict[str, Any]:
    settings = get_settings()
    config = load_research_config(config_path)
    cms, stops, takes = parse_sensitivity_args(cost_mult, stop, take)
    ext = parse_sensitivity_extended(
        max_hold=max_hold,
        max_weight=max_weight,
        max_new=max_new,
        sizing_base=sizing_base,
        max_names_per_industry=max_names_per_industry,
        max_new_per_industry=max_new_per_industry,
    )
    n_est = len(cms) * len(stops) * len(takes)
    for k in (
        "max_hold",
        "max_weight",
        "max_new",
        "sizing_base",
        "max_names_per_industry",
        "max_new_per_industry",
    ):
        n_est *= len(ext[k]) if k in ext else 1
    n_grid_est = min(max_grid, max(1, n_est))
    config.gates.n_trials_assumed = max(int(config.gates.n_trials_assumed or 1), n_grid_est)

    writer = RunWriter(settings.runs_dir)
    writer.write_config_snapshot(config)
    events = load_events(events_path, config)
    panel = load_price_panel(events, config, cache_dir=settings.cache_dir / "prices")
    grid = run_sensitivity_grid(
        events,
        panel,
        config,
        cost_mult=cms,
        stops=stops,
        takes=takes,
        max_grid=max_grid,
        **ext,
    )
    pl.DataFrame(grid["rows"]).write_csv(writer.artifact_path("sensitivity_grid.csv"))
    writer.write_json(
        "artifacts/sensitivity_summary.json",
        {
            "n_grid": grid["n_grid"],
            "truncated": grid["truncated"],
            "best_by_sharpe": grid["best_by_sharpe"],
            "best_params": grid.get("best_params"),
            "n_robust_cost2_nonneg_sharpe": grid["n_robust_cost2_nonneg_sharpe"],
            "n_trials_assumed": config.gates.n_trials_assumed,
        },
    )
    writer.write_meta(
        {
            "command": "pipeline.sensitivity",
            "n_grid": grid["n_grid"],
            "promotable": False,
            "n_trials_assumed": config.gates.n_trials_assumed,
            "hypothesis": config.hypothesis.model_dump(),
        }
    )
    return {
        "run_id": writer.run_id,
        "summary": {
            "n_grid": grid["n_grid"],
            "truncated": grid["truncated"],
            "best_by_sharpe": grid["best_by_sharpe"],
            "best_params": grid.get("best_params"),
            "n_robust_cost2_nonneg_sharpe": grid["n_robust_cost2_nonneg_sharpe"],
            "promotable": False,
            "n_trials_assumed": config.gates.n_trials_assumed,
        },
        "artifacts": {
            "run_dir": str(writer.root),
            "sensitivity_grid": str(writer.artifact_path("sensitivity_grid.csv")),
            "sensitivity_summary": str(writer.artifact_path("sensitivity_summary.json")),
        },
        "next_actions": [
            {
                "op": "config.apply-best",
                "reason": "freeze_best_execution_risk_in_new_yaml",
                "n_trials_assumed": config.gates.n_trials_assumed,
            }
        ],
    }


def pipeline_band_ic(
    events_path: str | Path | list[str],
    config_path: str | Path | None = None,
    *,
    feature: str,
    lo: float,
    hi: float,
    horizons: str | None = None,
    inside_feature: list[str] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Full-sample vs in-band Rank IC for an interval-factor hypothesis (train only)."""
    settings = get_settings()
    config = load_research_config(config_path)
    writer = RunWriter(settings.runs_dir, run_id=run_id)
    writer.write_config_snapshot(config)
    events = load_events(events_path, config)
    panel = load_price_panel(events, config, cache_dir=settings.cache_dir / "prices")
    if horizons:
        hs = [int(x.strip()) for x in str(horizons).split(",") if x.strip()]
    else:
        hs = list(config.ic_horizons)
    inside = list(inside_feature or [])
    if not inside:
        # default: up to 3 other resolved feature cols
        feats = resolve_feature_cols(events, config.factors)
        inside = [f for f in feats if f != feature][:3]
    out = run_band_ic(
        events,
        panel,
        feature=feature,
        lo=float(lo),
        hi=float(hi),
        horizons=hs,
        inside_features=inside,
        icir_min_periods=int(config.factors.icir_min_periods),
    )
    if out["rows"]:
        pl.DataFrame(out["rows"]).write_csv(writer.artifact_path("band_ic_compare.csv"))
    if out["inside_rows"]:
        pl.DataFrame(out["inside_rows"]).write_csv(writer.artifact_path("band_ic_inside.csv"))
    summary = {
        "feature": out["feature"],
        "lo": out["lo"],
        "hi": out["hi"],
        "n_full": out["n_full"],
        "n_band": out["n_band"],
        "keep_frac": out["keep_frac"],
        "band_stronger": out["band_stronger"],
        "band_stronger_share": out["band_stronger_share"],
        "rows": out["rows"],
        "inside_n": len(out["inside_rows"]),
        "promotable": False,
    }
    writer.write_json("artifacts/band_ic_summary.json", {**out, "summary": summary})
    writer.write_meta(
        {
            "command": "factor.band-ic",
            "feature": feature,
            "lo": lo,
            "hi": hi,
            "n_full": out["n_full"],
            "n_band": out["n_band"],
            "band_stronger": out["band_stronger"],
            "promotable": False,
        }
    )
    return {
        "run_id": writer.run_id,
        "summary": summary,
        "artifacts": {
            "run_dir": str(writer.root),
            "band_ic_summary": str(writer.artifact_path("band_ic_summary.json")),
            "band_ic_compare": str(writer.artifact_path("band_ic_compare.csv")),
            "band_ic_inside": str(writer.artifact_path("band_ic_inside.csv")),
        },
        "next_actions": [
            {"op": "pipeline.sweep", "reason": "search_between_bounds_on_train"},
            {"op": "strategy_design", "reason": "freeze_band_plus_mono_rank_yaml"},
        ],
    }
