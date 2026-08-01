from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from qresearch.config.models import ResearchConfig
from qresearch.engines.analysis.pit_audit import run_pit_audit
from qresearch.engines.data.panel import PricePanel


def test_pit_audit_warns_on_window_end_adj_and_passes_decision_order():
    sessions = [date(2025, 1, 2) + timedelta(days=i) for i in range(5)]
    events = pl.DataFrame(
        {
            "instrument": ["AAA001.SZ", "AAA001.SZ"],
            "decision_date": [sessions[0], sessions[1]],
            "entry_intent_date": [sessions[0], sessions[1]],
            "exit_intent_date": [sessions[2], sessions[3]],
        }
    )
    rows = []
    for s in sessions:
        rows.append(
            {
                "instrument": "AAA001.SZ",
                "trade_date": s,
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "vol": 100.0,
                "amount": 1000.0,
            }
        )
        rows.append(
            {
                "instrument": "000852.SH",
                "trade_date": s,
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "vol": 1.0,
                "amount": 1.0,
            }
        )
    panel = PricePanel(
        bars=pl.DataFrame(rows),
        calendar=sessions,
        adjustment_as_of="20250106",
        data_fingerprint="fp",
        start=sessions[0],
        end=sessions[-1],
        instruments=["000852.SH", "AAA001.SZ"],
    )
    panel.build_index()
    audit = run_pit_audit(events, panel, ResearchConfig(), strict=False)
    assert audit["status"] in ("pass", "warn")
    assert audit["adjustment"]["methodology"] == "qfq_window_end"
    assert audit["adjustment"]["full_pit_adj_factor_asof_session"] is False
    assert "adjustment_is_qfq_window_end_not_full_pit" in audit["warnings"]
    assert not any(c["id"] == "decision_before_entry" and c["status"] == "fail" for c in audit["checks"])


def test_pit_audit_fails_when_decision_after_entry():
    sessions = [date(2025, 1, 2) + timedelta(days=i) for i in range(3)]
    events = pl.DataFrame(
        {
            "instrument": ["AAA001.SZ"],
            "decision_date": [sessions[2]],
            "entry_intent_date": [sessions[0]],
            "exit_intent_date": [sessions[2]],
        }
    )
    rows = [
        {
            "instrument": "AAA001.SZ",
            "trade_date": s,
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "vol": 1.0,
            "amount": 1.0,
        }
        for s in sessions
    ]
    panel = PricePanel(
        bars=pl.DataFrame(rows),
        calendar=sessions,
        adjustment_as_of="20250104",
        data_fingerprint="fp",
        start=sessions[0],
        end=sessions[-1],
        instruments=["AAA001.SZ"],
    )
    panel.build_index()
    audit = run_pit_audit(events, panel, ResearchConfig(), strict=False)
    assert audit["status"] == "fail"
    assert any("decision_after_entry" in f for f in audit["failures"])
