from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from qresearch.config.models import (
    CompositeComponent,
    CompositeConfig,
    FactorsConfig,
    GatesConfig,
    RankBy,
    ResearchConfig,
    SignalsConfig,
)
from qresearch.engines.analysis.report import evaluate_gates
from qresearch.engines.data.panel import PricePanel
from qresearch.engines.factor.ic import compute_icir_table, compute_quantile_returns
from qresearch.engines.factor.sample_profile import build_sample_profile
from qresearch.engines.factor.universe import resolve_feature_cols
from qresearch.engines.signal.composite import apply_composite
from qresearch.engines.signal.engine import build_ranked


def _panel_events(n: int = 40):
    start = date(2020, 1, 2)
    sessions = []
    d = start
    while len(sessions) < n:
        if d.weekday() < 5:
            sessions.append(d)
        d += timedelta(days=1)
    rows = []
    for i, s in enumerate(sessions):
        close = 10.0 * (1.0 + 0.01 * i)
        rows.append(
            {
                "instrument": "AAA001.SZ",
                "trade_date": s,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "vol": 1e5,
                "amount": 1e6,
            }
        )
    panel = PricePanel(
        bars=pl.DataFrame(rows),
        calendar=sessions,
        adjustment_as_of=sessions[-1].strftime("%Y%m%d"),
        data_fingerprint="t",
        start=sessions[0],
        end=sessions[-1],
        instruments=["AAA001.SZ"],
    )
    panel.build_index()
    # multi-year events, feature aligns with later higher returns if low score preferred?
    # Use score = -i so higher score -> earlier entry -> lower fwd in uptrend => negative IC
    # For quantile: higher feature should have lower fwd ret
    events = pl.DataFrame(
        {
            "instrument": ["AAA001.SZ"] * 12,
            "entry_intent_date": [sessions[1 + i] for i in range(12)],
            "features.score": [float(11 - i) for i in range(12)],
            "features.name": ["x"] * 12,
            "features.noise": [None] * 12,
        }
    )
    return panel, events, sessions


def test_resolve_feature_cols_excludes_blacklist_and_empty():
    _, events, _ = _panel_events()
    cols = resolve_feature_cols(events, FactorsConfig(min_non_null=5, max_features=10))
    assert "features.score" in cols
    assert "features.name" not in cols
    assert "features.noise" not in cols


def test_sample_profile_years():
    _, events, _ = _panel_events()
    # stretch years
    ev = events.with_columns(
        pl.Series(
            "entry_intent_date",
            [date(2019, 1, 3), date(2020, 1, 3)] + events["entry_intent_date"].to_list()[2:],
        )
    )
    prof = build_sample_profile(ev, ["features.score"])
    assert prof["n_events"] == 12
    assert "2019" in prof["years"] or "2020" in prof["years"]


def test_quantile_and_icir_shapes():
    panel, events, _ = _panel_events(n=60)
    # spread across years for ICIR periods
    years = [2019, 2020, 2021, 2022, 2023, 2024] * 2
    entries = []
    d0 = date(2019, 1, 3)
    for y in years:
        # map to a session in panel roughly
        entries.append(panel.calendar[min(len(panel.calendar) - 6, abs(hash(y)) % 40)])
    ev = events.head(12).with_columns(pl.Series("entry_intent_date", entries[:12]))
    q = compute_quantile_returns(ev, panel, ["features.score"], horizon=5, n_quantiles=4)
    assert q.height >= 1
    assert set(q.columns) >= {"feature", "quantile", "mean_fwd_ret"}
    # higher score earlier-ish -> in uptrend lower fwd; Q4 mean <= Q1 mean often
    icir = compute_icir_table(ev, panel, ["features.score"], [5], min_periods=2)
    assert icir.height >= 1
    assert "icir" in icir.columns


def test_gates_structural_vs_economic():
    metrics = {"sharpe": -0.5, "max_dd": -0.5, "n_trades": 100, "n_trials": 1}
    gates = GatesConfig(
        min_oos_folds=0,
        min_trades=10,
        min_oos_sharpe=0.0,
        max_oos_drawdown=0.35,
        require_economic_for_promote=True,
    )
    res = evaluate_gates(metrics, gates, n_oos_folds=2)
    assert res["structural_passed"] is True
    assert res["economic_passed"] is False
    assert res["passed"] is True
    assert res["promotable"] is False
    assert "sharpe_below_min" in res["economic_reasons"]
    assert "drawdown_above_max" in res["economic_reasons"]


def test_composite_zscore_rank():
    events = pl.DataFrame(
        {
            "instrument": ["A", "B", "C"],
            "entry_intent_date": [date(2024, 1, 2)] * 3,
            "features.pre_r1": [0.0, -1.0, -2.0],
            "features.bandwidth_percent": [30.0, 20.0, 10.0],
        }
    )
    cfg = ResearchConfig(
        signals=SignalsConfig(
            composite=CompositeConfig(
                enabled=True,
                name="composite_score",
                components=[
                    CompositeComponent(field="features.pre_r1", weight=1.0, ascending=True),
                    CompositeComponent(field="features.bandwidth_percent", weight=0.5, ascending=True),
                ],
            ),
            rank_by=[RankBy(field="features.composite_score", ascending=False)],
        )
    )
    out = build_ranked(events, cfg)
    assert "features.composite_score" in out.columns
    # lowest pre_r1 and bandwidth should rank first when ascending components -> high composite
    assert out["instrument"][0] == "C"
