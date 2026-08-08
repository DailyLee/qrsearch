from __future__ import annotations

import json
from datetime import date

import polars as pl

from qresearch.config.models import (
    EntryFilterConfig,
    ExecutionConfig,
    FeatureRefConfig,
    FeatureSourceConfig,
    FilterRule,
    PortfolioConfig,
    ResearchConfig,
    RiskConfig,
    SampleConfig,
    SignalsConfig,
)
from qresearch.engines.backtest.session import run_backtest
from qresearch.engines.backtest.sizing import allocate_shares
from qresearch.engines.data.limitbook import LimitBook
from qresearch.engines.data.panel import PricePanel
from qresearch.engines.risk.pretrade import Intent, select_buy_intents
from qresearch.engines.risk.state import PortfolioState, Position
from qresearch.io.envelope import ExitCode, ResultEnvelope, emit


def _research_config(**updates: object) -> ResearchConfig:
    return ResearchConfig(
        sample=SampleConfig(
            universe="synthetic",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        ),
        features=FeatureSourceConfig(
            refs=[FeatureRefConfig(name="synthetic", availability_lag_sessions=0)]
        ),
        **updates,
    )


def test_cor_gfd_validity_one(panel: PricePanel, events: pl.DataFrame, sessions):
    cfg = _research_config(
        portfolio=PortfolioConfig(starting_cash=100_000, max_weight=0.5, max_new_entries_per_day=2),
        execution=ExecutionConfig(order_validity_sessions=1),
        risk=RiskConfig(stop_loss=None, take_profit=None),
    )
    # force first name limit-up on entry day by patching bar open
    entry = sessions[1]
    key = ("AAA001.SZ", entry)
    bar = dict(panel._by_key[key])
    prev = panel.prior_close("AAA001.SZ", entry)
    bar["open"] = round(prev * 1.1, 2)
    panel._by_key[key] = bar
    res = run_backtest(events, panel, cfg)
    buys = [t for t in res.trades if t["side"] == "buy" and t["instrument"] == "AAA001.SZ"]
    assert buys == []  # expired same day due to limit up + validity=1


def test_cor_gtd_and_decision_prior_close(panel: PricePanel, events: pl.DataFrame, sessions):
    cfg = _research_config(
        portfolio=PortfolioConfig(starting_cash=100_000, max_weight=0.5, max_new_entries_per_day=2),
        execution=ExecutionConfig(
            order_validity_sessions=5,
            entry_filter=EntryFilterConfig(
                enabled=True,
                min_open_ret=-0.5,
                max_open_ret=0.5,
                ref="decision_prior_close",
            ),
        ),
        risk=RiskConfig(stop_loss=None, take_profit=None),
    )
    entry = sessions[1]
    # day1 limit up
    bar = dict(panel._by_key[("AAA001.SZ", entry)])
    prev = panel.prior_close("AAA001.SZ", entry)
    bar["open"] = round(prev * 1.1, 2)
    panel._by_key[("AAA001.SZ", entry)] = bar
    res = run_backtest(events, panel, cfg)
    buys = [t for t in res.trades if t["side"] == "buy" and t["instrument"] == "AAA001.SZ"]
    assert buys, "should fill on a later session"
    assert buys[0]["session"] > str(entry)


def test_cor_size_allocate():
    from qresearch.config.models import CostsConfig

    qtys = allocate_shares(
        [10.0, 20.0],
        cash=10_000,
        portfolio=PortfolioConfig(max_weight=0.5, lot_size=100),
        costs=CostsConfig(commission_rate=0.0, commission_min=0.0, stamp_duty_rate=0.0, slippage_bps=0.0),
    )
    assert all(q % 100 == 0 for q in qtys)
    assert sum(q * p for q, p in zip(qtys, [10.0, 20.0])) <= 10_000 + 1e-6
    assert all(q * p <= 10_000 * 0.5 + 1e-6 for q, p in zip(qtys, [10.0, 20.0]) if q)


def test_cor_t1(panel: PricePanel, events: pl.DataFrame, sessions):
    # one event same-day exit intent
    ev = events.head(1).with_columns(
        pl.lit(sessions[1]).alias("exit_intent_date"),
    )
    cfg = _research_config(
        portfolio=PortfolioConfig(starting_cash=100_000, max_weight=0.8, max_new_entries_per_day=1),
        execution=ExecutionConfig(order_validity_sessions=1),
        risk=RiskConfig(stop_loss=-0.9, take_profit=None),  # huge stop unlikely same open
    )
    res = run_backtest(ev, panel, cfg)
    buys = [t for t in res.trades if t["side"] == "buy"]
    assert buys
    buy_day = buys[0]["session"]
    sells_same = [t for t in res.trades if t["side"] == "sell" and t["session"] == buy_day]
    assert sells_same == []
    sells = [t for t in res.trades if t["side"] == "sell"]
    assert sells, "should sell after T+1"


def test_cor_pretrade_state_sensitive(panel: PricePanel, sessions):
    cfg_port = PortfolioConfig(max_new_entries_per_day=1, max_names=1)
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
    empty = PortfolioState(cash=1e6)
    allowed_empty, _ = select_buy_intents(
        intents, empty, cfg_port, panel, sessions[1], LimitBook()
    )
    full = PortfolioState(cash=1e6)
    full.positions["AAA001.SZ"] = Position(
        instrument="AAA001.SZ",
        qty=100,
        entry_price=10,
        entry_session=sessions[0],
        exit_intent_date=sessions[10],
        cost_basis=1000,
    )
    allowed_full, _ = select_buy_intents(
        intents, full, cfg_port, panel, sessions[1], LimitBook()
    )
    assert len(allowed_empty) == 1
    assert len(allowed_full) == 0


def test_agt_envelope_json_loads(capsys):
    env = ResultEnvelope(
        command="test",
        started_at="t0",
        finished_at="t1",
        summary={"x": 1},
        artifacts={"a": "b"},
    )
    code = emit(env, format="json", quiet=True, exit_code=ExitCode.OK)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["schema_version"] == "1.0"
    assert data["ok"] is True
    assert code == 0
    assert "\x1b" not in captured.out


def test_exit_priority_stop_before_tp(panel: PricePanel, events: pl.DataFrame, sessions):
    # hold one name, craft bar that hits both stop and tp
    ev = events.head(1)
    cfg = _research_config(
        portfolio=PortfolioConfig(starting_cash=100_000, max_weight=0.8, max_new_entries_per_day=1),
        execution=ExecutionConfig(order_validity_sessions=1),
        risk=RiskConfig(stop_loss=-0.02, take_profit=0.02),
    )
    res = run_backtest(ev, panel, cfg)
    buys = [t for t in res.trades if t["side"] == "buy"]
    assert buys
    # mutate next day bar extremes
    buy_session = date.fromisoformat(buys[0]["session"])
    idx = panel.calendar.index(buy_session)
    nxt = panel.calendar[idx + 1]
    bar = dict(panel._by_key[("AAA001.SZ", nxt)])
    entry_px = buys[0]["price"]
    bar["low"] = entry_px * 0.9
    bar["high"] = entry_px * 1.1
    bar["open"] = entry_px
    bar["close"] = entry_px
    panel._by_key[("AAA001.SZ", nxt)] = bar
    # re-run from clean - simpler assert on logic via second backtest with wild bars from day1
    # Instead unit-check: after full run with modified panel from start
    # Reset events and panel already mutated for nxt only; run again
    res2 = run_backtest(ev, panel, cfg)
    sells = [t for t in res2.trades if t["side"] == "sell"]
    assert sells
    assert sells[0]["reason"] == "stop"
