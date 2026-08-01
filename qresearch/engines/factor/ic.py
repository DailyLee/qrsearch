"""Event-level factor IC utilities."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl

from qresearch.engines.data.panel import PricePanel


def _fwd_return(panel: PricePanel, instrument: str, entry: date, horizon: int) -> float | None:
    bar0 = panel.get(instrument, entry)
    if bar0 is None:
        # try next session
        nxt = panel.next_session(entry, 0)
        if nxt is None:
            return None
        bar0 = panel.get(instrument, nxt)
        entry = nxt
    if bar0 is None:
        return None
    end = panel.next_session(entry, horizon)
    if end is None:
        return None
    bar1 = panel.get(instrument, end)
    if bar1 is None:
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
