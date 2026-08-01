"""PIT forward-adjustment: no study-window-end peek."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from qresearch.config.models import ResearchConfig
from qresearch.engines.analysis.pit_audit import run_pit_audit
from qresearch.engines.data.panel import PricePanel
from qresearch.engines.factor.ic import _fwd_return


def _sessions(n: int = 6) -> list[date]:
    start = date(2024, 1, 2)
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_qfq_asof_session_does_not_use_future_factor():
    sessions = _sessions(5)
    # raw close flat 10; adj jumps at sessions[3] (split), then again at sessions[4]
    rows = []
    factors = [1.0, 1.0, 1.0, 2.0, 3.0]
    for s, af in zip(sessions, factors):
        rows.append(
            {
                "instrument": "AAA001.SZ",
                "trade_date": s,
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "vol": 1e5,
                "amount": 1e6,
                "adj_factor": af,
            }
        )
    panel = PricePanel(
        bars=pl.DataFrame(rows),
        calendar=sessions,
        adjustment_as_of="session_pit",
        data_fingerprint="test",
        start=sessions[0],
        end=sessions[-1],
        instruments=["AAA001.SZ"],
        adj_mode="qfq",
    )
    panel.build_index()

    # On day 0, history as-of day0: scale = adj0/adj0 = 1
    b0 = panel.get("AAA001.SZ", sessions[0], asof=sessions[0])
    assert b0 is not None
    assert abs(float(b0["close"]) - 10.0) < 1e-9

    # Early bar viewed as-of mid (before last jump): base=adj[2]=1 → still 10
    b_mid = panel.get("AAA001.SZ", sessions[0], asof=sessions[2])
    assert abs(float(b_mid["close"]) - 10.0) < 1e-9

    # Early bar as-of after split (sessions[3], adj=2): 10 * 1/2 = 5
    b_pit = panel.get("AAA001.SZ", sessions[0], asof=sessions[3])
    assert abs(float(b_pit["close"]) - 5.0) < 1e-9

    # Must NOT equal window-end qfq (base=3): 10 * 1/3
    window_end = 10.0 * 1.0 / 3.0
    assert abs(float(b_pit["close"]) - window_end) > 0.5


def test_fwd_return_uses_horizon_end_asof_not_panel_end():
    sessions = _sessions(6)
    rows = []
    # flat raw 10; factor doubles at sessions[4] (after a 2-day horizon from sessions[1])
    factors = [1.0, 1.0, 1.0, 1.0, 2.0, 2.0]
    for s, af in zip(sessions, factors):
        rows.append(
            {
                "instrument": "AAA001.SZ",
                "trade_date": s,
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "vol": 1e5,
                "amount": 1e6,
                "adj_factor": af,
            }
        )
    panel = PricePanel(
        bars=pl.DataFrame(rows),
        calendar=sessions,
        adjustment_as_of="session_pit",
        data_fingerprint="test",
        start=sessions[0],
        end=sessions[-1],
        instruments=["AAA001.SZ"],
        adj_mode="qfq",
    )
    panel.build_index()
    # entry sessions[1], horizon 2 → end sessions[3]; both asof end, adj=1 → ret=0
    ret = _fwd_return(panel, "AAA001.SZ", sessions[1], 2)
    assert ret is not None
    assert abs(ret) < 1e-9


def test_pit_audit_reports_session_pit():
    sessions = _sessions(4)
    events = pl.DataFrame(
        {
            "instrument": ["AAA001.SZ"],
            "decision_date": [sessions[0]],
            "entry_intent_date": [sessions[0]],
            "exit_intent_date": [sessions[2]],
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
                "vol": 1.0,
                "amount": 1.0,
                "adj_factor": 1.0,
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
                "adj_factor": 1.0,
            }
        )
    panel = PricePanel(
        bars=pl.DataFrame(rows),
        calendar=sessions,
        adjustment_as_of="session_pit",
        data_fingerprint="fp",
        start=sessions[0],
        end=sessions[-1],
        instruments=["000852.SH", "AAA001.SZ"],
        adj_mode="qfq",
    )
    panel.build_index()
    audit = run_pit_audit(events, panel, ResearchConfig(), strict=False)
    assert audit["adjustment"]["methodology"] == "qfq_session_pit"
    assert audit["adjustment"]["full_pit_adj_factor_asof_session"] is True
    assert "adjustment_is_qfq_window_end_not_full_pit" not in audit["warnings"]
