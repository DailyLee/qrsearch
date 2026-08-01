"""Session engine: GTD entry, T+1, exits, costs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import polars as pl

from qresearch.config.models import ResearchConfig
from qresearch.engines.backtest.costs import buy_cost, sell_cost
from qresearch.engines.backtest.sizing import allocate_shares
from qresearch.engines.data.limitbook import LimitBook
from qresearch.engines.data.panel import PricePanel
from qresearch.engines.risk.pretrade import Intent, select_buy_intents
from qresearch.engines.risk.state import PortfolioState, Position


@dataclass
class BacktestResult:
    equity: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    rejects: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


def _as_date(v: object) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _ref_close(
    panel: PricePanel,
    instrument: str,
    session: date,
    decision_date: date,
    ref: str,
) -> float | None:
    if ref == "decision_prior_close":
        # prior close of decision_date session
        return panel.prior_close(instrument, decision_date)
    return panel.prior_close(instrument, session)


def _entry_filter_ok(
    config: ResearchConfig,
    panel: PricePanel,
    instrument: str,
    session: date,
    decision_date: date,
    bar: dict,
) -> tuple[bool, str]:
    ef = config.execution.entry_filter
    if not ef.enabled:
        return True, "ok"
    ref = _ref_close(panel, instrument, session, decision_date, ef.ref)
    if ref is None or ref <= 0:
        return False, "missing_ref_close"
    ret = float(bar[config.execution.price]) / ref - 1.0
    if ef.min_open_ret is not None and ret < ef.min_open_ret:
        return False, "entry_filter_min"
    if ef.max_open_ret is not None and ret > ef.max_open_ret:
        return False, "entry_filter_max"
    return True, "ok"


def events_to_intents(ranked: pl.DataFrame) -> list[Intent]:
    intents = []
    for r in ranked.iter_rows(named=True):
        feats = {k: v for k, v in r.items() if str(k).startswith("features.")}
        intents.append(
            Intent(
                instrument=r["instrument"],
                side="buy",
                decision_date=_as_date(r["decision_date"]),
                entry_intent_date=_as_date(r["entry_intent_date"]),
                exit_intent_date=_as_date(r["exit_intent_date"]),
                rank_score=float(r.get("rank_score") or 0.0),
                features=feats,
            )
        )
    return intents


def run_backtest(
    ranked: pl.DataFrame,
    panel: PricePanel,
    config: ResearchConfig,
) -> BacktestResult:
    limitbook = LimitBook()
    state = PortfolioState(cash=config.portfolio.starting_cash)
    result = BacktestResult()

    # pending GTD orders keyed by instrument list
    pending: list[dict[str, Any]] = []
    all_intents = events_to_intents(ranked)

    # index intents by first attempt session
    for it in all_intents:
        first = panel.next_session(it.entry_intent_date, config.execution.lag_sessions)
        if first is None:
            # if entry_intent_date itself is a session
            if it.entry_intent_date in panel.calendar:
                first = it.entry_intent_date
                for _ in range(config.execution.lag_sessions):
                    nxt = panel.next_session(first, 1)
                    if nxt is None:
                        break
                    first = nxt
            else:
                # find first calendar >= entry
                for d in panel.calendar:
                    if d >= it.entry_intent_date:
                        first = d
                        break
        if first is None:
            result.rejects.append(
                {
                    "instrument": it.instrument,
                    "reason": "no_session_for_entry",
                    "entry_intent_date": str(it.entry_intent_date),
                }
            )
            continue
        pending.append(
            {
                "intent": it,
                "first_session": first,
                "expiry_session": panel.next_session(
                    first, max(config.execution.order_validity_sessions - 1, 0)
                )
                or first,
                "attempts": 0,
            }
        )

    cal = [d for d in panel.calendar if panel.start <= d <= panel.end]
    for session in cal:
        state.asof = session

        # --- exits first (sells) ---
        _process_exits(state, panel, config, limitbook, session, result)

        # --- activate pending buys for today ---
        todays: list[Intent] = []
        still_pending = []
        for p in pending:
            it: Intent = p["intent"]
            first = p["first_session"]
            expiry = p["expiry_session"]
            if session < first:
                still_pending.append(p)
                continue
            if session > expiry:
                result.rejects.append(
                    {
                        "session": str(session),
                        "instrument": it.instrument,
                        "reason": "order_expired",
                    }
                )
                continue
            # eligibility besides limit checked in pretrade
            bar = panel.get(it.instrument, session)
            ok_f, why_f = _entry_filter_ok(
                config, panel, it.instrument, session, it.decision_date, bar or {}
            )
            if bar is None:
                still_pending.append(p)
                result.rejects.append(
                    {
                        "session": str(session),
                        "instrument": it.instrument,
                        "reason": "data_gap",
                    }
                )
                continue
            if not ok_f:
                # retry later if validity remains
                if session < expiry:
                    still_pending.append(p)
                result.rejects.append(
                    {
                        "session": str(session),
                        "instrument": it.instrument,
                        "reason": why_f,
                    }
                )
                continue
            todays.append(it)
            # keep in pending until filled; remove if selected and filled below
            p["_active_today"] = True
            still_pending.append(p)
        pending = still_pending

        allowed, rejects = select_buy_intents(
            todays, state, config.portfolio, panel, session, limitbook
        )
        result.rejects.extend(rejects)

        if allowed:
            price_key = config.execution.price
            prices = []
            for it in allowed:
                bar = panel.get(it.instrument, session)
                prices.append(float(bar[price_key]))
            budget = state.cash if config.portfolio.sizing_base == "cash" else state.nav()
            qtys = allocate_shares(prices, budget, config.portfolio, config.costs)
            filled_inst = set()
            for it, px, qty in zip(allowed, prices, qtys):
                if qty <= 0:
                    result.rejects.append(
                        {
                            "session": str(session),
                            "instrument": it.instrument,
                            "reason": "insufficient_cash_or_lot",
                        }
                    )
                    continue
                notional = px * qty
                fee = buy_cost(notional, config.costs)
                total = notional + fee
                if total > state.cash + 1e-6:
                    result.rejects.append(
                        {
                            "session": str(session),
                            "instrument": it.instrument,
                            "reason": "insufficient_cash",
                        }
                    )
                    continue
                state.cash -= total
                state.fees_paid += fee
                state.positions[it.instrument] = Position(
                    instrument=it.instrument,
                    qty=qty,
                    entry_price=px,
                    entry_session=session,
                    exit_intent_date=it.exit_intent_date,
                    cost_basis=notional,
                )
                result.trades.append(
                    {
                        "session": str(session),
                        "instrument": it.instrument,
                        "side": "buy",
                        "qty": qty,
                        "price": px,
                        "fee": fee,
                        "reason": "entry",
                    }
                )
                filled_inst.add(it.instrument)

            # drop filled from pending
            pending = [
                p
                for p in pending
                if p["intent"].instrument not in filled_inst
                or not p.get("_active_today")
                or p["intent"].instrument not in filled_inst
            ]
            pending = [p for p in pending if p["intent"].instrument not in filled_inst]

        # mark to market equity
        marks = {}
        for inst, pos in state.positions.items():
            bar = panel.get(inst, session)
            marks[inst] = float(bar["close"]) if bar else pos.entry_price
        result.equity.append(
            {
                "session": str(session),
                "cash": state.cash,
                "nav": state.nav(marks),
                "n_positions": state.n_names(),
            }
        )

    result.metrics = compute_metrics(
        result.equity,
        result.trades,
        config.portfolio.starting_cash,
        panel=panel,
        config=config,
    )
    return result


def _process_exits(
    state: PortfolioState,
    panel: PricePanel,
    config: ResearchConfig,
    limitbook: LimitBook,
    session: date,
    result: BacktestResult,
) -> None:
    to_close: list[tuple[str, str]] = []  # instrument, reason
    for inst, pos in list(state.positions.items()):
        # T+1
        if session <= pos.entry_session:
            continue
        bar = panel.get(inst, session)
        prev = panel.prior_close(inst, session)
        can_sell, why = limitbook.can_sell_open(bar, prev)
        # determine reason by priority
        reason = pos.pending_exit_reason
        px_open = float(bar["open"]) if bar else None
        px_close = float(bar["close"]) if bar else None
        px_high = float(bar["high"]) if bar else None
        px_low = float(bar["low"]) if bar else None

        triggered = None
        for key in config.risk.exit_priority:
            if key == "stop" and config.risk.stop_loss is not None and px_low is not None:
                stop_px = pos.entry_price * (1.0 + config.risk.stop_loss)
                if px_low <= stop_px:
                    triggered = ("stop", stop_px)
                    break
            if key == "take_profit" and config.risk.take_profit is not None and px_high is not None:
                tp_px = pos.entry_price * (1.0 + config.risk.take_profit)
                if px_high >= tp_px:
                    triggered = ("take_profit", tp_px)
                    break
            if key == "max_hold" and config.risk.max_hold_sessions is not None:
                # sessions held
                try:
                    i0 = panel.calendar.index(pos.entry_session)
                    i1 = panel.calendar.index(session)
                    held = i1 - i0
                except ValueError:
                    held = (session - pos.entry_session).days
                if held >= config.risk.max_hold_sessions:
                    triggered = ("max_hold", px_close or px_open)
                    break
            if key == "exit_intent" and session >= pos.exit_intent_date:
                triggered = ("exit_intent", px_close or px_open)
                break
            if key == "deferred_exit" and reason:
                triggered = (reason, px_close or px_open)
                break

        if triggered is None:
            continue
        exit_reason, ideal_px = triggered
        if not can_sell:
            pos.pending_exit_reason = exit_reason
            result.rejects.append(
                {
                    "session": str(session),
                    "instrument": inst,
                    "reason": f"sell_blocked_{why}",
                    "exit_reason": exit_reason,
                }
            )
            if why == "data_gap":
                result.rejects.append(
                    {"session": str(session), "instrument": inst, "reason": "data_gap"}
                )
            continue

        # execution price: stop/tp use touch proxy (open if gap through else level); else close/open config
        if exit_reason == "stop":
            trade_px = min(float(bar["open"]), float(ideal_px))
        elif exit_reason == "take_profit":
            trade_px = max(float(bar["open"]), float(ideal_px))
        else:
            trade_px = float(bar[config.execution.price if exit_reason != "exit_intent" else "close"])

        notional = trade_px * pos.qty
        fee = sell_cost(notional, config.costs)
        state.cash += notional - fee
        state.fees_paid += fee
        pnl = notional - pos.cost_basis - fee
        state.realized_pnl += pnl
        result.trades.append(
            {
                "session": str(session),
                "instrument": inst,
                "side": "sell",
                "qty": pos.qty,
                "price": trade_px,
                "fee": fee,
                "reason": exit_reason,
                "pnl": pnl,
            }
        )
        if exit_reason == "stop" and session == pos.entry_session:
            # should be unreachable due to T+1
            pass
        del state.positions[inst]


def compute_metrics(
    equity: list[dict],
    trades: list[dict],
    starting_cash: float,
    *,
    panel: PricePanel | None = None,
    config: ResearchConfig | None = None,
) -> dict[str, Any]:
    from qresearch.engines.analysis.metrics import compute_extended_metrics

    return compute_extended_metrics(
        equity,
        trades,
        starting_cash,
        panel=panel,
        config=config,
    )
