from __future__ import annotations

from qresearch.config.models import PortfolioConfig
from qresearch.engines.data.limitbook import LimitBook
from qresearch.engines.risk.pretrade import Intent, select_buy_intents
from qresearch.engines.risk.state import PortfolioState, Position


def _intent(inst: str, session, rank: float, industry: str | None) -> Intent:
    feats = {"features.industry": industry} if industry is not None else {}
    return Intent(
        instrument=inst,
        side="buy",
        decision_date=session,
        entry_intent_date=session,
        exit_intent_date=session,
        rank_score=rank,
        features=feats or None,
    )


def test_industry_caps_null_matches_quota_only(panel, sessions):
    s = sessions[1]
    intents = [
        _intent("AAA001.SZ", s, 0, "银行"),
        _intent("AAA002.SZ", s, 1, "银行"),
    ]
    state = PortfolioState(cash=1e6)
    allowed, rejects = select_buy_intents(
        intents,
        state,
        PortfolioConfig(max_new_entries_per_day=1),
        panel,
        s,
        LimitBook(),
    )
    assert [a.instrument for a in allowed] == ["AAA001.SZ"]
    assert any(
        r["instrument"] == "AAA002.SZ" and r["reason"] == "max_new_entries_or_max_names"
        for r in rejects
    )


def test_max_new_per_industry_per_day(panel, sessions):
    s = sessions[1]
    intents = [
        _intent("AAA001.SZ", s, 0, "银行"),
        _intent("AAA002.SZ", s, 1, "银行"),
    ]
    state = PortfolioState(cash=1e6)
    allowed, rejects = select_buy_intents(
        intents,
        state,
        PortfolioConfig(max_new_entries_per_day=10, max_new_per_industry_per_day=1),
        panel,
        s,
        LimitBook(),
    )
    assert [a.instrument for a in allowed] == ["AAA001.SZ"]
    assert any(
        r["instrument"] == "AAA002.SZ" and r["reason"] == "industry_daily_cap" for r in rejects
    )


def test_max_names_per_industry_held(panel, sessions):
    s = sessions[1]
    state = PortfolioState(cash=1e6)
    state.positions["HELD.SZ"] = Position(
        instrument="HELD.SZ",
        qty=100,
        entry_price=10.0,
        entry_session=sessions[0],
        exit_intent_date=sessions[10],
        cost_basis=1000.0,
        industry="银行",
    )
    intents = [
        _intent("AAA001.SZ", s, 0, "银行"),
        _intent("AAA002.SZ", s, 1, "电子"),
    ]
    allowed, rejects = select_buy_intents(
        intents,
        state,
        PortfolioConfig(max_new_entries_per_day=10, max_names_per_industry=1),
        panel,
        s,
        LimitBook(),
    )
    assert [a.instrument for a in allowed] == ["AAA002.SZ"]
    assert any(
        r["instrument"] == "AAA001.SZ" and r["reason"] == "industry_held_cap" for r in rejects
    )


def test_missing_industry_allowed_and_uncounted(panel, sessions):
    s = sessions[1]
    intents = [
        _intent("AAA001.SZ", s, 0, "银行"),
        _intent("AAA002.SZ", s, 1, None),
    ]
    state = PortfolioState(cash=1e6)
    allowed, rejects = select_buy_intents(
        intents,
        state,
        PortfolioConfig(max_new_entries_per_day=10, max_new_per_industry_per_day=1),
        panel,
        s,
        LimitBook(),
    )
    assert [a.instrument for a in allowed] == ["AAA001.SZ", "AAA002.SZ"]
    assert rejects == []
