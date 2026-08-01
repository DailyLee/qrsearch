"""Purged event walk-forward."""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

import polars as pl

from qresearch.config.models import ResearchConfig, WalkForwardConfig
from qresearch.engines.backtest.session import BacktestResult, run_backtest
from qresearch.engines.data.panel import PricePanel
from qresearch.engines.signal.engine import build_ranked


def _as_date(v: object) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def year_bounds(events: pl.DataFrame) -> list[int]:
    years = sorted({_as_date(d).year for d in events["entry_intent_date"].to_list()})
    return years


def build_folds(events: pl.DataFrame, wf: WalkForwardConfig) -> list[dict[str, Any]]:
    years = year_bounds(events)
    folds = []
    if wf.mode == "expanding":
        for i in range(1, len(years)):
            is_years = years[:i]
            oos_year = years[i]
            folds.append(
                {
                    "name": f"expanding_to_{oos_year}",
                    "is_years": is_years,
                    "oos_years": [oos_year],
                }
            )
    else:
        L = wf.rolling_is_years
        for i in range(L, len(years)):
            is_years = years[i - L : i]
            oos_year = years[i]
            folds.append(
                {
                    "name": f"rolling_{is_years[0]}_{is_years[-1]}_oos_{oos_year}",
                    "is_years": is_years,
                    "oos_years": [oos_year],
                }
            )
    return folds


def _mask_years(events: pl.DataFrame, years: list[int]) -> pl.DataFrame:
    ys = set(years)

    def ok(d: object) -> bool:
        return _as_date(d).year in ys

    return events.filter(pl.col("entry_intent_date").map_elements(ok, return_dtype=pl.Boolean))


def purge_is_events(is_events: pl.DataFrame, oos_start: date) -> pl.DataFrame:
    """Drop IS events whose planned hold intersects OOS (exit_intent >= oos_start)."""

    def keep(row_exit: object) -> bool:
        return _as_date(row_exit) < oos_start

    return is_events.filter(
        pl.col("exit_intent_date").map_elements(keep, return_dtype=pl.Boolean)
    )


def run_walk_forward(
    events: pl.DataFrame,
    panel: PricePanel,
    config: ResearchConfig,
    *,
    backtest_fn: Callable | None = None,
) -> dict[str, Any]:
    bt = backtest_fn or run_backtest
    folds = build_folds(events, config.walk_forward)
    fold_rows = []
    for fold in folds:
        is_ev = _mask_years(events, fold["is_years"])
        oos_ev = _mask_years(events, fold["oos_years"])
        if oos_ev.height == 0:
            continue
        oos_start = min(_as_date(d) for d in oos_ev["entry_intent_date"].to_list())
        is_purged = purge_is_events(is_ev, oos_start)
        # OOS evaluation only
        ranked_oos = build_ranked(oos_ev, config)
        res: BacktestResult = bt(ranked_oos, panel, config)
        fold_rows.append(
            {
                "fold": fold["name"],
                "is_events": is_purged.height,
                "is_events_before_purge": is_ev.height,
                "oos_events": oos_ev.height,
                "metrics": res.metrics,
                "n_trades": res.metrics.get("n_trades", 0),
                "sharpe": res.metrics.get("sharpe", 0.0),
            }
        )

    if not fold_rows:
        return {"folds": [], "aggregate": {"sharpe": 0.0, "n_folds": 0}}

    # aggregate
    total_trades = sum(r["n_trades"] for r in fold_rows)
    if config.walk_forward.objective == "trade_weighted_sharpe" and total_trades > 0:
        agg_sharpe = sum(r["sharpe"] * r["n_trades"] for r in fold_rows) / total_trades
    else:
        valid = [r for r in fold_rows if r["n_trades"] >= config.walk_forward.min_trades]
        agg_sharpe = sum(r["sharpe"] for r in valid) / len(valid) if valid else 0.0

    from qresearch.engines.analysis.overfit import deflated_sharpe

    n_obs = sum(int((r.get("metrics") or {}).get("n_return_obs") or 0) for r in fold_rows)
    n_obs = max(n_obs, 2)
    n_trials = int(getattr(config.gates, "n_trials_assumed", 1) or 1)
    dsr = deflated_sharpe(float(agg_sharpe), n_obs=n_obs, n_trials=n_trials)
    return {
        "folds": fold_rows,
        "aggregate": {
            "sharpe": agg_sharpe,
            "n_folds": len(fold_rows),
            "total_trades": total_trades,
            "n_obs": n_obs,
            "n_trials": n_trials,
            "deflated_sharpe": dsr["deflated_sharpe"],
            "dsr_prob": dsr["dsr_prob"],
        },
    }
