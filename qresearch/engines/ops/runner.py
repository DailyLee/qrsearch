"""Ops: Feed -> Signal -> PreTrade -> order intents."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from qresearch.config import load_research_config
from qresearch.config.models import ResearchConfig
from qresearch.engines.data.ingest import load_events
from qresearch.engines.data.limitbook import LimitBook
from qresearch.engines.data.panel import load_price_panel
from qresearch.engines.risk.pretrade import Intent, select_buy_intents
from qresearch.engines.risk.state import PortfolioState, Position
from qresearch.engines.signal.engine import build_ranked


def _parse_asof(asof: str) -> date:
    s = asof.replace("-", "")
    return datetime.strptime(s, "%Y%m%d").date()


def load_state(path: str | Path | None, starting_cash: float, asof: date) -> PortfolioState:
    state = PortfolioState(cash=starting_cash, asof=asof)
    if not path:
        return state
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    state.cash = float(data.get("cash", starting_cash))
    for p in data.get("positions", []):
        state.positions[p["instrument"]] = Position(
            instrument=p["instrument"],
            qty=int(p["qty"]),
            entry_price=float(p["entry_price"]),
            entry_session=date.fromisoformat(p["entry_session"]),
            exit_intent_date=date.fromisoformat(p["exit_intent_date"]),
            cost_basis=float(p.get("cost_basis", 0.0)),
        )
    return state


def run_ops(
    *,
    package_dir: Path | None,
    events_path: str | Path,
    asof: str,
    mode: str,
    state_path: str | Path | None,
    config: ResearchConfig | None = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    stages = ["normalize", "signal", "pretrade", "intents"]
    session = _parse_asof(asof)

    if package_dir is not None:
        spec = Path(package_dir) / "spec.yaml"
        config = load_research_config(spec)
    elif config is None:
        config = ResearchConfig()

    events = load_events(events_path, config)
    ranked = build_ranked(events, config)
    # intents with entry_intent_date == asof (or pending GTD window)
    intents: list[Intent] = []
    for r in ranked.iter_rows(named=True):
        entry = r["entry_intent_date"]
        if not isinstance(entry, date):
            entry = date.fromisoformat(str(entry))
        # include if within validity from entry
        # simplified: entry == asof for ops day signal
        if entry == session:
            intents.append(
                Intent(
                    instrument=r["instrument"],
                    side="buy",
                    decision_date=entry,
                    entry_intent_date=entry,
                    exit_intent_date=date.fromisoformat(str(r["exit_intent_date"])[:10]),
                    rank_score=float(r.get("rank_score") or 0),
                )
            )

    state = load_state(state_path, config.portfolio.starting_cash, session)
    live_ready = True
    warnings = []
    if mode == "signal" and not state_path:
        live_ready = False
        warnings.append("signal_mode_without_state")

    # Include held names because due sells need the same historical-limit facts as buys.
    panel = load_price_panel(
        events,
        config,
        cache_dir=cache_dir,
        extra_instruments=list(state.positions),
    )
    limitbook = LimitBook()
    allowed, rejects = select_buy_intents(
        intents, state, config.portfolio, panel, session, limitbook
    )

    orders = []
    for it in allowed:
        # T+1: no sell intents for same-day buys in this simplified ops
        orders.append(
            {
                "asof": asof,
                "instrument": it.instrument,
                "side": "buy",
                "order_type": "market_open",
                "rank_score": it.rank_score,
            }
        )

    # sell intents for positions due
    for inst, pos in state.positions.items():
        if session <= pos.entry_session:
            rejects.append(
                {
                    "instrument": inst,
                    "reason": "t1_defer",
                    "side": "sell",
                }
            )
            continue
        if session >= pos.exit_intent_date:
            bar = panel.get(inst, session)
            can_sell, reason = limitbook.can_sell_open(bar)
            if not can_sell:
                rejects.append(
                    {
                        "session": str(session),
                        "instrument": inst,
                        "reason": reason,
                        "side": "sell",
                    }
                )
                continue
            orders.append(
                {
                    "asof": asof,
                    "instrument": inst,
                    "side": "sell",
                    "order_type": "market_close",
                    "reason": "exit_intent",
                }
            )

    return {
        "stages": stages,
        "mode": mode,
        "asof": asof,
        "live_ready": live_ready,
        "warnings": warnings,
        "n_intents": len(orders),
        "n_rejected": len(rejects),
        "orders": orders,
        "rejects": rejects,
    }
