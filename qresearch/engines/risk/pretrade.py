"""Daily pre-trade checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from qresearch.config.models import PortfolioConfig
from qresearch.engines.data.limitbook import LimitBook
from qresearch.engines.data.panel import PricePanel
from qresearch.engines.risk.state import PortfolioState


@dataclass
class Intent:
    instrument: str
    side: str  # buy/sell
    decision_date: date
    entry_intent_date: date
    exit_intent_date: date
    rank_score: float = 0.0
    reason: str = ""
    features: dict | None = None


def select_buy_intents(
    candidates: list[Intent],
    state: PortfolioState,
    portfolio: PortfolioConfig,
    panel: PricePanel,
    session: date,
    limitbook: LimitBook,
) -> tuple[list[Intent], list[dict]]:
    """Filter/rank buy intents for a session given current state."""
    rejects: list[dict] = []
    # already held
    openable = []
    for it in candidates:
        if it.instrument in state.positions:
            rejects.append(
                {"session": str(session), "instrument": it.instrument, "reason": "already_held"}
            )
            continue
        bar = panel.get(it.instrument, session)
        prev = panel.prior_close(it.instrument, session)
        ok, why = limitbook.can_buy_open(bar, prev)
        if not ok:
            rejects.append(
                {"session": str(session), "instrument": it.instrument, "reason": why}
            )
            continue
        openable.append(it)

    openable.sort(key=lambda x: x.rank_score)
    slots = portfolio.max_new_entries_per_day
    if portfolio.max_names is not None:
        slots = min(slots, max(0, portfolio.max_names - state.n_names()))
    allowed = openable[:slots]
    for it in openable[slots:]:
        rejects.append(
            {
                "session": str(session),
                "instrument": it.instrument,
                "reason": "max_new_entries_or_max_names",
            }
        )
    return allowed, rejects
