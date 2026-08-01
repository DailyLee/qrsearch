from __future__ import annotations

import numpy as np
import polars as pl

from qresearch.config.models import FactorPreprocessConfig
from qresearch.engines.factor.preprocess import (
    apply_factor_preprocess,
    industry_neutralize,
    size_neutralize,
    winsorize_series,
    zscore_series,
)


def test_winsorize_and_zscore():
    x = np.array([0.0, 1.0, 2.0, 3.0, 100.0])
    w = winsorize_series(x, q=0.2)
    assert w.max() < 100.0
    z = zscore_series(np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert abs(float(np.nanmean(z))) < 1e-9
    assert abs(float(np.nanstd(z, ddof=0)) - 1.0) < 1e-9


def test_industry_neutralize_constant_within_industry():
    x = np.array([10.0, 10.0, 20.0, 20.0])
    ind = np.array(["A", "A", "B", "B"], dtype=object)
    y = industry_neutralize(x, ind)
    assert np.allclose(y, 0.0)


def test_size_neutralize_reduces_linear_dependence():
    rng = np.random.default_rng(0)
    size = np.linspace(10.0, 100.0, 40)
    x = 0.5 * np.log1p(size) + rng.normal(0, 0.01, size=40)
    resid = size_neutralize(x, size)
    corr_raw = abs(np.corrcoef(x, np.log1p(size))[0, 1])
    m = np.isfinite(resid)
    corr_res = abs(np.corrcoef(resid[m], np.log1p(size)[m])[0, 1])
    assert corr_raw > 0.9
    assert corr_res < 0.2


def test_apply_disabled_no_new_cols():
    events = pl.DataFrame(
        {
            "entry_intent_date": ["2024-01-02"] * 5,
            "features.score": [1.0, 2.0, 3.0, 4.0, 5.0],
            "features.industry": ["A", "A", "B", "B", "B"],
            "features.total_mv": [10.0, 20.0, 30.0, 40.0, 50.0],
        }
    )
    cfg = FactorPreprocessConfig(enabled=False)
    out, report = apply_factor_preprocess(events, ["features.score"], cfg)
    assert out.columns == events.columns
    assert report.get("skipped") == "preprocess_disabled"


def test_apply_pipeline_steps_and_suffix():
    events = pl.DataFrame(
        {
            "entry_intent_date": ["2024-01-02"] * 40,
            "features.score": list(np.linspace(1.0, 10.0, 40)),
            "features.industry": (["A"] * 20) + (["B"] * 20),
            "features.total_mv": list(np.linspace(10.0, 200.0, 40)),
        }
    )
    cfg = FactorPreprocessConfig(
        enabled=True,
        steps=["winsorize", "industry_neutral", "size_neutral", "zscore"],
        cross_section="all",
        min_group_size=5,
        suffix="__prep",
    )
    out, report = apply_factor_preprocess(events, ["features.score"], cfg)
    assert "features.score__prep" in out.columns
    assert "features.score" in out.columns
    assert report["output_features"] == ["features.score__prep"]
    prep = np.asarray(out["features.score__prep"].to_list(), dtype=float)
    assert np.isfinite(prep).sum() >= 30
    assert abs(float(np.nanmean(prep))) < 0.25  # after zscore ~0


def test_steps_order_configurable():
    events = pl.DataFrame(
        {
            "entry_intent_date": ["2024-01-02"] * 20,
            "features.score": list(range(20)),
            "features.industry": ["X"] * 20,
            "features.total_mv": list(range(20)),
        }
    )
    only_z = FactorPreprocessConfig(enabled=True, steps=["zscore"], cross_section="all")
    out, _ = apply_factor_preprocess(events, ["features.score"], only_z)
    z = np.asarray(out["features.score__prep"].to_list(), dtype=float)
    assert abs(float(np.nanstd(z, ddof=0)) - 1.0) < 1e-6
