from __future__ import annotations

from conftest import research_config

from datetime import date, timedelta

import polars as pl
import pytest

from qresearch.config.models import (
    FilterRule,
    HypothesisConfig,
    RankBy,
    ResearchConfig,
    SignalsConfig,
)
from qresearch.engines.data.panel import PricePanel
from qresearch.engines.experiment.optimize import (
    OptimizeError,
    replace_feature_filter,
    resolve_side,
    run_signal_threshold_search,
    threshold_for_keep,
)


def test_threshold_high_low_keep_frac():
    vals = __import__("numpy").arange(1.0, 101.0)
    op_h, thr_h = threshold_for_keep(vals, side="high", keep_frac=0.2)
    assert op_h == "ge"
    assert thr_h == pytest.approx(80.0, abs=1.0)
    op_l, thr_l = threshold_for_keep(vals, side="low", keep_frac=0.2)
    assert op_l == "le"
    assert thr_l == pytest.approx(20.0, abs=1.0)


def test_resolve_side_from_expected_sign_and_rank():
    cfg = research_config(
        hypothesis=HypothesisConfig(expected_sign={"features.pre_r1": "negative"})
    )
    assert resolve_side(cfg, "features.pre_r1", "auto") == "low"
    cfg2 = research_config(
        signals=SignalsConfig(rank_by=[RankBy(field="features.box_quality", ascending=False)])
    )
    assert resolve_side(cfg2, "features.box_quality", "auto") == "high"
    with pytest.raises(OptimizeError, match="cannot resolve side"):
        resolve_side(research_config(), "features.pre_r1", "auto")


def test_replace_feature_filter_preserves_others():
    filters = [
        FilterRule(field="features.box_quality", op="ge", value=0.9),
        FilterRule(field="features.pre_r1", op="le", value=0.0),
    ]
    out = replace_feature_filter(filters, "features.pre_r1", "le", -0.02)
    fields = {f.field: f for f in out}
    assert fields["features.box_quality"].value == 0.9
    assert fields["features.pre_r1"].op == "le"
    assert fields["features.pre_r1"].value == -0.02


def _tiny_panel_events():
    start = date(2023, 1, 2)
    sessions: list[date] = []
    d = start
    while len(sessions) < 40:
        if d.weekday() < 5:
            sessions.append(d)
        d += timedelta(days=1)
    rows = []
    for inst, base in [("AAA001.SZ", 10.0), ("AAA002.SZ", 20.0)]:
        for i, s in enumerate(sessions):
            close = base * (1.0 + 0.002 * i)
            rows.append(
                {
                    "instrument": inst,
                    "trade_date": s,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "vol": 1e5,
                    "amount": 1e6,
                    "adj_factor": 1.0,
                }
            )
    bars = pl.DataFrame(rows)
    panel = PricePanel(
        bars=bars,
        calendar=sessions,
        adjustment_as_of="session_pit",
        data_fingerprint="t",
        start=sessions[0],
        end=sessions[-1],
        instruments=["AAA001.SZ", "AAA002.SZ"],
        adj_mode="none",
    )
    panel.build_index()
    # two calendar years for WF path
    events = pl.DataFrame(
        {
            "instrument": ["AAA001.SZ", "AAA002.SZ"] * 6,
            "entry_intent_date": [sessions[2], sessions[2], sessions[5], sessions[5]]
            + [sessions[20], sessions[20], sessions[25], sessions[25]]
            + [sessions[30], sessions[30], sessions[35], sessions[35]],
            "exit_intent_date": [sessions[8]] * 12,
            "features.pre_r1": [-0.05, 0.04, -0.03, 0.02, -0.06, 0.01, -0.04, 0.03, -0.02, 0.05, -0.07, 0.0],
            "features.box_quality": [0.95] * 12,
        }
    )
    # fix entry years: force some 2024
    eds = events["entry_intent_date"].to_list()
    eds[6] = date(2024, 2, 1)
    eds[7] = date(2024, 2, 1)
    eds[8] = date(2024, 3, 1)
    eds[9] = date(2024, 3, 1)
    eds[10] = date(2024, 4, 1)
    eds[11] = date(2024, 4, 1)
    events = events.with_columns(pl.Series("entry_intent_date", eds))
    return panel, events


def test_search_low_side_uses_le_and_keeps_other_filters(monkeypatch: pytest.MonkeyPatch):
    panel, events = _tiny_panel_events()
    cfg = research_config(
        hypothesis=HypothesisConfig(expected_sign={"features.pre_r1": "negative"}),
        signals=SignalsConfig(
            filters=[FilterRule(field="features.box_quality", op="ge", value=0.9)],
            rank_by=[RankBy(field="features.pre_r1", ascending=True)],
        ),
    )

    scores = {0.1: 0.5, 0.2: 2.0, 0.3: 1.0, 0.4: 0.2}
    n = {"i": 0}
    want = [0.1, 0.2, 0.3, 0.4]

    def fake_score(ev, pan, c):
        fr = next(f for f in c.signals.filters if f.field == "features.pre_r1")
        assert fr.op == "le"
        assert any(f.field == "features.box_quality" for f in c.signals.filters)
        kf = want[n["i"]]
        n["i"] += 1
        return float(scores[kf]), 20, "full_sample"

    monkeypatch.setattr(
        "qresearch.engines.experiment.optimize._score_config", fake_score
    )
    out = run_signal_threshold_search(
        events,
        panel,
        cfg,
        feature="features.pre_r1",
        side="auto",
        keep_fracs=[0.1, 0.2, 0.3, 0.4],
    )
    assert out["side"] == "low"
    assert out["best_params"]["op"] == "le"
    assert out["best_params"]["keep_frac"] == 0.2
    assert out["method"] == "signal_quantile_grid"


def test_search_high_side_uses_ge(monkeypatch: pytest.MonkeyPatch):
    panel, events = _tiny_panel_events()
    cfg = research_config(
        hypothesis=HypothesisConfig(expected_sign={"features.box_quality": "positive"}),
    )
    n = {"i": 0}

    def fake_score(ev, pan, c):
        fr = next(f for f in c.signals.filters if f.field == "features.box_quality")
        assert fr.op == "ge"
        n["i"] += 1
        return float(n["i"]), 20, "full_sample"

    monkeypatch.setattr(
        "qresearch.engines.experiment.optimize._score_config", fake_score
    )
    out = run_signal_threshold_search(
        events, panel, cfg, feature="features.box_quality", side="auto", keep_fracs=[0.2, 0.3]
    )
    assert out["side"] == "high"
    assert out["best_params"]["op"] == "ge"
