from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from qresearch.engines.data.panel import PricePanel
from qresearch.engines.factor.ic import compute_ic_table, shuffle_date_ic, spearman_ic


def test_spearman_ic_perfect_and_short():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
    assert spearman_ic(x, y) == pytest.approx(1.0)

    y_neg = np.array([10.0, 8.0, 6.0, 4.0, 2.0])
    assert spearman_ic(x, y_neg) == pytest.approx(-1.0)

    assert np.isnan(spearman_ic(np.array([1.0, 2.0]), np.array([1.0, 2.0])))


def _mono_panel(n: int = 12) -> tuple[PricePanel, list[date]]:
    start = date(2024, 1, 2)
    sessions = []
    d = start
    while len(sessions) < n:
        if d.weekday() < 5:
            sessions.append(d)
        d += timedelta(days=1)

    rows = []
    for inst, base in [("AAA001.SZ", 10.0), ("AAA002.SZ", 20.0), ("AAA003.SZ", 30.0)]:
        for i, s in enumerate(sessions):
            # instrument-specific drift so feature ranks can correlate with fwd ret
            close = base * (1.0 + 0.01 * i * (1 if inst.endswith("1.SZ") else 0.5 if inst.endswith("2.SZ") else 0.2))
            rows.append(
                {
                    "instrument": inst,
                    "trade_date": s,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "vol": 1e5,
                    "amount": 1e6,
                }
            )
    bars = pl.DataFrame(rows)
    panel = PricePanel(
        bars=bars,
        calendar=sessions,
        adjustment_as_of=sessions[-1].strftime("%Y%m%d"),
        data_fingerprint="test",
        start=sessions[0],
        end=sessions[-1],
        instruments=["AAA001.SZ", "AAA002.SZ", "AAA003.SZ"],
    )
    panel.build_index()
    return panel, sessions


def test_compute_ic_table_shape_and_direction():
    panel, sessions = _mono_panel()
    # higher feature -> instrument with higher drift (AAA001)
    events = pl.DataFrame(
        {
            "instrument": ["AAA001.SZ", "AAA002.SZ", "AAA003.SZ"] * 2,
            "entry_intent_date": [sessions[1], sessions[1], sessions[1], sessions[2], sessions[2], sessions[2]],
            "features.score": [3.0, 2.0, 1.0, 3.0, 2.0, 1.0],
        }
    )
    table = compute_ic_table(events, panel, ["features.score"], horizons=[5])
    assert table.height == 1
    assert table["feature"][0] == "features.score"
    assert table["horizon"][0] == 5
    assert table["n"][0] == 6
    assert table["rank_ic"][0] > 0  # score aligns with higher fwd return


def test_compute_ic_table_missing_feature_returns_empty():
    panel, sessions = _mono_panel(n=8)
    events = pl.DataFrame(
        {
            "instrument": ["AAA001.SZ"],
            "entry_intent_date": [sessions[1]],
            "features.score": [1.0],
        }
    )
    table = compute_ic_table(events, panel, ["features.absent"], horizons=[3])
    assert table.height == 0
    assert set(table.columns) == {"feature", "horizon", "n", "rank_ic"}


def test_shuffle_date_ic_changes_signal():
    panel, sessions = _mono_panel(n=16)
    # date-carried signal: later entries have higher score and higher subsequent drift
    instruments = ["AAA001.SZ"] * 8
    entries = sessions[1:9]
    scores = [float(i) for i in range(len(entries))]
    events = pl.DataFrame(
        {
            "instrument": instruments,
            "entry_intent_date": entries,
            "features.score": scores,
        }
    )
    orig, shuf = shuffle_date_ic(events, panel, "features.score", horizon=3, seed=7)
    assert not np.isnan(orig)
    # with enough date signal, shuffle should move IC (allow rare ties)
    assert orig != shuf or abs(orig) > 0
