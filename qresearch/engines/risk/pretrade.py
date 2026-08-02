"""Daily pre-trade checks."""

from __future__ import annotations

from collections import Counter
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


def _industry_of(intent: Intent, field: str) -> str | None:
    feats = intent.features or {}
    v = feats.get(field)
    if v is None and not str(field).startswith("features."):
        v = feats.get(f"features.{field}")
    if v is None or (isinstance(v, float) and v != v):  # NaN
        return None
    s = str(v).strip()
    return s or None


def _held_industry_counts(state: PortfolioState) -> Counter[str]:
    c: Counter[str] = Counter()
    for pos in state.positions.values():
        if pos.industry:
            c[pos.industry] += 1
    return c


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
    openable: list[Intent] = []
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

    max_held = portfolio.max_names_per_industry
    max_day = portfolio.max_new_per_industry_per_day
    industry_field = portfolio.industry_field or "features.industry"
    held_by_ind = _held_industry_counts(state)
    daily_by_ind: Counter[str] = Counter()

    allowed: list[Intent] = []
    for it in openable:
        if len(allowed) >= slots:
            rejects.append(
                {
                    "session": str(session),
                    "instrument": it.instrument,
                    "reason": "max_new_entries_or_max_names",
                }
            )
            continue

        ind = _industry_of(it, industry_field)
        # Missing industry: allow and do not count toward industry caps
        if ind is not None:
            if max_held is not None and held_by_ind[ind] >= int(max_held):
                rejects.append(
                    {
                        "session": str(session),
                        "instrument": it.instrument,
                        "reason": "industry_held_cap",
                    }
                )
                continue
            if max_day is not None and daily_by_ind[ind] >= int(max_day):
                rejects.append(
                    {
                        "session": str(session),
                        "instrument": it.instrument,
                        "reason": "industry_daily_cap",
                    }
                )
                continue

        allowed.append(it)
        if ind is not None:
            daily_by_ind[ind] += 1
            held_by_ind[ind] += 1

    return allowed, rejects
