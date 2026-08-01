from __future__ import annotations

from qresearch.config.models import CostsConfig


def commission(notional: float, costs: CostsConfig) -> float:
    return max(abs(notional) * costs.commission_rate, costs.commission_min)


def buy_cost(notional: float, costs: CostsConfig) -> float:
    slip = abs(notional) * costs.slippage_bps / 10000.0
    return commission(notional, costs) + slip


def sell_cost(notional: float, costs: CostsConfig) -> float:
    slip = abs(notional) * costs.slippage_bps / 10000.0
    stamp = abs(notional) * costs.stamp_duty_rate
    return commission(notional, costs) + slip + stamp
