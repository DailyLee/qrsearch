"""Read-only trade / equity / reject diagnostics for a finished run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from qresearch.engines.analysis.invested import mean_invested_from_equity
from qresearch.engines.analysis.report import summarize_rejects, summarize_trades


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def exit_reason_pnl_groups(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sells = [r for r in trades if str(r.get("side")) == "sell"]
    by: dict[str, list[float]] = {}
    for r in sells:
        reason = str(r.get("reason") or "unknown")
        p = _safe_float(r.get("pnl"))
        if p is None:
            continue
        by.setdefault(reason, []).append(p)
    rows: list[dict[str, Any]] = []
    for reason, pnls in sorted(by.items(), key=lambda kv: -len(kv[1])):
        wins = [p for p in pnls if p > 0]
        rows.append(
            {
                "reason": reason,
                "n": len(pnls),
                "win_rate": (len(wins) / len(pnls)) if pnls else 0.0,
                "avg_pnl": (sum(pnls) / len(pnls)) if pnls else 0.0,
            }
        )
    return rows


def analyze_trades_run(run_dir: Path, *, role: str | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    if not run_dir.exists():
        raise FileNotFoundError(f"missing run dir {run_dir}")

    art = run_dir / "artifacts"
    if role is not None:
        art = art / "backtests" / role
    trades_path = art / "trades.csv"
    equity_path = art / "equity.csv"
    rejects_path = art / "rejects_summary.json"
    yearly_path = art / "yearly_metrics.json"
    metrics_path = art / "metrics.json"

    trades_rows: list[dict[str, Any]] = []
    if trades_path.exists():
        trades_rows = pl.read_csv(trades_path).to_dicts()

    trade_stats = summarize_trades(trades_rows)
    by_exit = exit_reason_pnl_groups(trades_rows)

    equity_rows: list[dict[str, Any]] = []
    if equity_path.exists():
        equity_rows = pl.read_csv(equity_path).to_dicts()
    invested = mean_invested_from_equity(equity_rows)

    rejects_raw: list[dict[str, Any]] | None = None
    if rejects_path.exists():
        raw = json.loads(rejects_path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            rejects_raw = raw
        elif isinstance(raw, dict) and "by_reason" in raw:
            # already summarized
            reject_stats = raw
            rejects_raw = None
        else:
            rejects_raw = []
    if rejects_raw is not None:
        reject_stats = summarize_rejects(rejects_raw)
    elif rejects_path.exists():
        reject_stats = json.loads(rejects_path.read_text(encoding="utf-8"))
    else:
        reject_stats = {"total": 0, "by_reason": []}

    # Top reject reasons
    by_reason = list(reject_stats.get("by_reason") or [])
    reject_top = by_reason[:10]

    yearly: Any = None
    if yearly_path.exists():
        yearly = json.loads(yearly_path.read_text(encoding="utf-8"))
    elif metrics_path.exists():
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        yearly = metrics.get("yearly")

    # compact yearly for envelope
    yearly_summary: list[dict[str, Any]] = []
    if isinstance(yearly, dict):
        for y, m in sorted(yearly.items()):
            if isinstance(m, dict):
                yearly_summary.append(
                    {
                        "year": y,
                        "ann_return": m.get("ann_return") or m.get("total_return"),
                        "sharpe": m.get("sharpe"),
                        "excess_return": m.get("excess_return") or m.get("excess"),
                    }
                )
    elif isinstance(yearly, list):
        yearly_summary = yearly

    diag = {
        "trade_stats": trade_stats,
        "exit_reason_groups": by_exit,
        "exit_reasons": (trade_stats or {}).get("exit_reasons") if trade_stats else [],
        "mean_invested": invested.get("mean_invested"),
        "empty_cash_share": invested.get("empty_cash_share"),
        "invested_definition": invested.get("definition"),
        "n_equity_sessions": invested.get("n_sessions"),
        "reject_top": reject_top,
        "rejects_total": reject_stats.get("total"),
        "yearly": yearly_summary,
    }
    out_path = art / "trades_diagnostics.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(diag, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {
        "summary": diag,
        "artifacts": {"trades_diagnostics": str(out_path)},
    }
