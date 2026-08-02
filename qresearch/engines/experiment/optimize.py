"""Direction-aware signal threshold search (empirical quantile grid + WF)."""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import polars as pl

from qresearch.config.models import FilterRule, ResearchConfig
from qresearch.engines.backtest.session import run_backtest
from qresearch.engines.data.panel import PricePanel
from qresearch.engines.experiment.walkforward import run_walk_forward
from qresearch.engines.signal.engine import build_ranked

Side = Literal["high", "low"]
_POSITIVE = frozenset({"positive", "+", "pos", "high"})
_NEGATIVE = frozenset({"negative", "-", "neg", "low"})


class OptimizeError(ValueError):
    """Invalid optimize inputs (missing side/feature, empty column, etc.)."""


def parse_keep_fracs(spec: str | list[float] | None) -> list[float]:
    if spec is None:
        return [0.1, 0.2, 0.3, 0.4]
    if isinstance(spec, list):
        out = [float(x) for x in spec]
    else:
        out = [float(p.strip()) for p in str(spec).split(",") if p.strip()]
    cleaned: list[float] = []
    for x in out:
        if not (0.0 < x < 1.0):
            raise OptimizeError(f"keep_frac must be in (0,1), got {x}")
        cleaned.append(x)
    if not cleaned:
        raise OptimizeError("keep_frac grid is empty")
    return cleaned


def resolve_feature(config: ResearchConfig, feature: str | None) -> str:
    if feature and str(feature).strip():
        return str(feature).strip()
    rank = list(config.signals.rank_by or [])
    if len(rank) == 1:
        return rank[0].field
    signs = dict(config.hypothesis.expected_sign or {})
    if len(signs) == 1:
        return next(iter(signs.keys()))
    raise OptimizeError(
        "feature required: pass --feature, or set a single rank_by / expected_sign entry"
    )


def resolve_side(config: ResearchConfig, feature: str, side: str) -> Side:
    s = (side or "auto").strip().lower()
    if s in ("high", "low"):
        return s  # type: ignore[return-value]
    if s != "auto":
        raise OptimizeError(f"side must be high|low|auto, got {side!r}")

    signs = dict(config.hypothesis.expected_sign or {})
    raw = signs.get(feature)
    if raw is None and feature.startswith("features."):
        raw = signs.get(feature[len("features.") :])
    if raw is None and not feature.startswith("features."):
        raw = signs.get(f"features.{feature}")
    if raw is not None:
        key = str(raw).strip().lower()
        if key in _POSITIVE:
            return "high"
        if key in _NEGATIVE:
            return "low"
        raise OptimizeError(
            f"expected_sign[{feature}]={raw!r} not in positive/negative; pass --side"
        )

    for rb in config.signals.rank_by or []:
        if rb.field == feature:
            return "low" if rb.ascending else "high"

    raise OptimizeError(
        f"cannot resolve side for {feature}: set hypothesis.expected_sign, "
        f"rank_by.ascending, or pass --side high|low"
    )


def _feature_values(events: pl.DataFrame, feature: str) -> np.ndarray:
    if feature not in events.columns:
        raise OptimizeError(f"feature column missing in events: {feature}")
    s = events.get_column(feature).cast(pl.Float64, strict=False).drop_nulls()
    arr = s.to_numpy()
    arr = arr[np.isfinite(arr)]
    if arr.size < 5:
        raise OptimizeError(f"not enough finite values for quantile on {feature}")
    return arr


def threshold_for_keep(
    values: np.ndarray, *, side: Side, keep_frac: float
) -> tuple[str, float]:
    """Return (op, threshold) so that roughly keep_frac of mass is kept on the chosen side."""
    if side == "high":
        # keep top keep_frac → ge at (1 - keep_frac) empirical quantile
        thr = float(np.quantile(values, 1.0 - keep_frac))
        return "ge", thr
    thr = float(np.quantile(values, keep_frac))
    return "le", thr


def replace_feature_filter(
    filters: list[FilterRule], feature: str, op: str, value: float
) -> list[FilterRule]:
    others = [f for f in filters if f.field != feature]
    others.append(FilterRule(field=feature, op=op, value=value))  # type: ignore[arg-type]
    return others


def _score_config(
    events: pl.DataFrame,
    panel: PricePanel,
    cfg: ResearchConfig,
) -> tuple[float, int, str]:
    years = {str(d)[:4] for d in events["entry_intent_date"].to_list()}
    mode = "walk_forward" if len(years) >= 2 else "full_sample"
    if mode == "walk_forward":
        wf = run_walk_forward(events, panel, cfg)
        score = float(wf["aggregate"]["sharpe"])
        n_trades = int(wf["aggregate"].get("total_trades") or 0)
    else:
        ranked = build_ranked(events, cfg)
        res = run_backtest(ranked, panel, cfg)
        score = float(res.metrics.get("sharpe") or 0.0)
        n_trades = int(res.metrics.get("n_trades") or 0)
    if n_trades < int(cfg.walk_forward.min_trades):
        score = -1e6
    return score, n_trades, mode


def run_signal_threshold_search(
    events: pl.DataFrame,
    panel: PricePanel,
    base_config: ResearchConfig,
    *,
    feature: str | None = None,
    side: str = "auto",
    keep_fracs: str | list[float] | None = None,
    max_grid: int | None = None,
) -> dict[str, Any]:
    """Enumerate keep_frac thresholds for one feature; direction from side/expected_sign/rank_by."""
    feat = resolve_feature(base_config, feature)
    resolved_side = resolve_side(base_config, feat, side)
    fracs = parse_keep_fracs(keep_fracs)
    if max_grid is not None and max_grid > 0:
        fracs = fracs[: int(max_grid)]
    values = _feature_values(events, feat)

    trials_log: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None

    for i, kf in enumerate(fracs):
        op, thr = threshold_for_keep(values, side=resolved_side, keep_frac=kf)
        cfg = base_config.model_copy(deep=True)
        cfg.signals.filters = replace_feature_filter(
            list(cfg.signals.filters or []), feat, op, thr
        )
        score, n_trades, mode = _score_config(events, panel, cfg)
        row = {
            "number": i,
            "params": {
                "feature": feat,
                "side": resolved_side,
                "keep_frac": kf,
                "threshold": thr,
                "op": op,
            },
            "value": score,
            "n_trades": n_trades,
            "mode": mode,
        }
        trials_log.append(row)
        if best is None or score > float(best["value"]):
            best = row

    assert best is not None
    return {
        "best_params": dict(best["params"]),
        "best_value": best["value"],
        "trials": trials_log,
        "n_grid": len(trials_log),
        "feature": feat,
        "side": resolved_side,
        "method": "signal_quantile_grid",
    }


def run_optuna(
    events: pl.DataFrame,
    panel: PricePanel,
    base_config: ResearchConfig,
    *,
    n_trials: int = 20,
    feature: str = "features.box_quality",
    study_name: str = "qresearch",
    side: str = "auto",
    keep_fracs: str | list[float] | None = None,
) -> dict[str, Any]:
    """Backward-compatible entry: quantile grid (n_trials caps grid size)."""
    _ = study_name
    return run_signal_threshold_search(
        events,
        panel,
        base_config,
        feature=feature,
        side=side,
        keep_fracs=keep_fracs,
        max_grid=n_trials,
    )
