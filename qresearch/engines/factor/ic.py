"""Event-level factor IC utilities."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl

from qresearch.engines.data.panel import PricePanel


def _fwd_return(panel: PricePanel, instrument: str, entry: date, horizon: int) -> float | None:
    """Holding-period return with both legs qfq-adjusted as-of the exit session (PIT)."""
    if panel.get(instrument, entry) is None:
        nxt = panel.next_session(entry, 0)
        if nxt is None:
            return None
        entry = nxt
        if panel.get(instrument, entry) is None:
            return None
    end = panel.next_session(entry, horizon)
    if end is None:
        return None
    # asof=end so corporate actions between entry and end are included without window-end peek
    bar0 = panel.get(instrument, entry, asof=end)
    bar1 = panel.get(instrument, end, asof=end)
    if bar0 is None or bar1 is None:
        return None
    c0 = float(bar0["close"])
    c1 = float(bar1["close"])
    if c0 == 0:
        return None
    return c1 / c0 - 1.0


def spearman_ic(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return float("nan")
    rx = x.argsort().argsort().astype(float)
    ry = y.argsort().argsort().astype(float)
    rx -= rx.mean()
    ry -= ry.mean()
    denom = np.sqrt((rx**2).sum() * (ry**2).sum())
    if denom == 0:
        return float("nan")
    return float((rx * ry).sum() / denom)


def _ols_alpha_beta(y: np.ndarray, x: np.ndarray) -> tuple[float, float, float]:
    """Return (alpha, beta, alpha_tstat) for y = alpha + beta * x."""
    if len(y) < 3:
        return float("nan"), float("nan"), float("nan")
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    # drop non-finite
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(y) < 3:
        return float("nan"), float("nan"), float("nan")
    A = np.column_stack([np.ones(len(x)), x])
    try:
        coef, _, _, _ = np.linalg.lstsq(A, y, rcond=None)
    except np.linalg.LinAlgError:
        return float("nan"), float("nan"), float("nan")
    alpha, beta = float(coef[0]), float(coef[1])
    resid = y - A @ coef
    dof = max(len(y) - 2, 1)
    sse = float((resid**2).sum())
    sigma2 = sse / dof
    xtx_inv = np.linalg.pinv(A.T @ A)
    se_alpha = float(np.sqrt(max(sigma2 * xtx_inv[0, 0], 0.0)))
    tstat = alpha / se_alpha if se_alpha > 1e-12 else float("nan")
    return alpha, beta, tstat


def compute_ic_table(
    events: pl.DataFrame,
    panel: PricePanel,
    feature_cols: list[str],
    horizons: list[int],
) -> pl.DataFrame:
    rows = []
    for feat in feature_cols:
        if feat not in events.columns:
            continue
        xs = []
        rets: dict[int, list[float]] = {h: [] for h in horizons}
        xs_h: dict[int, list[float]] = {h: [] for h in horizons}
        for r in events.iter_rows(named=True):
            fv = r.get(feat)
            if fv is None:
                continue
            entry = r["entry_intent_date"]
            if not isinstance(entry, date):
                entry = date.fromisoformat(str(entry))
            for h in horizons:
                ret = _fwd_return(panel, r["instrument"], entry, h)
                if ret is None:
                    continue
                xs_h[h].append(float(fv))
                rets[h].append(ret)
        for h in horizons:
            arr_x = np.asarray(xs_h[h], dtype=float)
            arr_y = np.asarray(rets[h], dtype=float)
            ic = spearman_ic(arr_x, arr_y) if len(arr_x) else float("nan")
            rows.append(
                {
                    "feature": feat,
                    "horizon": h,
                    "n": int(len(arr_x)),
                    "rank_ic": ic,
                }
            )
    return pl.DataFrame(rows) if rows else pl.DataFrame(
        schema={"feature": pl.Utf8, "horizon": pl.Int64, "n": pl.Int64, "rank_ic": pl.Float64}
    )


def shuffle_date_ic(
    events: pl.DataFrame,
    panel: PricePanel,
    feature: str,
    horizon: int,
    seed: int = 42,
) -> tuple[float, float]:
    """Return (ic_original, ic_shuffled) with only dates permuted."""
    rng = np.random.default_rng(seed)
    base = events.select(["instrument", "entry_intent_date", feature]).drop_nulls()
    dates = base["entry_intent_date"].to_list()
    shuffled = list(dates)
    rng.shuffle(shuffled)
    orig_ic = compute_ic_table(base, panel, [feature], [horizon])
    shuf = base.with_columns(pl.Series("entry_intent_date", shuffled))
    shuf_ic = compute_ic_table(shuf, panel, [feature], [horizon])
    o = float(orig_ic["rank_ic"][0]) if orig_ic.height else float("nan")
    s = float(shuf_ic["rank_ic"][0]) if shuf_ic.height else float("nan")
    return o, s


def _entry_year(entry: object) -> int:
    if isinstance(entry, date):
        return entry.year
    return date.fromisoformat(str(entry)[:10]).year


def compute_icir_table(
    events: pl.DataFrame,
    panel: PricePanel,
    feature_cols: list[str],
    horizons: list[int],
    *,
    min_periods: int = 4,
) -> pl.DataFrame:
    """Year-sliced Rank IC mean/std → ICIR."""
    rows: list[dict] = []
    empty = pl.DataFrame(
        schema={
            "feature": pl.Utf8,
            "horizon": pl.Int64,
            "n_periods": pl.Int64,
            "ic_mean": pl.Float64,
            "ic_std": pl.Float64,
            "icir": pl.Float64,
        }
    )
    if not feature_cols:
        return empty

    year_vals = [_entry_year(d) for d in events["entry_intent_date"].to_list()]
    ev = events.with_columns(pl.Series("_ic_year", year_vals))
    years = sorted(set(year_vals))
    for feat in feature_cols:
        if feat not in events.columns:
            continue
        for h in horizons:
            period_ics: list[float] = []
            for y in years:
                sub = ev.filter(pl.col("_ic_year") == y).drop("_ic_year")
                if sub.height < 3:
                    continue
                tab = compute_ic_table(sub, panel, [feat], [h])
                if tab.height == 0:
                    continue
                ic = tab["rank_ic"][0]
                if ic is None or (isinstance(ic, float) and np.isnan(ic)):
                    continue
                period_ics.append(float(ic))
            n_p = len(period_ics)
            if n_p == 0:
                rows.append(
                    {
                        "feature": feat,
                        "horizon": h,
                        "n_periods": 0,
                        "ic_mean": float("nan"),
                        "ic_std": float("nan"),
                        "icir": float("nan"),
                    }
                )
                continue
            arr = np.asarray(period_ics, dtype=float)
            mean = float(arr.mean())
            std = float(arr.std(ddof=1)) if n_p > 1 else float("nan")
            icir = (
                mean / std
                if n_p >= min_periods and std and std > 1e-12 and np.isfinite(std)
                else float("nan")
            )
            rows.append(
                {
                    "feature": feat,
                    "horizon": h,
                    "n_periods": n_p,
                    "ic_mean": mean,
                    "ic_std": std,
                    "icir": icir,
                }
            )
    return pl.DataFrame(rows) if rows else empty


def compute_alpha_beta_table(
    events: pl.DataFrame,
    panel: PricePanel,
    feature_cols: list[str],
    horizons: list[int],
    *,
    benchmark: str | None,
) -> pl.DataFrame:
    """Event-level CAPM-style alpha/beta and residual Rank IC vs benchmark.

    For each feature × horizon, pool events and estimate:
      R_stock = alpha + beta * R_benchmark + e
    Also report Rank IC of the factor against excess return (R - Rm).
    """
    empty = pl.DataFrame(
        schema={
            "feature": pl.Utf8,
            "horizon": pl.Int64,
            "n": pl.Int64,
            "rank_ic": pl.Float64,
            "rank_ic_excess": pl.Float64,
            "ols_alpha": pl.Float64,
            "ols_beta": pl.Float64,
            "alpha_tstat": pl.Float64,
            "top_bottom_excess": pl.Float64,
            "mean_fwd_ret": pl.Float64,
            "mean_bench_ret": pl.Float64,
            "mean_excess": pl.Float64,
            "benchmark": pl.Utf8,
        }
    )
    if not benchmark or not feature_cols:
        return empty

    rows: list[dict] = []
    for feat in feature_cols:
        if feat not in events.columns:
            continue
        for h in horizons:
            xs: list[float] = []
            rs: list[float] = []
            rms: list[float] = []
            for r in events.iter_rows(named=True):
                fv = r.get(feat)
                if fv is None:
                    continue
                entry = r["entry_intent_date"]
                if not isinstance(entry, date):
                    entry = date.fromisoformat(str(entry))
                ret = _fwd_return(panel, r["instrument"], entry, h)
                mret = _fwd_return(panel, benchmark, entry, h)
                if ret is None or mret is None:
                    continue
                try:
                    xs.append(float(fv))
                except (TypeError, ValueError):
                    continue
                rs.append(float(ret))
                rms.append(float(mret))
            if len(xs) < 3:
                continue
            arr_x = np.asarray(xs, dtype=float)
            arr_r = np.asarray(rs, dtype=float)
            arr_m = np.asarray(rms, dtype=float)
            excess = arr_r - arr_m
            alpha, beta, tstat = _ols_alpha_beta(arr_r, arr_m)
            # High-minus-low mean excess (top/bottom quintile by feature)
            order = arr_x.argsort()
            ex_s = excess[order]
            n = len(ex_s)
            k = max(n // 5, 1)
            top_bottom = float(ex_s[-k:].mean() - ex_s[:k].mean())
            rows.append(
                {
                    "feature": feat,
                    "horizon": int(h),
                    "n": int(len(xs)),
                    "rank_ic": spearman_ic(arr_x, arr_r),
                    "rank_ic_excess": spearman_ic(arr_x, excess),
                    "ols_alpha": alpha,
                    "ols_beta": beta,
                    "alpha_tstat": tstat,
                    "top_bottom_excess": top_bottom,
                    "mean_fwd_ret": float(arr_r.mean()),
                    "mean_bench_ret": float(arr_m.mean()),
                    "mean_excess": float(excess.mean()),
                    "benchmark": benchmark,
                }
            )
    return pl.DataFrame(rows) if rows else empty


def compute_quantile_returns(
    events: pl.DataFrame,
    panel: PricePanel,
    feature_cols: list[str],
    *,
    horizon: int = 5,
    n_quantiles: int = 5,
    benchmark: str | None = None,
) -> pl.DataFrame:
    """Mean forward return by in-sample feature quantile (+ excess / alpha if benchmark)."""
    rows: list[dict] = []
    empty = pl.DataFrame(
        schema={
            "feature": pl.Utf8,
            "horizon": pl.Int64,
            "quantile": pl.Int64,
            "n": pl.Int64,
            "mean_fwd_ret": pl.Float64,
            "mean_excess": pl.Float64,
            "ols_alpha": pl.Float64,
            "ols_beta": pl.Float64,
        }
    )
    if n_quantiles < 2:
        return empty

    for feat in feature_cols:
        if feat not in events.columns:
            continue
        pairs: list[tuple[float, float, float | None]] = []
        for r in events.iter_rows(named=True):
            fv = r.get(feat)
            if fv is None:
                continue
            entry = r["entry_intent_date"]
            if not isinstance(entry, date):
                entry = date.fromisoformat(str(entry))
            ret = _fwd_return(panel, r["instrument"], entry, horizon)
            if ret is None:
                continue
            mret = _fwd_return(panel, benchmark, entry, horizon) if benchmark else None
            try:
                pairs.append((float(fv), float(ret), float(mret) if mret is not None else None))
            except (TypeError, ValueError):
                continue
        if len(pairs) < n_quantiles * 2:
            continue
        xs = np.asarray([p[0] for p in pairs], dtype=float)
        ys = np.asarray([p[1] for p in pairs], dtype=float)
        ms = np.asarray([p[2] if p[2] is not None else np.nan for p in pairs], dtype=float)
        order = xs.argsort()
        xs_s, ys_s, ms_s = xs[order], ys[order], ms[order]
        edges = np.array_split(np.arange(len(xs_s)), n_quantiles)
        for q, idx in enumerate(edges, start=1):
            if len(idx) == 0:
                continue
            yq = ys_s[idx]
            mq = ms_s[idx]
            mean_ex = float("nan")
            alpha = float("nan")
            beta = float("nan")
            finite_m = mq[np.isfinite(mq)]
            if len(finite_m) >= 3:
                y_ok = yq[np.isfinite(mq)]
                m_ok = mq[np.isfinite(mq)]
                mean_ex = float((y_ok - m_ok).mean())
                alpha, beta, _ = _ols_alpha_beta(y_ok, m_ok)
            elif len(finite_m) > 0:
                y_ok = yq[np.isfinite(mq)]
                m_ok = mq[np.isfinite(mq)]
                if len(y_ok):
                    mean_ex = float((y_ok - m_ok).mean())
            rows.append(
                {
                    "feature": feat,
                    "horizon": int(horizon),
                    "quantile": int(q),
                    "n": int(len(idx)),
                    "mean_fwd_ret": float(yq.mean()),
                    "mean_excess": mean_ex,
                    "ols_alpha": alpha,
                    "ols_beta": beta,
                }
            )
    return pl.DataFrame(rows) if rows else empty
