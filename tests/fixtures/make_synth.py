"""Generate tiny synthetic events + bars for unit tests."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).parent


def make_event_contract_case() -> tuple[pl.DataFrame, pl.DataFrame, list[date]]:
    """Return an isolated two-year panel for the research contract golden test."""
    calendar: list[date] = []
    for start in (date(2024, 1, 2), date(2025, 1, 2)):
        current = start
        year_sessions: list[date] = []
        while len(year_sessions) < 12:
            if current.weekday() < 5:
                year_sessions.append(current)
            current += timedelta(days=1)
        calendar.extend(year_sessions)

    instruments = [f"00000{i}.SZ" for i in range(1, 7)]
    bars_rows: list[dict[str, object]] = []
    for instrument_index, instrument in enumerate(instruments, start=1):
        previous_close = 10.0 + instrument_index
        for session_index, session in enumerate(calendar):
            # One intentional data gap exercises deferred order handling.  All opens
            # remain well inside their explicit daily limits.
            if instrument == "000006.SZ" and session == calendar[15]:
                continue
            drift = 0.002 * ((session_index % 4) - 1) + 0.0003 * instrument_index
            opening = previous_close * (1.0 + 0.001 * ((instrument_index % 3) - 1))
            closing = opening * (1.0 + drift)
            bars_rows.append(
                {
                    "instrument": instrument,
                    "trade_date": session,
                    "open": round(opening, 4),
                    "high": round(max(opening, closing) * 1.01, 4),
                    "low": round(min(opening, closing) * 0.99, 4),
                    "close": round(closing, 4),
                    "vol": 100_000.0 + instrument_index * 1_000.0,
                    "amount": 1_000_000.0 + instrument_index * 10_000.0,
                    "up_limit": round(previous_close * 1.10, 4),
                    "down_limit": round(previous_close * 0.90, 4),
                    "adj_factor": 1.0,
                }
            )
            previous_close = closing

    events = pl.DataFrame(
        {
            "instrument": [
                "000001.SZ",
                "000002.SZ",
                "000002.SZ",  # duplicate event key, filtered from the trade path
                "000003.SZ",
                "000004.SZ",
                "000005.SZ",
                "000006.SZ",
            ],
            "decision_date": [
                calendar[2],
                calendar[2],
                calendar[2],
                calendar[5],
                calendar[14],
                calendar[16],
                calendar[15],
            ],
            "entry_intent_date": [
                calendar[2],
                calendar[2],
                calendar[2],
                calendar[5],
                calendar[14],
                calendar[16],
                calendar[15],
            ],
            "exit_intent_date": [
                calendar[7],
                calendar[8],
                calendar[9],
                calendar[10],
                calendar[19],
                calendar[21],
                calendar[20],
            ],
            "features.continuous_factor": [0.20, 0.90, 0.10, 0.60, 0.80, 0.40, 0.70],
            "features.discrete_factor": [1, 2, 2, 1, 3, 2, 3],
            "features.trade_eligible": [1, 1, 0, 1, 1, 1, 1],
            "source_file": ["event_contract"] * 7,
        }
    )
    return events, pl.DataFrame(bars_rows), calendar


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
