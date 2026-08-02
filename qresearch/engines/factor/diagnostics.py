"""Factor-compare extras: correlation, quantile monotonicity, near-constant rejects."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl


def reject_near_constant_features(
    events: pl.DataFrame,
    features: list[str],
    *,
    min_unique: int = 3,
    min_std: float = 1e-12,
) -> list[dict[str, Any]]:
    rejected: list[dict[str, Any]] = []
    for feat in features:
        if feat not in events.columns:
            rejected.append({"feature": feat, "reason": "missing_column"})
            continue
        s = events[feat].drop_nulls()
        if len(s) == 0:
            rejected.append({"feature": feat, "reason": "all_null"})
            continue
        n_unique = s.n_unique()
        if n_unique < min_unique:
            rejected.append(
                {"feature": feat, "reason": f"n_unique<{min_unique}", "n_unique": int(n_unique)}
            )
            continue
        try:
            std = float(s.std())
        except Exception:
            std = float("nan")
        if not np.isfinite(std) or abs(std) < min_std:
            rejected.append({"feature": feat, "reason": "near_zero_std", "std": std})
    return rejected


def feature_corr_matrix(
    events: pl.DataFrame,
    features: list[str],
    *,
    method: str = "spearman",
) -> pl.DataFrame:
    """Pairwise correlation; returns long-form feature_i, feature_j, corr."""
    cols = [f for f in features if f in events.columns]
    if len(cols) < 2:
        return pl.DataFrame(
            schema={"feature_i": pl.Utf8, "feature_j": pl.Utf8, "corr": pl.Float64, "method": pl.Utf8}
        )
    df = events.select(cols).drop_nulls()
    if df.height < 3:
        return pl.DataFrame(
            schema={"feature_i": pl.Utf8, "feature_j": pl.Utf8, "corr": pl.Float64, "method": pl.Utf8}
        )
    mat = df.to_numpy()
    # rank for spearman
    if method == "spearman":
        ranked = np.zeros_like(mat, dtype=float)
        for j in range(mat.shape[1]):
            order = mat[:, j].argsort(kind="mergesort")
            ranks = np.empty(len(order), dtype=float)
            ranks[order] = np.arange(1, len(order) + 1, dtype=float)
            ranked[:, j] = ranks
        use = ranked
    else:
        use = mat.astype(float)
    c = np.corrcoef(use, rowvar=False)
    rows: list[dict[str, Any]] = []
    for i, fi in enumerate(cols):
        for j, fj in enumerate(cols):
            if i >= j:
                continue
            val = float(c[i, j]) if np.isfinite(c[i, j]) else float("nan")
            rows.append({"feature_i": fi, "feature_j": fj, "corr": val, "method": method})
    return pl.DataFrame(rows) if rows else pl.DataFrame(
        schema={"feature_i": pl.Utf8, "feature_j": pl.Utf8, "corr": pl.Float64, "method": pl.Utf8}
    )


def corr_top_pairs(corr_df: pl.DataFrame, *, top_n: int = 10) -> list[dict[str, Any]]:
    if corr_df is None or corr_df.height == 0:
        return []
    sub = corr_df.drop_nulls(subset=["corr"]).with_columns(pl.col("corr").abs().alias("_a"))
    return (
        sub.sort("_a", descending=True)
        .drop("_a")
        .head(top_n)
        .to_dicts()
    )


def _pick_value_col(quant_df: pl.DataFrame, preferred: str | None) -> str:
    """Prefer mean_excess when mostly finite; else mean_fwd_ret / preferred."""
    if preferred and preferred not in ("mean_fwd_ret", "auto") and preferred in quant_df.columns:
        return preferred
    if "mean_excess" in quant_df.columns:
        xs = quant_df["mean_excess"].drop_nulls()
        if len(xs) >= 2:
            arr = np.asarray(xs.to_list(), dtype=float)
            if np.isfinite(arr).sum() >= max(2, int(0.5 * len(arr))):
                return "mean_excess"
    if "mean_fwd_ret" in quant_df.columns:
        return "mean_fwd_ret"
    if preferred and preferred in quant_df.columns:
        return preferred
    return "mean_fwd_ret"


def _shape_from_vals(vals: list[float], mono_score: float) -> tuple[str, str]:
    """Return (monotonic_compat, shape). monotonic stays up|down|weak|n/a."""
    if len(vals) < 2:
        return "n/a", "n/a"
    if mono_score >= 0.75:
        return "up", "mono_up"
    if mono_score <= -0.75:
        return "down", "mono_down"
    if len(vals) < 4:
        return "weak", "weak"
    arr = np.asarray(vals, dtype=float)
    n = len(arr)
    imin = int(np.argmin(arr))
    imax = int(np.argmax(arr))
    pos_min = imin / (n - 1)
    pos_max = imax / (n - 1)
    left, right = float(arr[0]), float(arr[-1])
    rng = float(arr.max() - arr.min())
    if rng <= 1e-15:
        return "weak", "weak"
    # U: valley mid, both ends elevated
    if 0.25 <= pos_min <= 0.75:
        valley = float(arr[imin])
        if (
            left > valley
            and right > valley
            and (left - valley) / rng >= 0.15
            and (right - valley) / rng >= 0.15
        ):
            return "weak", "u"
    # inv_u / hump: peak mid, ends lower
    if 0.25 <= pos_max <= 0.75:
        peak = float(arr[imax])
        if (
            left < peak
            and right < peak
            and (peak - left) / rng >= 0.15
            and (peak - right) / rng >= 0.15
        ):
            if 0.4 <= pos_max <= 0.6:
                return "weak", "inv_u"
            return "weak", "hump"
    return "weak", "weak"


def quantile_monotonicity(
    quant_df: pl.DataFrame,
    *,
    value_col: str | None = None,
) -> list[dict[str, Any]]:
    """Per-feature mono_score, monotonic (compat), and shape (band-routing)."""
    if quant_df is None or quant_df.height == 0 or "feature" not in quant_df.columns:
        return []
    col = _pick_value_col(quant_df, value_col or "mean_fwd_ret")
    if col not in quant_df.columns:
        return []
    out: list[dict[str, Any]] = []
    for feat in quant_df["feature"].unique().to_list():
        sub = (
            quant_df.filter(pl.col("feature") == feat)
            .sort("quantile")
            .select(["quantile", col])
        )
        vals = [
            float(v) for v in sub[col].to_list() if v is not None and np.isfinite(float(v))
        ]
        if len(vals) < 2:
            out.append(
                {
                    "feature": feat,
                    "mono_score": None,
                    "monotonic": "n/a",
                    "shape": "n/a",
                    "n_quantiles": len(vals),
                    "value_col": col,
                }
            )
            continue
        diffs = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
        pos = sum(1 for d in diffs if d > 0)
        neg = sum(1 for d in diffs if d < 0)
        n = len(diffs)
        score = (pos - neg) / n if n else 0.0
        mono_label, shape = _shape_from_vals(vals, score)
        out.append(
            {
                "feature": feat,
                "mono_score": float(score),
                "monotonic": mono_label,
                "shape": shape,
                "n_quantiles": len(vals),
                "value_col": col,
            }
        )
    return out
