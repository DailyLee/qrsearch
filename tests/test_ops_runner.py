"""Ops runner contracts for held-position sell intents."""

from __future__ import annotations

import json
from datetime import date

import polars as pl
import pytest

from qresearch.config.models import ResearchConfig
from qresearch.engines.data.panel import PricePanel
from qresearch.engines.ops import runner


_SESSION = date(2024, 1, 3)
_HELD = "000001.SZ"
_EVENT = "000002.SZ"


def _events() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "instrument": [_EVENT],
            "decision_date": [date(2024, 1, 4)],
            "entry_intent_date": [date(2024, 1, 4)],
            "exit_intent_date": [date(2024, 1, 8)],
        }
    )


def _state_path(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "cash": 90_000.0,
                "positions": [
                    {
                        "instrument": _HELD,
                        "qty": 1_000,
                        "entry_price": 10.0,
                        "entry_session": "2024-01-02",
                        "exit_intent_date": "2024-01-03",
                        "cost_basis": 10_000.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _run_with_held_bar(monkeypatch, tmp_path, held_bar: dict[str, object] | None):
    events = _events()
    monkeypatch.setattr(runner, "load_events", lambda *_args, **_kwargs: events)

    def load_synthetic_panel(
        panel_events,
        _config,
        *,
        cache_dir=None,
        extra_instruments=None,
    ):
        del cache_dir
        instruments = sorted(
            set(panel_events["instrument"].to_list()) | set(extra_instruments or [])
        )
        rows = [
            {
                "instrument": _EVENT,
                "trade_date": _SESSION,
                "open": 20.0,
                "high": 20.5,
                "low": 19.5,
                "close": 20.1,
                "vol": 1_000.0,
                "amount": 20_000.0,
                "adj_factor": 1.0,
                "up_limit": 22.0,
                "down_limit": 18.0,
            }
        ]
        if _HELD in instruments and held_bar is not None:
            rows.append(
                {
                    "instrument": _HELD,
                    "trade_date": _SESSION,
                    "high": 10.2,
                    "low": 8.8,
                    "close": 9.5,
                    "amount": 10_000.0,
                    "adj_factor": 1.0,
                    "up_limit": 11.0,
                    **held_bar,
                }
            )
        panel = PricePanel(
            bars=pl.DataFrame(rows),
            calendar=[_SESSION],
            adjustment_as_of="session_pit",
            data_fingerprint="synthetic",
            start=_SESSION,
            end=_SESSION,
            instruments=instruments,
        )
        panel.build_index()
        return panel

    monkeypatch.setattr(runner, "load_price_panel", load_synthetic_panel)
    return runner.run_ops(
        package_dir=None,
        events_path="unused.csv",
        asof="20240103",
        mode="signal",
        state_path=_state_path(tmp_path),
        config=ResearchConfig(),
    )


@pytest.mark.parametrize(
    ("held_bar", "expected_reason"),
    [
        ({"open": 9.0, "vol": 1_000.0, "down_limit": 9.0}, "limit_down"),
        ({"open": 10.0, "vol": 0.0, "down_limit": 9.0}, "suspended"),
        ({"open": 10.0, "vol": 1_000.0, "down_limit": None}, "missing_limit_data"),
        (None, "data_gap"),
    ],
)
def test_due_held_sell_obeys_historical_limit_data(
    monkeypatch, tmp_path, held_bar, expected_reason
):
    """Catches emitting a due sell without checking its historical session bar."""
    result = _run_with_held_bar(monkeypatch, tmp_path, held_bar)

    assert result["orders"] == []
    assert result["rejects"] == [
        {
            "session": "2024-01-03",
            "instrument": _HELD,
            "reason": expected_reason,
            "side": "sell",
        }
    ]


def test_due_held_sell_keeps_established_market_close_output(monkeypatch, tmp_path):
    """Catches changing the public order shape while adding the sell check."""
    result = _run_with_held_bar(
        monkeypatch,
        tmp_path,
        {"open": 10.0, "vol": 1_000.0, "down_limit": 9.0},
    )

    assert result["orders"] == [
        {
            "asof": "20240103",
            "instrument": _HELD,
            "side": "sell",
            "order_type": "market_close",
            "reason": "exit_intent",
        }
    ]
    assert result["rejects"] == []
