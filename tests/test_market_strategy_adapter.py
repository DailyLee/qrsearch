from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from conftest import research_config
from qresearch.config.models import RiskConfig
from qresearch.research.domain import ResearchDataset
from qresearch.research.strategy import build_market_signal_frame


def _dataset() -> ResearchDataset:
    return ResearchDataset(
        frame=pl.DataFrame(
            {
                "sample_id": ["a", "b", "c"],
                "instrument": ["000001.SZ", "000001.SZ", "000002.SZ"],
                "asof_session": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 3)],
                "effective_session": [date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 4)],
                "features.alpha": [1.0, 2.0, 3.0],
                "features.industry": ["bank", "bank", "tech"],
                "role": ["train", "train", "train"],
            }
        ),
        metadata={},
    )


def test_build_market_signal_frame_maps_observations_and_keeps_features() -> None:
    config = research_config(risk=RiskConfig(max_hold_sessions=2))

    frame = build_market_signal_frame(
        _dataset(),
        config,
        [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8)],
    )

    assert frame.to_dicts() == [
        {
            "instrument": "000001.SZ",
            "decision_date": date(2024, 1, 2),
            "entry_intent_date": date(2024, 1, 3),
            "exit_intent_date": date(2024, 1, 5),
            "features.alpha": 1.0,
            "features.industry": "bank",
        },
        {
            "instrument": "000001.SZ",
            "decision_date": date(2024, 1, 3),
            "entry_intent_date": date(2024, 1, 4),
            "exit_intent_date": date(2024, 1, 8),
            "features.alpha": 2.0,
            "features.industry": "bank",
        },
        {
            "instrument": "000002.SZ",
            "decision_date": date(2024, 1, 3),
            "entry_intent_date": date(2024, 1, 4),
            "exit_intent_date": date(2024, 1, 8),
            "features.alpha": 3.0,
            "features.industry": "tech",
        },
    ]


def test_build_market_signal_frame_drops_observations_without_exit_session() -> None:
    config = research_config(risk=RiskConfig(max_hold_sessions=2))

    frame = build_market_signal_frame(
        _dataset(), config, [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    )

    assert frame.is_empty()
    assert frame.columns == [
        "instrument",
        "decision_date",
        "entry_intent_date",
        "exit_intent_date",
        "features.alpha",
        "features.industry",
    ]


@pytest.mark.parametrize(
    "risk",
    [
        RiskConfig(max_hold_sessions=None),
        RiskConfig(max_hold_sessions=2, exit_priority=["stop", "exit_intent"]),
    ],
)
def test_build_market_signal_frame_rejects_unsafe_fixed_holding(risk: RiskConfig) -> None:
    with pytest.raises(ValueError, match="max_hold"):
        build_market_signal_frame(_dataset(), research_config(risk=risk), [date(2024, 1, 2)])
