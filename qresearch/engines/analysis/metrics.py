"""Extended performance metrics: absolute, benchmark-relative, turnover, capacity."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np

from qresearch.config.models import ResearchConfig
from qresearch.engines.data.panel import PricePanel


def _as_date(v: object) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _daily_returns(navs: list[float]) -> list[float]:
    rets: list[float] = []
    for i in range(1, len(navs)):
        if navs[i - 1] > 0:
            rets.append(navs[i] / navs[i - 1] - 1.0)
    return rets


def _max_dd(navs: list[float]) -> float:
    if not navs:
        return 0.0
    peak = navs[0]
    max_dd = 0.0
    for v in navs:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1.0 if peak > 0 else 0.0)
    return max_dd


def absolute_metrics(equity: list[dict], trades: list[dict], starting_cash: float) -> dict[str, Any]:
    if not equity:
        return {
            "n_trades": 0,
            "n_sessions": 0,
            "total_return": 0.0,
            "ann_return": 0.0,
            "sharpe": 0.0,
            "max_dd": 0.0,
            "end_nav": starting_cash,
        }
    navs = [float(e["nav"]) for e in equity]
    rets = _daily_returns(navs)
    arr = np.asarray(rets, dtype=float)
    vol = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    mean = float(arr.mean()) if len(arr) else 0.0
    sharpe = (mean / vol * (252**0.5)) if vol > 1e-12 else 0.0
    total_return = navs[-1] / starting_cash - 1.0 if starting_cash else 0.0
    n_days = max(len(navs), 1)
    ann_return = (1.0 + total_return) ** (252 / n_days) - 1.0 if n_days > 1 else total_return
    n_trades = sum(1 for t in trades if t.get("side") == "buy")
    return {
        "n_trades": n_trades,
        "n_sessions": len(navs),
        "n_return_obs": len(rets),
        "total_return": total_return,
        "ann_return": ann_return,
        "sharpe": sharpe,
        "max_dd": _max_dd(navs),
        "end_nav": navs[-1],
    }


def benchmark_series(panel: PricePanel, instrument: str, sessions: list[date]) -> list[float | None]:
    """Daily close-to-close returns aligned to sessions (None if missing)."""
    out: list[float | None] = []
    prev_close: float | None = None
    for s in sessions:
        bar = panel.get(instrument, s)
        if bar is None:
            out.append(None)
            continue
        close = float(bar["close"])
        if prev_close is None or prev_close <= 0:
            out.append(None)
        else:
            out.append(close / prev_close - 1.0)
        prev_close = close
    return out


def relative_metrics(
    equity: list[dict],
    panel: PricePanel | None,
    benchmark: str | None,
) -> dict[str, Any]:
    empty = {
        "benchmark": benchmark,
        "benchmark_available": False,
        "excess_return": None,
        "ann_excess": None,
        "tracking_error": None,
        "information_ratio": None,
        "benchmark_total_return": None,
    }
    if not equity or panel is None or not benchmark:
        return empty
    sessions = [_as_date(e["session"]) for e in equity]
    navs = [float(e["nav"]) for e in equity]
    port_rets = _daily_returns(navs)
    # align: port_rets[i] corresponds to sessions[i+1]
    b_rets_full = benchmark_series(panel, benchmark, sessions)
    # first element of b_rets_full is often 0/None for first day; pair with port_rets
    paired_p: list[float] = []
    paired_b: list[float] = []
    for i, pr in enumerate(port_rets):
        br = b_rets_full[i + 1] if i + 1 < len(b_rets_full) else None
        if br is None:
            continue
        paired_p.append(pr)
        paired_b.append(float(br))
    if len(paired_p) < 2:
        # try if benchmark has any bars at all
        any_bar = any(panel.get(benchmark, s) is not None for s in sessions[:5])
        empty["benchmark_available"] = bool(any_bar)
        return empty

    p = np.asarray(paired_p, dtype=float)
    b = np.asarray(paired_b, dtype=float)
    excess = p - b
    te = float(excess.std(ddof=1)) if len(excess) > 1 else 0.0
    mean_x = float(excess.mean())
    ir = (mean_x / te * (252**0.5)) if te > 1e-12 else 0.0
    # compound approx via cumsum of log or product
    port_total = float(np.prod(1.0 + p) - 1.0)
    bench_total = float(np.prod(1.0 + b) - 1.0)
    excess_total = port_total - bench_total
    n = len(paired_p)
    ann_excess = (1.0 + excess_total) ** (252 / max(n, 1)) - 1.0 if n > 1 else excess_total
    return {
        "benchmark": benchmark,
        "benchmark_available": True,
        "excess_return": excess_total,
        "ann_excess": ann_excess,
        "tracking_error": te * (252**0.5),
        "information_ratio": ir,
        "benchmark_total_return": bench_total,
        "n_relative_obs": n,
    }


def turnover_metrics(equity: list[dict], trades: list[dict]) -> dict[str, Any]:
    if not equity:
        return {"avg_daily_turnover": 0.0, "ann_turnover": 0.0, "turnover_days": 0}
    nav_by_session: dict[str, float] = {str(e["session"]): float(e["nav"]) for e in equity}
    notional_by_session: dict[str, float] = {}
    for t in trades:
        sess = str(t.get("session"))
        px = float(t.get("price") or 0.0)
        qty = float(t.get("qty") or 0.0)
        notional_by_session[sess] = notional_by_session.get(sess, 0.0) + abs(px * qty)
    turnovers: list[float] = []
    for sess, notion in notional_by_session.items():
        nav = nav_by_session.get(sess)
        if nav and nav > 0:
            turnovers.append(notion / nav)
    avg = float(np.mean(turnovers)) if turnovers else 0.0
    # annualize average daily turnover (sum of traded/NAV per active day) * 252 / n_sessions
    n_sess = max(len(equity), 1)
    # more standard: mean over all calendar sessions (0 on quiet days)
    daily = []
    for e in equity:
        sess = str(e["session"])
        nav = float(e["nav"])
        notion = notional_by_session.get(sess, 0.0)
        daily.append(notion / nav if nav > 0 else 0.0)
    avg_all = float(np.mean(daily)) if daily else 0.0
    return {
        "avg_daily_turnover": avg_all,
        "ann_turnover": avg_all * 252.0,
        "turnover_days": len(turnovers),
        "avg_turnover_on_trade_days": avg,
    }


def _adv_for_trade(panel: PricePanel, instrument: str, session: date, lookback: int = 20) -> float | None:
    try:
        idx = panel.calendar.index(session)
    except ValueError:
        return None
    start = max(0, idx - lookback)
    vals: list[float] = []
    for d in panel.calendar[start:idx]:
        bar = panel.get(instrument, d)
        if bar is None:
            continue
        amt = bar.get("amount")
        if amt is not None and float(amt) > 0:
            # tushare amount often in 千元; treat as relative proxy anyway
            vals.append(float(amt))
            continue
        vol = bar.get("vol")
        close = bar.get("close")
        if vol is not None and close is not None and float(vol) > 0:
            vals.append(float(vol) * float(close))
    if not vals:
        return None
    return float(np.median(vals))


def capacity_metrics(
    trades: list[dict],
    panel: PricePanel | None,
    *,
    lookback: int = 20,
) -> dict[str, Any]:
    if panel is None or not trades:
        return {"capacity": "unavailable", "median_participation": None, "n_capacity_samples": 0}
    parts: list[float] = []
    for t in trades:
        if t.get("side") not in ("buy", "sell"):
            continue
        inst = str(t.get("instrument"))
        sess = _as_date(t["session"])
        notion = abs(float(t.get("price") or 0.0) * float(t.get("qty") or 0.0))
        adv = _adv_for_trade(panel, inst, sess, lookback=lookback)
        if adv is None or adv <= 0:
            continue
        parts.append(notion / adv)
    if not parts:
        return {"capacity": "unavailable", "median_participation": None, "n_capacity_samples": 0}
    arr = np.asarray(parts, dtype=float)
    return {
        "capacity": "heuristic_adv",
        "median_participation": float(np.median(arr)),
        "p90_participation": float(np.quantile(arr, 0.90)),
        "mean_participation": float(arr.mean()),
        "n_capacity_samples": int(len(parts)),
        "adv_lookback_sessions": lookback,
    }


def compute_extended_metrics(
    equity: list[dict],
    trades: list[dict],
    starting_cash: float,
    *,
    panel: PricePanel | None = None,
    config: ResearchConfig | None = None,
) -> dict[str, Any]:
    base = absolute_metrics(equity, trades, starting_cash)
    bench = config.benchmark.instrument if config else None
    rel = relative_metrics(equity, panel, bench)
    turn = turnover_metrics(equity, trades)
    cap = capacity_metrics(trades, panel)
    out = {**base, **rel, **turn, **cap}
    return out
