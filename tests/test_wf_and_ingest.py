from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from qresearch.config.models import IngestConfig, ResearchConfig, WalkForwardConfig
from qresearch.engines.data.ingest import load_events
from qresearch.engines.experiment.walkforward import build_folds, purge_is_events


def test_purge_is_events():
    is_ev = pl.DataFrame(
        {
            "instrument": ["A", "B"],
            "entry_intent_date": [date(2020, 1, 2), date(2020, 6, 1)],
            "exit_intent_date": [date(2021, 1, 5), date(2020, 6, 10)],
        }
    )
    purged = purge_is_events(is_ev, date(2021, 1, 1))
    assert purged.height == 1
    assert purged["instrument"][0] == "B"


def test_build_folds_expanding():
    ev = pl.DataFrame(
        {
            "entry_intent_date": [date(2019, 1, 1), date(2020, 1, 1), date(2021, 1, 1)],
            "instrument": ["a", "b", "c"],
        }
    )
    folds = build_folds(ev, WalkForwardConfig(mode="expanding"))
    assert len(folds) == 2
    assert folds[0]["oos_years"] == [2020]


def test_load_real_events_sample():
    path = Path("workspace/events/平台期扫描_批量_2019_合并_0.94.csv")
    if not path.exists():
        pytest.skip(f"local events missing: {path}")
    cfg = ResearchConfig(ingest=IngestConfig())
    df = load_events(path, cfg)
    assert df.height > 0
    assert "instrument" in df.columns
    assert str(df["instrument"][0]).endswith((".SZ", ".SH", ".BJ"))
