"""Generate tiny synthetic events + bars for unit tests."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).parent


def main() -> None:
    # trading days Mon-Fri style simple sequence
    start = date(2024, 1, 2)
    sessions = []
    d = start
    while len(sessions) < 30:
        if d.weekday() < 5:
            sessions.append(d)
        d += timedelta(days=1)

    # two instruments
    rows = []
    for inst, base in [("AAA001.SZ", 10.0), ("AAA002.SZ", 20.0)]:
        px = base
        for i, s in enumerate(sessions):
            o = px
            h = px * 1.02
            l = px * 0.98
            c = px * (1.0 + (0.01 if i % 2 == 0 else -0.005))
            rows.append(
                {
                    "instrument": inst,
                    "trade_date": s,
                    "open": round(o, 2),
                    "high": round(h, 2),
                    "low": round(l, 2),
                    "close": round(c, 2),
                    "vol": 10000.0,
                    "amount": 100000.0,
                }
            )
            px = c
    bars = pl.DataFrame(rows)
    bars.write_parquet(ROOT / "bars.parquet")

    # events: buy on session[1], sell session[5]
    events_csv = ROOT / "events.csv"
    events_csv.write_text(
        "code,buy_date,sell_date,box_quality,%B,bandwidth_percent\n"
        f"sz.000001,{sessions[1].strftime('%Y/%m/%d')},{sessions[5].strftime('%Y/%m/%d')},0.97,0.5,20\n"
        f"sz.000002,{sessions[2].strftime('%Y/%m/%d')},{sessions[6].strftime('%Y/%m/%d')},0.96,0.4,15\n",
        encoding="utf-8",
    )
    # map fixtures instruments in events to AAA* by rewriting codes in test helper instead
    # Keep CSV with sz codes; tests will inject bars under mapped instruments.


if __name__ == "__main__":
    main()
