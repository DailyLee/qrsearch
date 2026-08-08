from __future__ import annotations

from conftest import research_config

from datetime import date

import polars as pl
import pytest

from qresearch.config.models import (
    CostsConfig,
    EntryFilterConfig,
    ExecutionConfig,
    PortfolioConfig,
    ResearchConfig,
    RiskConfig,
)
from qresearch.engines.backtest.session import (
    _entry_filter_ok,
    _ref_close,
    events_to_intents,
    run_backtest,
)
from qresearch.engines.backtest.sizing import allocate_shares
from qresearch.engines.data.panel import PricePanel


def test_ref_close_decision_vs_session(panel: PricePanel, sessions):
    decision = sessions[2]
    session = sessions[5]
    d_prior = panel.prior_close("AAA001.SZ", decision)
    s_prior = panel.prior_close("AAA001.SZ", session)
    assert _ref_close(panel, "AAA001.SZ", session, decision, "decision_prior_close") == d_prior
    assert _ref_close(panel, "AAA001.SZ", session, decision, "session_prior_close") == s_prior


def test_entry_filter_min_max_bounds(panel: PricePanel, sessions):
    decision = sessions[2]
    session = sessions[2]
    ref = panel.prior_close("AAA001.SZ", decision)
    assert ref is not None
    cfg = research_config(
        execution=ExecutionConfig(
            price="open",
            entry_filter=EntryFilterConfig(
                enabled=True,
                min_open_ret=-0.01,
                max_open_ret=0.01,
                ref="decision_prior_close",
            ),
        )
    )
    ok_bar = {"open": ref * 1.005}
    bad_hi = {"open": ref * 1.05}
    bad_lo = {"open": ref * 0.95}
    assert _entry_filter_ok(cfg, panel, "AAA001.SZ", session, decision, ok_bar)[0] is True
    assert _entry_filter_ok(cfg, panel, "AAA001.SZ", session, decision, bad_hi) == (
        False,
        "entry_filter_max",
    )
    assert _entry_filter_ok(cfg, panel, "AAA001.SZ", session, decision, bad_lo) == (
        False,
        "entry_filter_min",
    )


def test_entry_filter_empty_bar_and_data_gap(panel: PricePanel, events: pl.DataFrame, sessions):
    """Missing panel bar → data_gap reject; empty open → missing_entry_price."""
    decision = sessions[2]
    session = sessions[2]
    cfg = research_config(
        execution=ExecutionConfig(
            price="open",
            entry_filter=EntryFilterConfig(
                enabled=True,
                max_open_ret=0.5,
                ref="decision_prior_close",
            ),
        )
    )
    assert _entry_filter_ok(cfg, panel, "AAA001.SZ", session, decision, {}) == (
        False,
        "missing_entry_price",
    )
    assert _entry_filter_ok(
        cfg, panel, "AAA001.SZ", session, decision, {"open": None}
    ) == (False, "missing_entry_price")

    # instrument with no bars in panel → buy rejected as data_gap
    orphan = events.head(1).with_columns(pl.lit("ZZZ999.SH").alias("instrument"))
    res = run_backtest(orphan, panel, cfg)
    assert any(r.get("reason") == "data_gap" for r in res.rejects)
    assert not any(
        t.get("side") == "buy" and t.get("instrument") == "ZZZ999.SH" for t in res.trades
    )


def test_lag_sessions_delays_first_buy(panel: PricePanel, events: pl.DataFrame, sessions):
    ev = events.head(1)
    entry = sessions[1]
    cfg = research_config(
        portfolio=PortfolioConfig(starting_cash=100_000, max_weight=0.8, max_new_entries_per_day=1),
        execution=ExecutionConfig(order_validity_sessions=5, lag_sessions=1),
        risk=RiskConfig(stop_loss=None, take_profit=None, max_hold_sessions=100),
    )
    res = run_backtest(ev, panel, cfg)
    buys = [t for t in res.trades if t["side"] == "buy"]
    assert buys
    assert buys[0]["session"] == str(sessions[2])  # entry_intent + 1 session
    assert buys[0]["session"] != str(entry)


def test_max_hold_exit(panel: PricePanel, events: pl.DataFrame, sessions):
    ev = events.head(1).with_columns(pl.lit(sessions[20]).alias("exit_intent_date"))
    cfg = research_config(
        portfolio=PortfolioConfig(starting_cash=100_000, max_weight=0.8, max_new_entries_per_day=1),
        execution=ExecutionConfig(order_validity_sessions=1),
        risk=RiskConfig(stop_loss=None, take_profit=None, max_hold_sessions=3),
    )
    res = run_backtest(ev, panel, cfg)
    sells = [t for t in res.trades if t["side"] == "sell"]
    assert sells
    assert sells[0]["reason"] == "max_hold"


def test_sell_blocked_limit_down_sets_pending(panel: PricePanel, events: pl.DataFrame, sessions):
    ev = events.head(1)
    cfg = research_config(
        portfolio=PortfolioConfig(starting_cash=100_000, max_weight=0.8, max_new_entries_per_day=1),
        execution=ExecutionConfig(order_validity_sessions=1),
        risk=RiskConfig(stop_loss=None, take_profit=None, max_hold_sessions=100),
    )
    # first run to discover buy session, then craft limit-down on exit day
    res0 = run_backtest(ev, panel, cfg)
    buys = [t for t in res0.trades if t["side"] == "buy"]
    assert buys
    buy_session = date.fromisoformat(buys[0]["session"])
    exit_day = sessions[5]  # matches fixture exit_intent for first event
    assert exit_day > buy_session

    bar = dict(panel._by_key[("AAA001.SZ", exit_day)])
    bar["open"] = 8.76
    bar["high"] = bar["open"]
    bar["low"] = bar["open"]
    bar["close"] = bar["open"]
    bar["down_limit"] = 8.76
    panel._by_key[("AAA001.SZ", exit_day)] = bar

    res = run_backtest(ev, panel, cfg)
    blocked = [r for r in res.rejects if str(r.get("reason", "")).startswith("sell_blocked_")]
    assert blocked
    assert any(r.get("exit_reason") == "exit_intent" for r in blocked)
    # should eventually sell on a later non-limit-down day (or remain blocked if all locked)
    sells_on_exit = [
        t
        for t in res.trades
        if t["side"] == "sell" and t["session"] == str(exit_day) and t["instrument"] == "AAA001.SZ"
    ]
    assert sells_on_exit == []


def test_events_to_intents_maps_features(events: pl.DataFrame):
    intents = events_to_intents(events)
    assert len(intents) == 2
    assert intents[0].instrument == "AAA001.SZ"
    assert "features.box_quality" in (intents[0].features or {})


def test_buy_uses_close_when_execution_price_close(panel: PricePanel, events: pl.DataFrame, sessions):
    ev = events.head(1)
    cfg = research_config(
        portfolio=PortfolioConfig(starting_cash=100_000, max_weight=0.8, max_new_entries_per_day=1),
        execution=ExecutionConfig(price="close", order_validity_sessions=1),
        risk=RiskConfig(stop_loss=None, take_profit=None, max_hold_sessions=100),
        costs=CostsConfig(commission_rate=0.0, commission_min=0.0, stamp_duty_rate=0.0, slippage_bps=0.0),
    )
    res = run_backtest(ev, panel, cfg)
    buys = [t for t in res.trades if t["side"] == "buy"]
    assert buys
    buy_day = date.fromisoformat(buys[0]["session"])
    bar = panel.get("AAA001.SZ", buy_day)
    assert bar is not None
    assert buys[0]["price"] == pytest.approx(float(bar["close"]))
    assert buys[0]["price"] != pytest.approx(float(bar["open"])) or float(bar["open"]) == float(
        bar["close"]
    )


def test_take_profit_fill_price(panel: PricePanel, events: pl.DataFrame, sessions):
    ev = events.head(1).with_columns(pl.lit(sessions[20]).alias("exit_intent_date"))
    cfg = research_config(
        portfolio=PortfolioConfig(starting_cash=100_000, max_weight=0.8, max_new_entries_per_day=1),
        execution=ExecutionConfig(order_validity_sessions=1, price="open"),
        risk=RiskConfig(stop_loss=None, take_profit=0.02, max_hold_sessions=100),
        costs=CostsConfig(commission_rate=0.0, commission_min=0.0, stamp_duty_rate=0.0, slippage_bps=0.0),
    )
    # discover buy, then force TP touch next session without gap-through open
    res0 = run_backtest(ev, panel, cfg)
    buys0 = [t for t in res0.trades if t["side"] == "buy"]
    assert buys0
    buy_session = date.fromisoformat(buys0[0]["session"])
    entry_px = buys0[0]["price"]
    nxt = panel.next_session(buy_session, 1)
    assert nxt is not None
    tp_px = entry_px * 1.02
    bar = dict(panel._by_key[("AAA001.SZ", nxt)])
    bar["open"] = round(entry_px * 1.005, 2)  # open below TP
    bar["high"] = round(entry_px * 1.05, 2)  # high clears TP
    bar["low"] = round(entry_px * 0.99, 2)
    bar["close"] = round(entry_px * 1.01, 2)
    panel._by_key[("AAA001.SZ", nxt)] = bar

    res = run_backtest(ev, panel, cfg)
    sells = [t for t in res.trades if t["side"] == "sell"]
    assert sells
    assert sells[0]["reason"] == "take_profit"
    assert sells[0]["price"] == pytest.approx(max(float(bar["open"]), tp_px))


def test_insufficient_cash_or_lot_reject(panel: PricePanel, events: pl.DataFrame, sessions):
    ev = events.head(1)
    cfg = research_config(
        portfolio=PortfolioConfig(
            starting_cash=50.0,  # less than 1 lot * ~10
            max_weight=1.0,
            max_new_entries_per_day=1,
            lot_size=100,
        ),
        execution=ExecutionConfig(order_validity_sessions=1),
        risk=RiskConfig(stop_loss=None, take_profit=None, max_hold_sessions=100),
    )
    res = run_backtest(ev, panel, cfg)
    assert [t for t in res.trades if t["side"] == "buy"] == []
    assert any(r["reason"] == "insufficient_cash_or_lot" for r in res.rejects)


def test_entry_filter_rejects_then_retries(panel: PricePanel, events: pl.DataFrame, sessions):
    ev = events.head(1)
    entry = sessions[1]
    decision = sessions[1]
    ref = panel.prior_close("AAA001.SZ", decision)
    assert ref is not None
    # day1: gap up beyond filter; day2+: normal open so filter passes
    bar1 = dict(panel._by_key[("AAA001.SZ", entry)])
    bar1["open"] = round(ref * 1.08, 2)
    bar1["high"] = max(bar1["high"], bar1["open"])
    bar1["low"] = min(bar1["low"], bar1["open"])
    panel._by_key[("AAA001.SZ", entry)] = bar1

    cfg = research_config(
        portfolio=PortfolioConfig(starting_cash=100_000, max_weight=0.8, max_new_entries_per_day=1),
        execution=ExecutionConfig(
            order_validity_sessions=5,
            entry_filter=EntryFilterConfig(
                enabled=True,
                min_open_ret=-0.5,
                max_open_ret=0.03,
                ref="decision_prior_close",
            ),
        ),
        risk=RiskConfig(stop_loss=None, take_profit=None, max_hold_sessions=100),
    )
    res = run_backtest(ev, panel, cfg)
    assert any(r["reason"] == "entry_filter_max" for r in res.rejects)
    buys = [t for t in res.trades if t["side"] == "buy" and t["instrument"] == "AAA001.SZ"]
    assert buys
    assert buys[0]["session"] > str(entry)


def test_data_gap_on_entry_retries(panel: PricePanel, events: pl.DataFrame, sessions):
    ev = events.head(1)
    entry = sessions[1]
    del panel._by_key[("AAA001.SZ", entry)]
    cfg = research_config(
        portfolio=PortfolioConfig(starting_cash=100_000, max_weight=0.8, max_new_entries_per_day=1),
        execution=ExecutionConfig(order_validity_sessions=5),
        risk=RiskConfig(stop_loss=None, take_profit=None, max_hold_sessions=100),
    )
    res = run_backtest(ev, panel, cfg)
    assert any(r["reason"] == "data_gap" and r.get("instrument") == "AAA001.SZ" for r in res.rejects)
    buys = [t for t in res.trades if t["side"] == "buy" and t["instrument"] == "AAA001.SZ"]
    assert buys
    assert buys[0]["session"] > str(entry)


def test_deferred_exit_fills_after_limit_down_block(panel: PricePanel, events: pl.DataFrame, sessions):
    """Stop triggers but limit-down blocks sell; next day deferred_exit path fills."""
    ev = events.head(1).with_columns(pl.lit(sessions[25]).alias("exit_intent_date"))
    cfg = research_config(
        portfolio=PortfolioConfig(starting_cash=100_000, max_weight=0.8, max_new_entries_per_day=1),
        execution=ExecutionConfig(order_validity_sessions=1, price="open"),
        risk=RiskConfig(stop_loss=-0.02, take_profit=None, max_hold_sessions=100),
        costs=CostsConfig(commission_rate=0.0, commission_min=0.0, stamp_duty_rate=0.0, slippage_bps=0.0),
    )
    res0 = run_backtest(ev, panel, cfg)
    buys0 = [t for t in res0.trades if t["side"] == "buy"]
    assert buys0
    buy_session = date.fromisoformat(buys0[0]["session"])
    entry_px = float(buys0[0]["price"])
    stop_day = panel.next_session(buy_session, 1)
    fill_day = panel.next_session(stop_day, 1)
    assert stop_day and fill_day

    stop_bar = dict(panel._by_key[("AAA001.SZ", stop_day)])
    # limit-down open + low through stop → trigger stop but cannot sell
    ld = 8.76
    stop_bar["open"] = ld
    stop_bar["high"] = ld
    stop_bar["low"] = min(ld, round(entry_px * 0.9, 2))
    stop_bar["close"] = ld
    stop_bar["down_limit"] = ld
    panel._by_key[("AAA001.SZ", stop_day)] = stop_bar

    # next day: tradable, but do NOT re-trigger stop (low above stop)
    fill_bar = dict(panel._by_key[("AAA001.SZ", fill_day)])
    fill_bar["open"] = round(entry_px * 0.99, 2)
    fill_bar["high"] = round(entry_px * 1.01, 2)
    fill_bar["low"] = round(entry_px * 0.985, 2)  # above stop (-2%)
    fill_bar["close"] = round(entry_px * 0.995, 2)
    panel._by_key[("AAA001.SZ", fill_day)] = fill_bar

    res = run_backtest(ev, panel, cfg)
    blocked = [
        r
        for r in res.rejects
        if r.get("instrument") == "AAA001.SZ" and str(r.get("reason", "")).startswith("sell_blocked_")
    ]
    assert blocked
    assert any(r.get("exit_reason") == "stop" for r in blocked)

    sells = [t for t in res.trades if t["side"] == "sell" and t["instrument"] == "AAA001.SZ"]
    assert sells
    assert sells[0]["session"] == str(fill_day)
    assert sells[0]["reason"] == "stop"  # deferred_exit reuses pending reason
    assert sells[0]["price"] == pytest.approx(float(fill_bar["open"]))


def test_allocate_shares_respects_fees_and_zero_price():
    costs = CostsConfig(commission_rate=0.001, commission_min=0.0, stamp_duty_rate=0.0, slippage_bps=0.0)
    qtys = allocate_shares(
        [10.0, 0.0, 20.0],
        cash=5_000,
        portfolio=PortfolioConfig(max_weight=0.5, lot_size=100),
        costs=costs,
    )
    assert qtys[1] == 0
    assert all(q % 100 == 0 for q in qtys)
    # fee-inclusive notional must fit cash / max_weight
    spent = 0.0
    for px, q in zip([10.0, 0.0, 20.0], qtys):
        if q <= 0:
            continue
        notional = px * q
        spent += notional + notional * 0.001
        assert notional <= 5_000 * 0.5 + 1e-6
    assert spent <= 5_000 + 1e-6
