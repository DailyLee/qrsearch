from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from qresearch.config.models import (
    FeatureRefConfig,
    FeatureSourceConfig,
    ExecutionConfig,
    LabelConfig,
    ResearchConfig,
    RiskConfig,
    SampleConfig,
)
from qresearch.engines.data.panel import PricePanel
from qresearch.research.domain import SampleSet
from qresearch.research.labels import load_research_price_panel, materialize_labels


def _sessions(count: int = 8) -> list[date]:
    current = date(2024, 1, 2)
    sessions: list[date] = []
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current)
        current += timedelta(days=1)
    return sessions


def _samples(*, asof: date, instrument: str = "000001.SZ") -> SampleSet:
    return SampleSet(
        frame=pl.DataFrame(
            {
                "sample_id": ["market-1"],
                "instrument": [instrument],
                "asof_session": [asof],
                "effective_session": [asof],
                "sample_weight": [1.0],
            }
        ).with_columns(pl.col("asof_session", "effective_session").cast(pl.Date)),
        manifest={"sample_set_hash": "samples-1"},
    )


def _panel(
    sessions: list[date],
    *,
    instrument: str = "000001.SZ",
    opens: list[float | None] | None = None,
    closes: list[float | None] | None = None,
    factors: list[float] | None = None,
) -> PricePanel:
    opens = opens or [10.0 + index for index in range(len(sessions))]
    closes = closes or [20.0 + index for index in range(len(sessions))]
    factors = factors or [1.0] * len(sessions)
    panel = PricePanel(
        bars=pl.DataFrame(
            [
                {
                    "instrument": instrument,
                    "trade_date": session,
                    "open": opens[index],
                    "high": opens[index],
                    "low": opens[index],
                    "close": closes[index],
                    "vol": 1.0,
                    "amount": 1.0,
                    "adj_factor": factors[index],
                }
                for index, session in enumerate(sessions)
            ]
        ),
        calendar=sessions,
        adjustment_as_of="session_pit",
        data_fingerprint="prices-1",
        start=sessions[0],
        end=sessions[-1],
        instruments=[instrument],
        adj_mode="qfq",
    )
    panel.build_index()
    return panel


def _research() -> ResearchConfig:
    return ResearchConfig(
        sample=SampleConfig(universe="univ", start_date=date(2024, 1, 2), end_date=date(2024, 1, 31)),
        features=FeatureSourceConfig(
            refs=[FeatureRefConfig(name="momentum", availability_lag_sessions=0)]
        ),
        risk=RiskConfig(max_hold_sessions=100),
    )


def _research_without_legacy_buffers() -> ResearchConfig:
    return ResearchConfig(
        sample=SampleConfig(universe="univ", start_date=date(2024, 1, 2), end_date=date(2024, 12, 31)),
        features=FeatureSourceConfig(
            refs=[FeatureRefConfig(name="momentum", availability_lag_sessions=0)]
        ),
        execution=ExecutionConfig(order_validity_sessions=0),
        risk=RiskConfig(max_hold_sessions=100),
        delay_buffer_sessions=0,
        suspend_buffer_sessions=0,
    )


def test_load_research_price_panel_projects_only_in_memory_compatibility_events(
    monkeypatch, tmp_path
) -> None:
    # Reintroducing an event file, or using a sample as-of rather than effective date, changes loader semantics.
    sessions = _sessions()
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_load(events, research, *, cache_dir):
        captured.update({"events": events, "research": research, "cache_dir": cache_dir})
        return sentinel

    monkeypatch.setattr("qresearch.research.labels.load_price_panel", fake_load)

    result = load_research_price_panel(
        _samples(asof=sessions[0]), LabelConfig(horizon_sessions=5), _research(), tmp_path
    )

    assert result is sentinel
    assert captured["events"].to_dicts() == [
        {
            "instrument": "000001.SZ",
            "entry_intent_date": sessions[0],
            "exit_intent_date": sessions[0] + timedelta(days=26),
        }
    ]
    assert captured["cache_dir"] == tmp_path


def test_load_research_price_panel_covers_entry_delay_before_label_horizon(
    monkeypatch, tmp_path
) -> None:
    # Excluding entry lag from the range can truncate a later valid exit into no_calendar_session.
    sessions = _sessions()
    captured: dict[str, object] = {}

    def fake_load(events, _research, *, cache_dir):
        captured.update({"events": events, "cache_dir": cache_dir})
        return object()

    monkeypatch.setattr("qresearch.research.labels.load_price_panel", fake_load)

    load_research_price_panel(
        _samples(asof=sessions[0]),
        LabelConfig(entry_lag_sessions=10, horizon_sessions=1),
        _research(),
        tmp_path,
    )

    assert captured["events"].select("exit_intent_date").item() == sessions[0] + timedelta(days=36)


def test_load_research_price_panel_leaves_holiday_margin_when_legacy_buffers_are_zero(
    monkeypatch, tmp_path
) -> None:
    # A two-session target spanning National Day needs calendar margin beyond a weekday-only estimate.
    captured: dict[str, object] = {}

    def fake_load(events, _research, *, cache_dir):
        captured.update({"events": events, "cache_dir": cache_dir})
        return object()

    monkeypatch.setattr("qresearch.research.labels.load_price_panel", fake_load)
    effective_session = date(2024, 9, 30)

    load_research_price_panel(
        _samples(asof=effective_session),
        LabelConfig(entry_lag_sessions=1, horizon_sessions=1),
        _research_without_legacy_buffers(),
        tmp_path,
    )

    assert captured["events"].select("exit_intent_date").item() == date(2024, 10, 18)


def test_materialize_labels_uses_open_from_t_plus_1_to_t_plus_6() -> None:
    # Using effective-session or an implicit extra day would label the wrong holding interval.
    sessions = _sessions()
    labels = materialize_labels(
        _samples(asof=sessions[0]),
        _panel(sessions),
        LabelConfig(entry_price="open", exit_price="open", entry_lag_sessions=1, horizon_sessions=5),
    )

    assert labels.frame.select("label_start", "label_end", "forward_return", "label_status").to_dicts() == [
        {
            "label_start": sessions[1],
            "label_end": sessions[6],
            "forward_return": 0.4545454545454546,
            "label_status": "ok",
        }
    ]


def test_materialize_labels_uses_configured_close_prices() -> None:
    # Ignoring the configured price field would silently make close labels equal open labels.
    sessions = _sessions()
    labels = materialize_labels(
        _samples(asof=sessions[0]),
        _panel(sessions),
        LabelConfig(entry_price="close", exit_price="close", entry_lag_sessions=1, horizon_sessions=5),
    )

    assert labels.frame.select("label_start", "label_end", "forward_return", "label_status").to_dicts() == [
        {
            "label_start": sessions[1],
            "label_end": sessions[6],
            "forward_return": 0.23809523809523814,
            "label_status": "ok",
        }
    ]


def test_materialize_labels_uses_exit_session_asof_for_both_prices() -> None:
    # Pricing entry at its own as-of date would invent a split-period gain.
    sessions = _sessions()
    labels = materialize_labels(
        _samples(asof=sessions[0]),
        _panel(
            sessions,
            opens=[100.0, 100.0, 100.0, 50.0, 50.0, 50.0, 50.0, 50.0],
            closes=[100.0, 100.0, 100.0, 50.0, 50.0, 50.0, 50.0, 50.0],
            factors=[1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0, 2.0],
        ),
        LabelConfig(entry_lag_sessions=1, horizon_sessions=5),
    )

    assert labels.frame.select("forward_return", "label_status").to_dicts() == [
        {"forward_return": 0.0, "label_status": "ok"}
    ]


def test_materialize_labels_keeps_missing_entry_and_exit_rows() -> None:
    # Dropping failed lookups would change sample composition and hide suspension-driven missingness.
    sessions = _sessions()
    entry_missing = materialize_labels(
        _samples(asof=sessions[0]),
        _panel(sessions, opens=[10.0, None, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0]),
        LabelConfig(entry_lag_sessions=1, horizon_sessions=5),
    )
    exit_missing = materialize_labels(
        _samples(asof=sessions[0]),
        _panel(sessions, opens=[10.0, 11.0, 12.0, 13.0, 14.0, 15.0, None, 17.0]),
        LabelConfig(entry_lag_sessions=1, horizon_sessions=5),
    )

    assert entry_missing.frame.select("sample_id", "forward_return", "label_status").to_dicts() == [
        {"sample_id": "market-1", "forward_return": None, "label_status": "missing_entry"}
    ]
    assert exit_missing.frame.select("sample_id", "forward_return", "label_status").to_dicts() == [
        {"sample_id": "market-1", "forward_return": None, "label_status": "missing_exit"}
    ]


def test_materialize_labels_marks_calendar_overflow_without_dropping_sample() -> None:
    # Treating an unavailable horizon as a missing bar confuses calendar coverage with instrument coverage.
    sessions = _sessions(3)
    labels = materialize_labels(
        _samples(asof=sessions[0]),
        _panel(sessions),
        LabelConfig(entry_lag_sessions=1, horizon_sessions=5),
    )

    assert labels.frame.select("sample_id", "forward_return", "label_status").to_dicts() == [
        {"sample_id": "market-1", "forward_return": None, "label_status": "no_calendar_session"}
    ]
