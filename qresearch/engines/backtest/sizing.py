"""Equal-weight cash budget sizing with lot rounding."""

from __future__ import annotations

from qresearch.config.models import CostsConfig, PortfolioConfig
from qresearch.engines.backtest.costs import buy_cost


def allocate_shares(
    prices: list[float],
    cash: float,
    portfolio: PortfolioConfig,
    costs: CostsConfig,
) -> list[int]:
    n = len(prices)
    if n == 0 or cash <= 0:
        return []
    lot = portfolio.lot_size
    max_amt = cash * portfolio.max_weight
    target = cash / n
    volumes = []
    for px in prices:
        if px <= 0:
            volumes.append(0)
            continue
        lot_price = px * lot
        # estimate with commission on lot
        budget = min(target, max_amt)
        # iterative: shares in lots
        lots = int(budget // lot_price)
        while lots > 0:
            notional = lots * lot_price
            fee = buy_cost(notional, costs)
            if notional + fee <= cash and notional <= max_amt + 1e-6:
                break
            lots -= 1
        volumes.append(lots * lot)

    # greedy residual
    remaining = cash
    for i, (px, qty) in enumerate(zip(prices, volumes)):
        notional = px * qty
        remaining -= notional + buy_cost(notional, costs)
    order = sorted(range(n), key=lambda i: prices[i] * lot)
    changed = True
    while changed and remaining > 0:
        changed = False
        for i in order:
            px = prices[i]
            if px <= 0:
                continue
            lot_price = px * lot
            notional_new = (volumes[i] + lot) * px
            if notional_new > cash * portfolio.max_weight + 1e-6:
                continue
            fee = buy_cost(lot_price, costs)
            if lot_price + fee <= remaining + 1e-9:
                volumes[i] += lot
                remaining -= lot_price + fee
                changed = True
    return volumes
