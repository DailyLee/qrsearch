from __future__ import annotations

import polars as pl

from qresearch.engines.factor.diagnostics import (
    corr_top_pairs,
    feature_corr_matrix,
    quantile_monotonicity,
    reject_near_constant_features,
)


def test_reject_near_constant():
    events = pl.DataFrame(
        {
            "features.a": [1.0, 1.0, 1.0, 1.0],
            "features.b": [1.0, 2.0, 3.0, 4.0],
        }
    )
    rej = reject_near_constant_features(events, ["features.a", "features.b", "features.missing"])
    reasons = {r["feature"]: r["reason"] for r in rej}
    assert "features.a" in reasons
    assert "features.missing" in reasons
    assert "features.b" not in reasons


def test_corr_symmetric_and_top_pairs():
    n = 40
    x = list(range(n))
    events = pl.DataFrame(
        {
            "features.x": [float(v) for v in x],
            "features.y": [float(v) * 2 for v in x],
            "features.z": [float((-1) ** v) for v in x],
        }
    )
    corr = feature_corr_matrix(events, ["features.x", "features.y", "features.z"])
    assert corr.height == 3
    top = corr_top_pairs(corr, top_n=1)
    assert abs(top[0]["corr"]) >= 0.9


def test_quantile_monotonicity_labels():
    quant = pl.DataFrame(
        {
            "feature": ["f_up"] * 4 + ["f_weak"] * 4,
            "quantile": [1, 2, 3, 4, 1, 2, 3, 4],
            "mean_fwd_ret": [0.0, 0.1, 0.2, 0.3, 0.1, 0.0, 0.2, 0.05],
        }
    )
    mono = {r["feature"]: r for r in quantile_monotonicity(quant)}
    assert mono["f_up"]["monotonic"] == "up"
    assert mono["f_up"]["shape"] == "mono_up"
    assert mono["f_weak"]["monotonic"] == "weak"


def test_quantile_shape_u_inv_u_hump():
    quant = pl.DataFrame(
        {
            "feature": ["f_u"] * 5 + ["f_inv"] * 5 + ["f_hump"] * 5,
            "quantile": [1, 2, 3, 4, 5] * 3,
            "mean_fwd_ret": [
                # U: ends high, middle low
                0.3,
                0.1,
                0.0,
                0.1,
                0.3,
                # inv_u: peak at center (q3)
                0.0,
                0.1,
                0.4,
                0.1,
                0.0,
                # hump: peak off-center (q2)
                0.0,
                0.4,
                0.15,
                0.1,
                0.05,
            ],
        }
    )
    by = {r["feature"]: r for r in quantile_monotonicity(quant)}
    assert by["f_u"]["shape"] == "u"
    assert by["f_inv"]["shape"] == "inv_u"
    assert by["f_hump"]["shape"] == "hump"
