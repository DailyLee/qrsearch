"""Execution / portfolio / risk sensitivity grid (no full WF by default)."""

from __future__ import annotations

import itertools
from typing import Any

from qresearch.config.models import ResearchConfig
from qresearch.engines.analysis.invested import mean_invested_from_equity
from qresearch.engines.analysis.overfit import attach_overfit_metrics
from qresearch.engines.backtest.session import run_backtest
from qresearch.engines.data.panel import PricePanel
from qresearch.engines.experiment.best_params import sensitivity_row_to_patches
from qresearch.engines.signal.engine import build_ranked


def _parse_floats(spec: str) -> list[float]:
    parts = [p.strip() for p in str(spec).split(",") if p.strip() != ""]
    return [float(p) for p in parts]


def _parse_opt_floats(spec: str) -> list[float | None]:
    out: list[float | None] = []
    for p in str(spec).split(","):
        p = p.strip()
        if p.lower() in ("", "none", "null"):
            out.append(None)
        else:
            out.append(float(p))
    return out


def _parse_opt_ints(spec: str) -> list[int | None]:
    out: list[int | None] = []
    for p in str(spec).split(","):
        p = p.strip()
        if p.lower() in ("", "none", "null"):
            out.append(None)
        else:
            out.append(int(float(p)))
    return out


def _parse_sizing_bases(spec: str) -> list[str]:
    parts = [p.strip() for p in str(spec).split(",") if p.strip() != ""]
    out: list[str] = []
    for p in parts:
        if p not in ("cash", "nav"):
            raise ValueError(f"sizing_base must be cash|nav, got {p!r}")
        out.append(p)
    return out


def run_sensitivity_grid(
    events,
    panel: PricePanel,
    base_config: ResearchConfig,
    *,
    cost_mult: list[float] | None = None,
    stops: list[float | None] | None = None,
    takes: list[float | None] | None = None,
    max_hold: list[int | None] | None = None,
    max_weight: list[float] | None = None,
    max_new: list[int] | None = None,
    sizing_base: list[str] | None = None,
    max_names_per_industry: list[int | None] | None = None,
    max_new_per_industry: list[int | None] | None = None,
    max_grid: int = 64,
) -> dict[str, Any]:
    cost_mult = cost_mult or [1.0]
    stops = stops if stops is not None else [base_config.risk.stop_loss]
    takes = takes if takes is not None else [base_config.risk.take_profit]
    max_hold = max_hold if max_hold is not None else [base_config.risk.max_hold_sessions]
    max_weight = max_weight if max_weight is not None else [base_config.portfolio.max_weight]
    max_new = max_new if max_new is not None else [base_config.portfolio.max_new_entries_per_day]
    sizing_base = sizing_base if sizing_base is not None else [base_config.portfolio.sizing_base]
    max_names_per_industry = (
        max_names_per_industry
        if max_names_per_industry is not None
        else [base_config.portfolio.max_names_per_industry]
    )
    max_new_per_industry = (
        max_new_per_industry
        if max_new_per_industry is not None
        else [base_config.portfolio.max_new_per_industry_per_day]
    )

    combos = list(
        itertools.product(
            cost_mult,
            stops,
            takes,
            max_hold,
            max_weight,
            max_new,
            sizing_base,
            max_names_per_industry,
            max_new_per_industry,
        )
    )
    truncated = False
    if len(combos) > max_grid:
        combos = combos[:max_grid]
        truncated = True

    rows: list[dict[str, Any]] = []
    for cm, stop, take, mh, mw, mn, sb, mni, mnpi in combos:
        cfg = base_config.model_copy(deep=True)
        cfg.costs.commission_rate = float(base_config.costs.commission_rate) * float(cm)
        cfg.costs.stamp_duty_rate = float(base_config.costs.stamp_duty_rate) * float(cm)
        cfg.costs.transfer_fee_rate = float(base_config.costs.transfer_fee_rate or 0.0) * float(cm)
        cfg.costs.slippage_bps = float(base_config.costs.slippage_bps) * float(cm)
        cfg.risk.stop_loss = stop
        cfg.risk.take_profit = take
        cfg.risk.max_hold_sessions = mh
        cfg.portfolio.max_weight = float(mw)
        cfg.portfolio.max_new_entries_per_day = int(mn)
        cfg.portfolio.sizing_base = sb  # type: ignore[assignment]
        cfg.portfolio.max_names_per_industry = mni
        cfg.portfolio.max_new_per_industry_per_day = mnpi
        ranked = build_ranked(events, cfg)
        bt = run_backtest(ranked, panel, cfg)
        m = attach_overfit_metrics(
            bt.metrics,
            n_trials=max(int(base_config.gates.n_trials_assumed or 1), len(combos)),
        )
        inv = mean_invested_from_equity(bt.equity)
        rows.append(
            {
                "cost_mult": float(cm),
                "stop_loss": stop,
                "take_profit": take,
                "max_hold_sessions": mh,
                "max_weight": float(mw),
                "max_new_entries_per_day": int(mn),
                "sizing_base": sb,
                "max_names_per_industry": mni,
                "max_new_per_industry_per_day": mnpi,
                "n_trades": m.get("n_trades"),
                "total_return": m.get("total_return"),
                "sharpe": m.get("sharpe"),
                "max_dd": m.get("max_dd"),
                "end_nav": m.get("end_nav"),
                "deflated_sharpe": m.get("deflated_sharpe"),
                "mean_invested": inv.get("mean_invested"),
                "empty_cash_share": inv.get("empty_cash_share"),
            }
        )

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
    best_params = sensitivity_row_to_patches(best) if best else None
    return {
        "n_grid": len(rows),
        "truncated": truncated,
        "rows": rows,
        "best_by_sharpe": best,
        "best_params": best_params,
        "n_robust_cost2_nonneg_sharpe": len(robust),
    }


def parse_sensitivity_args(
    cost_mult: str,
    stop: str,
    take: str,
) -> tuple[list[float], list[float | None], list[float | None]]:
    return _parse_floats(cost_mult), _parse_opt_floats(stop), _parse_opt_floats(take)


def parse_sensitivity_extended(
    *,
    max_hold: str | None = None,
    max_weight: str | None = None,
    max_new: str | None = None,
    sizing_base: str | None = None,
    max_names_per_industry: str | None = None,
    max_new_per_industry: str | None = None,
) -> dict[str, Any]:
    """Parse optional CLI specs; missing/None → omit key (caller uses YAML single point)."""
    out: dict[str, Any] = {}
    if max_hold is not None and str(max_hold).strip() != "":
        out["max_hold"] = _parse_opt_ints(max_hold)
    if max_weight is not None and str(max_weight).strip() != "":
        out["max_weight"] = _parse_floats(max_weight)
    if max_new is not None and str(max_new).strip() != "":
        out["max_new"] = [int(x) for x in _parse_floats(max_new)]
    if sizing_base is not None and str(sizing_base).strip() != "":
        out["sizing_base"] = _parse_sizing_bases(sizing_base)
    if max_names_per_industry is not None and str(max_names_per_industry).strip() != "":
        out["max_names_per_industry"] = _parse_opt_ints(max_names_per_industry)
    if max_new_per_industry is not None and str(max_new_per_industry).strip() != "":
        out["max_new_per_industry"] = _parse_opt_ints(max_new_per_industry)
    return out
