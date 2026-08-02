"""Portfolio state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Position:
    instrument: str
    qty: int
    entry_price: float
    entry_session: date
    exit_intent_date: date
    cost_basis: float
    pending_exit_reason: str | None = None
    industry: str | None = None


@dataclass
class PortfolioState:
    cash: float
    asof: date | None = None
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0

    def n_names(self) -> int:
        return len(self.positions)

    def position_cost(self) -> float:
        return sum(p.cost_basis for p in self.positions.values())

    def nav(self, marks: dict[str, float] | None = None) -> float:
        equity = self.cash
        for inst, pos in self.positions.items():
            px = marks.get(inst, pos.entry_price) if marks else pos.entry_price
            equity += pos.qty * px
        return equity
