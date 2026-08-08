from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from qresearch.config.models import (
    EntryFilterConfig,
    ExecutionConfig,
    FeatureRefConfig,
    FeatureSourceConfig,
    PortfolioConfig,
    ResearchConfig,
    RiskConfig,
    SampleConfig,
)
from qresearch.engines.data.panel import PricePanel


def _sessions(n: int = 40, start: date = date(2024, 1, 2)) -> list[date]:
    out = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def research_config(**updates: object) -> ResearchConfig:
    """Complete market config for tests of lower-level iteration-1 engines."""
    return ResearchConfig(
        sample=SampleConfig(
            universe="synthetic",
            start_date=date(2024, 1, 1),
            end_date=date(2025, 12, 31),
        ),
        features=FeatureSourceConfig(
            refs=[FeatureRefConfig(name="synthetic", availability_lag_sessions=0)]
        ),
        **updates,
    )


@pytest.fixture
def sessions() -> list[date]:
    return _sessions()


@pytest.fixture
def panel(sessions: list[date]) -> PricePanel:
    rows = []
    for inst, base in [("AAA001.SZ", 10.0), ("AAA002.SZ", 20.0)]:
        px = base
        for i, s in enumerate(sessions):
            o = px
            c = px * (1.01 if i % 3 else 0.995)
            rows.append(
                {
                    "instrument": inst,
                    "trade_date": s,
                    "open": round(o, 2),
                    "high": round(max(o, c) * 1.01, 2),
                    "low": round(min(o, c) * 0.99, 2),
                    "close": round(c, 2),
                    "vol": 1e5,
                    "amount": 1e6,
                    "up_limit": round(o * 1.1, 2),
                    "down_limit": round(o * 0.9, 2),
                }
            )
            px = c
    bars = pl.DataFrame(rows)
    p = PricePanel(
        bars=bars,
        calendar=sessions,
        adjustment_as_of=sessions[-1].strftime("%Y%m%d"),
        data_fingerprint="test",
        start=sessions[0],
        end=sessions[-1],
        instruments=["AAA001.SZ", "AAA002.SZ"],
    )
    p.build_index()
    return p


@pytest.fixture
def base_config() -> ResearchConfig:
    return research_config(
        portfolio=PortfolioConfig(
            starting_cash=100_000,
            max_weight=0.5,
            max_new_entries_per_day=2,
            lot_size=100,
        ),
        execution=ExecutionConfig(
            price="open",
            lag_sessions=0,
            order_validity_sessions=5,
            entry_filter=EntryFilterConfig(enabled=False),
        ),
        risk=RiskConfig(stop_loss=-0.5, take_profit=0.5, max_hold_sessions=None),
    )


@pytest.fixture
def events(sessions: list[date]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "instrument": ["AAA001.SZ", "AAA002.SZ"],
            "decision_date": [sessions[1], sessions[2]],
            "entry_intent_date": [sessions[1], sessions[2]],
            "exit_intent_date": [sessions[5], sessions[6]],
            "features.box_quality": [0.97, 0.96],
            "features.bandwidth_percent": [20.0, 15.0],
            "rank_score": [0.0, 1.0],
            "source_file": ["t", "t"],
        }
    )
