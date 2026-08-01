"""Deflated Sharpe and trial-count helpers (Bailey & López de Prado approximate)."""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any

EULER_MASCHERONI = 0.5772156649015329


def sharpe_variance(sr: float, n_obs: int, skew: float = 0.0, kurt: float = 3.0) -> float:
    if n_obs <= 1:
        return float("inf")
    return (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr) / (n_obs - 1)


def expected_max_sr_null(n_trials: int, sr_var: float) -> float:
    """Expected maximum Sharpe under the null given n independent trials."""
    n_trials = max(int(n_trials), 1)
    if n_trials <= 1 or not math.isfinite(sr_var) or sr_var <= 0:
        return 0.0
    nd = NormalDist()
    z1 = nd.inv_cdf(1.0 - 1.0 / n_trials)
    z2 = nd.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(sr_var) * ((1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2)


def deflated_sharpe(
    sharpe: float,
    n_obs: int,
    n_trials: int = 1,
    *,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> dict[str, Any]:
    """
    Practical DSR-style adjustment in the same units as `sharpe`.

    Returns point deflated Sharpe (observed - null max) and DSR probability
    that true SR exceeds the selection-adjusted null.
    """
    n_trials = max(int(n_trials), 1)
    n_obs = max(int(n_obs), 2)
    sr = float(sharpe)
    sr_var = sharpe_variance(sr, n_obs, skew=skew, kurt=kurt)
    sr_star = expected_max_sr_null(n_trials, sr_var)
    se = math.sqrt(sr_var) if math.isfinite(sr_var) and sr_var > 0 else 1.0
    dsr_point = sr - sr_star
    dsr_prob = NormalDist().cdf((sr - sr_star) / se)
    return {
        "observed_sharpe": sr,
        "n_obs": n_obs,
        "n_trials": n_trials,
        "skew": skew,
        "kurt": kurt,
        "sr_null_max": sr_star,
        "deflated_sharpe": dsr_point,
        "dsr_prob": dsr_prob,
        "method": "bailey_lopez_de_prado_approx",
        "note": "Applied in same units as observed Sharpe (often annualized); V1 approximation.",
    }


def attach_overfit_metrics(
    metrics: dict[str, Any],
    *,
    n_trials: int = 1,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> dict[str, Any]:
    n_obs = int(metrics.get("n_return_obs") or max(int(metrics.get("n_sessions") or 2) - 1, 2))
    info = deflated_sharpe(
        float(metrics.get("sharpe") or 0.0),
        n_obs=n_obs,
        n_trials=n_trials,
        skew=skew,
        kurt=kurt,
    )
    out = dict(metrics)
    out.update(
        {
            "n_trials": info["n_trials"],
            "deflated_sharpe": info["deflated_sharpe"],
            "dsr_prob": info["dsr_prob"],
            "sr_null_max": info["sr_null_max"],
            "overfit": info,
        }
    )
    return out
