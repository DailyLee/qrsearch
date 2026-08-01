"""Execution / risk sensitivity grid (no full WF by default)."""

from __future__ import annotations

import itertools
from copy import deepcopy
from typing import Any

from qresearch.config.models import ResearchConfig
from qresearch.engines.analysis.overfit import attach_overfit_metrics
from qresearch.engines.backtest.session import run_backtest
from qresearch.engines.data.panel import PricePanel
from qresearch.engines.signal.engine import build_ranked


def _parse_floats(spec: str) -> list[float]:
    parts = [p.strip() for p in str(spec).split(",") if p.strip() != ""]
    return [float(p) for p in parts]


def run_sensitivity_grid(
    events,
    panel: PricePanel,
    base_config: ResearchConfig,
    *,
    cost_mult: list[float] | None = None,
    stops: list[float | None] | None = None,
    takes: list[float | None] | None = None,
    max_grid: int = 27,
) -> dict[str, Any]:
    cost_mult = cost_mult or [1.0]
    stops = stops if stops is not None else [base_config.risk.stop_loss]
    takes = takes if takes is not None else [base_config.risk.take_profit]

    combos = list(itertools.product(cost_mult, stops, takes))
    truncated = False
    if len(combos) > max_grid:
        combos = combos[:max_grid]
        truncated = True

    rows: list[dict[str, Any]] = []
    for cm, stop, take in combos:
        cfg = base_config.model_copy(deep=True)
        cfg.costs.commission_rate = float(base_config.costs.commission_rate) * float(cm)
        cfg.costs.stamp_duty_rate = float(base_config.costs.stamp_duty_rate) * float(cm)
        cfg.costs.transfer_fee_rate = float(base_config.costs.transfer_fee_rate or 0.0) * float(cm)
        cfg.costs.slippage_bps = float(base_config.costs.slippage_bps) * float(cm)
        cfg.risk.stop_loss = stop
        cfg.risk.take_profit = take
        ranked = build_ranked(events, cfg)
        bt = run_backtest(ranked, panel, cfg)
        m = attach_overfit_metrics(
            bt.metrics,
            n_trials=max(int(base_config.gates.n_trials_assumed or 1), len(combos)),
        )
        rows.append(
            {
                "cost_mult": float(cm),
                "stop_loss": stop,
                "take_profit": take,
                "n_trades": m.get("n_trades"),
                "total_return": m.get("total_return"),
                "sharpe": m.get("sharpe"),
                "max_dd": m.get("max_dd"),
                "end_nav": m.get("end_nav"),
                "deflated_sharpe": m.get("deflated_sharpe"),
            }
        )

    # best by sharpe among finite
    def _sharpe(r: dict) -> float:
        v = r.get("sharpe")
        try:
            return float(v)
        except (TypeError, ValueError):
            return float("-inf")

    best = max(rows, key=_sharpe) if rows else None
    robust = [
        r
        for r in rows
        if float(r.get("cost_mult") or 0) >= 2.0 - 1e-9 and float(r.get("sharpe") or -1e9) >= 0.0
    ]
    return {
        "n_grid": len(rows),
        "truncated": truncated,
        "rows": rows,
        "best_by_sharpe": best,
        "n_robust_cost2_nonneg_sharpe": len(robust),
    }


def parse_sensitivity_args(
    cost_mult: str,
    stop: str,
    take: str,
) -> tuple[list[float], list[float | None], list[float | None]]:
    cms = _parse_floats(cost_mult)

    def _opt_floats(spec: str) -> list[float | None]:
        out: list[float | None] = []
        for p in str(spec).split(","):
            p = p.strip()
            if p.lower() in ("", "none", "null"):
                out.append(None)
            else:
                out.append(float(p))
        return out

    return cms, _opt_floats(stop), _opt_floats(take)
