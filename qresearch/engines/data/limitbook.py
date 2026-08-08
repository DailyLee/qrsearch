"""Historical price-limit and suspension checks."""

from __future__ import annotations


class LimitBook:
    """Check fills against the historical limits supplied with each bar."""

    def is_suspended(self, bar: dict | None) -> bool:
        if bar is None:
            return True
        vol = bar.get("vol")
        if vol is None:
            return False
        try:
            return float(vol) <= 0
        except (TypeError, ValueError):
            return False

    def is_limit_up(self, bar: dict) -> bool:
        # Open locked at the historical limit-up price.
        return float(bar["open"]) >= float(bar["up_limit"]) - 1e-6

    def is_limit_down(self, bar: dict) -> bool:
        return float(bar["open"]) <= float(bar["down_limit"]) + 1e-6

    def can_buy_open(self, bar: dict | None) -> tuple[bool, str]:
        if bar is None:
            return False, "data_gap"
        if self.is_suspended(bar):
            return False, "suspended"
        if bar.get("up_limit") is None:
            return False, "missing_limit_data"
        if self.is_limit_up(bar):
            return False, "limit_up"
        return True, "ok"

    def can_sell_open(self, bar: dict | None) -> tuple[bool, str]:
        if bar is None:
            return False, "data_gap"
        if self.is_suspended(bar):
            return False, "suspended"
        if bar.get("down_limit") is None:
            return False, "missing_limit_data"
        if self.is_limit_down(bar):
            return False, "limit_down"
        return True, "ok"
