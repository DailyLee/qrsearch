from __future__ import annotations

from datetime import date

import pytest

from qresearch.config.models import CostsConfig
from qresearch.engines.backtest.costs import buy_cost, commission, sell_cost
from qresearch.engines.data.limitbook import LimitBook
from qresearch.engines.risk.pretrade import Intent, select_buy_intents
from qresearch.engines.risk.state import PortfolioState, Position


def test_commission_respects_minimum():
    costs = CostsConfig(
        commission_rate=0.00034, commission_min=5.0, transfer_fee_rate=0.0, slippage_bps=0.0
    )
    assert commission(1_000.0, costs) == 5.0  # 0.34 < 5
    assert commission(100_000.0, costs) == pytest.approx(34.0)


def test_default_costs_match_broker_spec():
    costs = CostsConfig()
    assert costs.commission_rate == pytest.approx(0.00008)
    assert costs.commission_min == 0.0
    assert costs.stamp_duty_rate == pytest.approx(0.0005)
    assert costs.transfer_fee_rate == pytest.approx(0.00001)
    assert costs.slippage_bps == pytest.approx(10.0)
    notional = 100_000.0
    # buy: 8 + 100 slip + 1 transfer = 109
    assert buy_cost(notional, costs) == pytest.approx(109.0)
    # sell: 8 + 100 + 50 stamp + 1 transfer = 159
    assert sell_cost(notional, costs) == pytest.approx(159.0)


def test_commission_and_costs_with_slip_and_stamp():
    costs = CostsConfig(
        commission_rate=0.001,
        commission_min=0.0,
        stamp_duty_rate=0.001,
        transfer_fee_rate=0.0,
        slippage_bps=10.0,  # 0.1%
    )
    notional = 10_000.0
    assert commission(notional, costs) == 10.0
    assert buy_cost(notional, costs) == pytest.approx(20.0)  # commission + slip
    assert sell_cost(notional, costs) == pytest.approx(30.0)  # + stamp


def test_limitbook_buy_sell_gates():
    lb = LimitBook(up_pct=0.1, down_pct=0.1)
    prev = 10.0
    assert lb.limit_up_price(prev) == 11.0
    assert lb.limit_down_price(prev) == 9.0

    assert lb.can_buy_open(None, prev) == (False, "suspended")
    assert lb.can_buy_open({"open": 11.0, "vol": 100}, prev) == (False, "limit_up")
    assert lb.can_buy_open({"open": 10.5, "vol": 100}, prev) == (True, "ok")
    assert lb.can_buy_open({"open": 10.5, "vol": 0}, prev) == (False, "suspended")

    assert lb.can_sell_open({"open": 9.0, "vol": 100}, prev) == (False, "limit_down")
    assert lb.can_sell_open({"open": 9.5, "vol": 100}, prev) == (True, "ok")
    assert lb.can_sell_open(None, prev) == (False, "suspended")


def test_portfolio_state_nav_and_names():
    state = PortfolioState(cash=50_000.0)
    assert state.n_names() == 0
    assert state.nav() == 50_000.0

    state.positions["AAA001.SZ"] = Position(
        instrument="AAA001.SZ",
        qty=100,
        entry_price=10.0,
        entry_session=date(2024, 1, 2),
        exit_intent_date=date(2024, 1, 10),
        cost_basis=1_000.0,
    )
    assert state.n_names() == 1
    assert state.position_cost() == 1_000.0
    assert state.nav() == 50_000.0 + 100 * 10.0
    assert state.nav({"AAA001.SZ": 12.0}) == 50_000.0 + 100 * 12.0


def test_select_buy_intents_rejects_already_held_and_quota(panel, sessions):
    from qresearch.config.models import PortfolioConfig

    intents = [
        Intent(
            instrument="AAA001.SZ",
            side="buy",
            decision_date=sessions[1],
            entry_intent_date=sessions[1],
            exit_intent_date=sessions[5],
            rank_score=0,
        ),
        Intent(
            instrument="AAA002.SZ",
            side="buy",
            decision_date=sessions[1],
            entry_intent_date=sessions[1],
            exit_intent_date=sessions[5],
            rank_score=1,
        ),
    ]
    state = PortfolioState(cash=1e6)
    state.positions["AAA001.SZ"] = Position(
        instrument="AAA001.SZ",
        qty=100,
        entry_price=10.0,
        entry_session=sessions[0],
        exit_intent_date=sessions[10],
        cost_basis=1000.0,
    )
    allowed, rejects = select_buy_intents(
        intents,
        state,
        PortfolioConfig(max_new_entries_per_day=2, max_names=None),
        panel,
        sessions[1],
        LimitBook(),
    )
    assert [a.instrument for a in allowed] == ["AAA002.SZ"]
    assert any(r["reason"] == "already_held" for r in rejects)
