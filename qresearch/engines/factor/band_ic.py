"""Full-sample vs in-band (between) Rank IC for interval-factor hypotheses."""

from __future__ import annotations

from typing import Any

import polars as pl

from qresearch.engines.data.panel import PricePanel
from qresearch.engines.factor.ic import compute_ic_table, compute_icir_table


class BandICError(ValueError):
    """Invalid band-ic request."""


def filter_feature_band(
    events: pl.DataFrame,
    feature: str,
    lo: float,
    hi: float,
) -> pl.DataFrame:
    if feature not in events.columns:
        raise BandICError(f"feature not in events: {feature}")
    if lo >= hi:
        raise BandICError(f"require lo < hi; got lo={lo}, hi={hi}")
    return events.filter(
        (pl.col(feature).is_not_null())
        & (pl.col(feature) >= float(lo))
        & (pl.col(feature) <= float(hi))
    )


def _ic_map(ic_df: pl.DataFrame, feature: str) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    if ic_df is None or ic_df.height == 0:
        return out
    for r in ic_df.filter(pl.col("feature") == feature).to_dicts():
        out[int(r["horizon"])] = r
    return out


def run_band_ic(
    events: pl.DataFrame,
    panel: PricePanel,
    *,
    feature: str,
    lo: float,
    hi: float,
    horizons: list[int],
    inside_features: list[str] | None = None,
    icir_min_periods: int = 3,
) -> dict[str, Any]:
    """Compare Rank IC on full sample vs feature band [lo, hi]."""
    if feature not in events.columns:
        raise BandICError(f"feature not in events: {feature}")
    n_full = int(events.height)
    band = filter_feature_band(events, feature, lo, hi)
    n_band = int(band.height)
    keep_frac = (n_band / n_full) if n_full else 0.0

    ic_full = compute_ic_table(events, panel, [feature], horizons)
    ic_band = compute_ic_table(band, panel, [feature], horizons) if n_band else ic_full.head(0)
    icir_full = compute_icir_table(
        events, panel, [feature], horizons, min_periods=icir_min_periods
    )
    icir_band = (
        compute_icir_table(band, panel, [feature], horizons, min_periods=icir_min_periods)
        if n_band
        else icir_full.head(0)
    )

    full_map = _ic_map(ic_full, feature)
    band_map = _ic_map(ic_band, feature)
    rows: list[dict[str, Any]] = []
    band_stronger_flags: list[bool] = []
    for h in horizons:
        f = full_map.get(int(h), {})
        b = band_map.get(int(h), {})
        fic = f.get("rank_ic")
        bic = b.get("rank_ic")
        stronger = False
        try:
            if fic is not None and bic is not None:
                stronger = abs(float(bic)) > abs(float(fic)) + 1e-12
        except (TypeError, ValueError):
            stronger = False
        band_stronger_flags.append(stronger)
        rows.append(
            {
                "feature": feature,
                "horizon": int(h),
                "rank_ic_full": fic,
                "n_full": f.get("n") or n_full,
                "rank_ic_band": bic,
                "n_band_ic": b.get("n") or 0,
                "band_stronger": stronger,
            }
        )

    inside_rows: list[dict[str, Any]] = []
    for feat in inside_features or []:
        if feat == feature or feat not in events.columns:
            continue
        ic_in = compute_ic_table(band, panel, [feat], horizons) if n_band else None
        if ic_in is None or ic_in.height == 0:
            continue
        for r in ic_in.to_dicts():
            inside_rows.append(
                {
                    "inside_feature": feat,
                    "horizon": r["horizon"],
                    "rank_ic_in_band": r["rank_ic"],
                    "n": r["n"],
                }
            )

    return {
        "feature": feature,
        "lo": float(lo),
        "hi": float(hi),
        "n_full": n_full,
        "n_band": n_band,
        "keep_frac": keep_frac,
        "horizons": list(horizons),
        "rows": rows,
        "band_stronger": any(band_stronger_flags),
        "band_stronger_share": (
            sum(1 for x in band_stronger_flags if x) / len(band_stronger_flags)
            if band_stronger_flags
            else 0.0
        ),
        "inside_rows": inside_rows,
        "ic_full": ic_full.to_dicts() if ic_full.height else [],
        "ic_band": ic_band.to_dicts() if ic_band.height else [],
        "icir_full": icir_full.to_dicts() if icir_full.height else [],
        "icir_band": icir_band.to_dicts() if icir_band.height else [],
    }
