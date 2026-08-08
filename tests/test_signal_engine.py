from __future__ import annotations

from conftest import research_config

from datetime import date

import polars as pl

from qresearch.config.models import FilterRule, RankBy, ResearchConfig, SignalsConfig
from qresearch.engines.signal.engine import apply_filter, build_ranked, rank_events


def test_apply_filter_comparisons():
    row = {"features.box_quality": 0.95, "features.bandwidth_percent": 18.0}
    assert apply_filter(row, FilterRule(field="features.box_quality", op="ge", value=0.94))
    assert not apply_filter(row, FilterRule(field="features.box_quality", op="gt", value=0.95))
    assert apply_filter(row, FilterRule(field="features.bandwidth_percent", op="lt", value=20))
    assert apply_filter(row, FilterRule(field="features.box_quality", op="eq", value=0.95))
    assert apply_filter(row, FilterRule(field="features.box_quality", op="ne", value=0.9))


def test_apply_filter_between_and_missing():
    row = {"features.pct_b": 0.3}
    assert apply_filter(
        row, FilterRule(field="features.pct_b", op="between", value=0.0, value_max=0.5)
    )
    assert not apply_filter(
        row, FilterRule(field="features.pct_b", op="between", value=0.4, value_max=0.5)
    )
    assert not apply_filter(row, FilterRule(field="features.missing", op="ge", value=0))
    assert not apply_filter(
        row, FilterRule(field="features.pct_b", op="between", value=0.0, value_max=None)
    )


def test_apply_filter_bare_feature_name():
    row = {"features.rsi_value": 55.0}
    assert apply_filter(row, FilterRule(field="rsi_value", op="ge", value=50))


def test_rank_events_filter_and_ascending_rank():
    events = pl.DataFrame(
        {
            "instrument": ["A", "B", "C"],
            "entry_intent_date": [date(2024, 1, 2), date(2024, 1, 2), date(2024, 1, 3)],
            "features.box_quality": [0.90, 0.96, 0.97],
            "features.bandwidth_percent": [30.0, 10.0, 20.0],
        }
    )
    signals = SignalsConfig(
        filters=[FilterRule(field="features.box_quality", op="ge", value=0.94)],
        rank_by=[RankBy(field="features.bandwidth_percent", ascending=True)],
    )
    out = rank_events(events, signals)
    assert out.height == 2
    assert out["instrument"].to_list() == ["B", "C"]  # 10 then 20
    assert out["rank_score"].to_list() == [0.0, 1.0]


def test_rank_events_descending_and_empty_after_filter():
    events = pl.DataFrame(
        {
            "instrument": ["A", "B"],
            "entry_intent_date": [date(2024, 1, 2), date(2024, 1, 2)],
            "features.box_quality": [0.5, 0.6],
            "features.pre_cum2": [1.0, 3.0],
        }
    )
    signals = SignalsConfig(
        filters=[FilterRule(field="features.box_quality", op="ge", value=0.9)],
        rank_by=[RankBy(field="features.pre_cum2", ascending=False)],
    )
    empty = rank_events(events, signals)
    assert empty.height == 0

    signals2 = SignalsConfig(
        filters=[],
        rank_by=[RankBy(field="features.pre_cum2", ascending=False)],
    )
    ranked = rank_events(events, signals2)
    assert ranked["instrument"].to_list() == ["B", "A"]


def test_build_ranked_uses_config_signals():
    events = pl.DataFrame(
        {
            "instrument": ["X", "Y"],
            "entry_intent_date": [date(2024, 1, 2), date(2024, 1, 2)],
            "features.box_quality": [0.99, 0.95],
            "features.bandwidth_percent": [25.0, 12.0],
        }
    )
    cfg = research_config(
        signals=SignalsConfig(
            filters=[FilterRule(field="features.box_quality", op="ge", value=0.94)],
            rank_by=[RankBy(field="features.bandwidth_percent", ascending=True)],
        )
    )
    out = build_ranked(events, cfg)
    assert out.height == 2
    assert out["instrument"][0] == "Y"
