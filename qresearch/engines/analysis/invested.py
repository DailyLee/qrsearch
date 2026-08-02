"""Average invested fraction from equity curve: 1 - cash/nav."""

from __future__ import annotations

from typing import Any

import polars as pl


def _equity_rows(equity: list[dict[str, Any]] | pl.DataFrame | None) -> list[dict[str, Any]]:
    if equity is None:
        return []
    if isinstance(equity, pl.DataFrame):
        return equity.to_dicts()
    return list(equity)


def session_invested(row: dict[str, Any]) -> float | None:
    """Invested fraction for one equity row: 1 - cash/nav (clipped to [0, 1])."""
    try:
        nav = float(row.get("nav"))
        cash = float(row.get("cash"))
    except (TypeError, ValueError):
        return None
    if nav <= 0:
        return None
    inv = 1.0 - cash / nav
    if inv < 0.0:
        return 0.0
    if inv > 1.0:
        return 1.0
    return inv


def mean_invested_from_equity(
    equity: list[dict[str, Any]] | pl.DataFrame | None,
    *,
    empty_eps: float = 1e-9,
) -> dict[str, Any]:
    """Summarize invested / empty-cash days from equity CSV rows."""
    rows = _equity_rows(equity)
    invested: list[float] = []
    for r in rows:
        inv = session_invested(r)
        if inv is not None:
            invested.append(inv)
    n = len(invested)
    if n == 0:
        return {
            "mean_invested": None,
            "empty_cash_share": None,
            "n_sessions": 0,
            "definition": "1 - cash/nav",
        }
    empty = sum(1 for v in invested if v <= empty_eps)
    return {
        "mean_invested": sum(invested) / n,
        "empty_cash_share": empty / n,
        "n_sessions": n,
        "definition": "1 - cash/nav",
    }
