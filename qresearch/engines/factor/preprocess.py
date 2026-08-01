"""Decoupled factor preprocessing: winsorize → industry/size neutral → z-score.

Pure transform: appends `features.<name><suffix>` columns; never mutates IC/backtest kernels.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Callable

import numpy as np
import polars as pl

if TYPE_CHECKING:
    from qresearch.config.models import FactorPreprocessConfig

StepFn = Callable[[np.ndarray, dict[str, Any]], np.ndarray]


def _as_float_array(values: list[Any]) -> np.ndarray:
    out = np.empty(len(values), dtype=float)
    for i, v in enumerate(values):
        if v is None:
            out[i] = np.nan
            continue
        try:
            out[i] = float(v)
        except (TypeError, ValueError):
            out[i] = np.nan
    return out


def winsorize_series(x: np.ndarray, *, q: float = 0.01) -> np.ndarray:
    """Two-sided quantile winsorize; NaNs preserved."""
    y = x.copy()
    m = np.isfinite(y)
    if m.sum() < 3:
        return y
    lo, hi = np.quantile(y[m], [q, 1.0 - q])
    y[m] = np.clip(y[m], lo, hi)
    return y


def zscore_series(x: np.ndarray) -> np.ndarray:
    y = x.copy()
    m = np.isfinite(y)
    if m.sum() < 2:
        return y
    mu = float(np.mean(y[m]))
    sigma = float(np.std(y[m], ddof=0))
    if sigma < 1e-12:
        y[m] = 0.0
        return y
    y[m] = (y[m] - mu) / sigma
    return y


def industry_neutralize(x: np.ndarray, industry: np.ndarray) -> np.ndarray:
    """Within-industry demean; missing industry → NaN."""
    y = np.full_like(x, np.nan, dtype=float)
    # industry labels as object/str
    labels = np.asarray(industry, dtype=object)
    for lab in set(labels.tolist()):
        if lab is None or (isinstance(lab, float) and np.isnan(lab)):
            continue
        idx = np.array([i for i, v in enumerate(labels) if v == lab], dtype=int)
        if len(idx) == 0:
            continue
        vals = x[idx]
        m = np.isfinite(vals)
        if m.sum() == 0:
            continue
        mu = float(np.mean(vals[m]))
        out = vals.copy()
        out[m] = vals[m] - mu
        out[~m] = np.nan
        y[idx] = out
    # rows with missing industry stay NaN
    return y


def size_neutralize(x: np.ndarray, size: np.ndarray) -> np.ndarray:
    """OLS residual of x on log1p(size); NaNs dropped from fit, preserved in output."""
    y = np.full_like(x, np.nan, dtype=float)
    sx = np.asarray(size, dtype=float)
    log_s = np.log1p(np.clip(sx, a_min=0.0, a_max=None))
    m = np.isfinite(x) & np.isfinite(log_s)
    if m.sum() < 3:
        return y
    A = np.column_stack([np.ones(int(m.sum())), log_s[m]])
    try:
        coef, _, _, _ = np.linalg.lstsq(A, x[m], rcond=None)
    except np.linalg.LinAlgError:
        return y
    y[m] = x[m] - (A @ coef)
    return y


def _group_indices(events: pl.DataFrame, cross_section: str) -> list[np.ndarray]:
    n = events.height
    if cross_section == "all" or n == 0:
        return [np.arange(n, dtype=int)]
    # date
    dates = events["entry_intent_date"].to_list()
    buckets: dict[Any, list[int]] = {}
    for i, d in enumerate(dates):
        key = d.isoformat() if isinstance(d, date) else str(d)[:10]
        buckets.setdefault(key, []).append(i)
    return [np.asarray(ix, dtype=int) for ix in buckets.values()]


def _apply_step_on_groups(
    values: np.ndarray,
    groups: list[np.ndarray],
    *,
    min_group_size: int,
    step: str,
    industry: np.ndarray | None,
    size: np.ndarray | None,
    winsorize_q: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    out = np.full_like(values, np.nan, dtype=float)
    skipped_small = 0
    applied = 0
    for g in groups:
        if len(g) < min_group_size and min_group_size > 0 and len(groups) > 1:
            # date mode: skip tiny groups
            skipped_small += 1
            continue
        xv = values[g]
        if step == "winsorize":
            out[g] = winsorize_series(xv, q=winsorize_q)
        elif step == "industry_neutral":
            if industry is None:
                out[g] = xv
            else:
                out[g] = industry_neutralize(xv, industry[g])
        elif step == "size_neutral":
            if size is None:
                out[g] = xv
            else:
                out[g] = size_neutralize(xv, size[g])
        elif step == "zscore":
            out[g] = zscore_series(xv)
        else:
            raise ValueError(f"unknown preprocess step: {step}")
        applied += 1
    meta = {
        "step": step,
        "groups_applied": applied,
        "groups_skipped_small": skipped_small,
        "n_finite": int(np.isfinite(out).sum()),
    }
    return out, meta


def prepared_col_name(feature: str, suffix: str) -> str:
    return f"{feature}{suffix}"


def apply_factor_preprocess(
    events: pl.DataFrame,
    feature_cols: list[str],
    cfg: FactorPreprocessConfig,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Append prepared columns for each feature; originals unchanged.

    Returns (events_with_cols, report).
    If cfg.enabled is False, returns events unchanged with a skipped report.
    """
    report: dict[str, Any] = {
        "enabled": bool(cfg.enabled),
        "steps": list(cfg.steps),
        "cross_section": cfg.cross_section,
        "suffix": cfg.suffix,
        "input_features": list(feature_cols),
        "output_features": [],
        "per_feature": [],
        "warnings": [],
    }
    if not cfg.enabled:
        report["skipped"] = "preprocess_disabled"
        return events, report
    if not feature_cols:
        report["skipped"] = "no_features"
        return events, report

    industry = None
    if cfg.industry_field in events.columns:
        industry = np.asarray(events.get_column(cfg.industry_field).to_list(), dtype=object)
    elif "industry_neutral" in cfg.steps:
        report["warnings"].append(f"missing_industry_field:{cfg.industry_field}")

    size = None
    if cfg.size_field in events.columns:
        size = _as_float_array(events.get_column(cfg.size_field).to_list())
    elif "size_neutral" in cfg.steps:
        report["warnings"].append(f"missing_size_field:{cfg.size_field}")

    groups = _group_indices(events, cfg.cross_section)
    min_gs = int(cfg.min_group_size) if cfg.cross_section == "date" else 0

    out = events
    new_cols: dict[str, list[float | None]] = {}
    for feat in feature_cols:
        if feat not in events.columns:
            report["warnings"].append(f"missing_feature:{feat}")
            continue
        vals = _as_float_array(events.get_column(feat).to_list())
        step_metas: list[dict[str, Any]] = []
        cur = vals
        for step in cfg.steps:
            cur, meta = _apply_step_on_groups(
                cur,
                groups,
                min_group_size=min_gs,
                step=step,
                industry=industry,
                size=size,
                winsorize_q=float(cfg.winsorize_q),
            )
            step_metas.append(meta)
        col = prepared_col_name(feat, cfg.suffix)
        new_cols[col] = [None if not np.isfinite(v) else float(v) for v in cur]
        report["output_features"].append(col)
        report["per_feature"].append(
            {
                "feature": feat,
                "output": col,
                "n_finite": int(np.isfinite(cur).sum()),
                "steps": step_metas,
            }
        )

    if new_cols:
        out = out.hstack(pl.DataFrame(new_cols))
    return out, report
