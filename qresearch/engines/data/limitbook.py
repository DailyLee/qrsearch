"""Limit / suspend heuristics (V1)."""

from __future__ import annotations

from datetime import date


def _round2(x: float) -> float:
    return round(float(x) + 1e-10, 2)


class LimitBook:
    """Heuristic limit-up/down and suspend checks."""

    def __init__(self, up_pct: float = 0.1, down_pct: float = 0.1):
        self.up_pct = up_pct
        self.down_pct = down_pct

    def limit_up_price(self, prev_close: float) -> float:
        return _round2(prev_close * (1.0 + self.up_pct))

    def limit_down_price(self, prev_close: float) -> float:
        return _round2(prev_close * (1.0 - self.down_pct))

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

    def is_limit_up(self, bar: dict, prev_close: float | None) -> bool:
        if prev_close is None or prev_close <= 0:
            return False
        up = self.limit_up_price(prev_close)
        # open locked at limit-up
        return float(bar["open"]) >= up - 1e-6

    def is_limit_down(self, bar: dict, prev_close: float | None) -> bool:
        if prev_close is None or prev_close <= 0:
            return False
        down = self.limit_down_price(prev_close)
        return float(bar["open"]) <= down + 1e-6

    def can_buy_open(self, bar: dict | None, prev_close: float | None) -> tuple[bool, str]:
        if self.is_suspended(bar):
            return False, "suspended"
        if bar is None:
            return False, "data_gap"
        if self.is_limit_up(bar, prev_close):
            return False, "limit_up"
        return True, "ok"

    def can_sell_open(self, bar: dict | None, prev_close: float | None) -> tuple[bool, str]:
        if self.is_suspended(bar):
            return False, "suspended"
        if bar is None:
            return False, "data_gap"
        if self.is_limit_down(bar, prev_close):
            return False, "limit_down"
        return True, "ok"
