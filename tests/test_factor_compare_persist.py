from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from qresearch.config.models import AppSettings, ResearchConfig
from qresearch.engines.data.panel import PricePanel
from qresearch.pipeline import pipeline_factor_compare


def _panel_and_events(n: int = 20) -> tuple[PricePanel, pl.DataFrame, list[date]]:
    start = date(2024, 1, 2)
    sessions: list[date] = []
    d = start
    while len(sessions) < n:
        if d.weekday() < 5:
            sessions.append(d)
        d += timedelta(days=1)

    rows = []
    for inst, base in [("AAA001.SZ", 10.0), ("AAA002.SZ", 20.0), ("AAA003.SZ", 30.0)]:
        for i, s in enumerate(sessions):
            close = base * (1.0 + 0.01 * i * (1 if inst.endswith("1.SZ") else 0.4))
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
                    "adj_factor": 1.0,
                }
            )
    bars = pl.DataFrame(rows)
    panel = PricePanel(
        bars=bars,
        calendar=sessions,
        adjustment_as_of="session_pit",
        data_fingerprint="test",
        start=sessions[0],
        end=sessions[-1],
        instruments=["AAA001.SZ", "AAA002.SZ", "AAA003.SZ"],
        adj_mode="none",
    )
    panel.build_index()
    events = pl.DataFrame(
        {
            "instrument": ["AAA001.SZ", "AAA002.SZ", "AAA003.SZ"] * 3,
            "entry_intent_date": [sessions[1]] * 3 + [sessions[2]] * 3 + [sessions[3]] * 3,
            "features.score": [3.0, 2.0, 1.0] * 3,
        }
    )
    return panel, events, sessions


def test_pipeline_factor_compare_writes_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    panel, events, _ = _panel_and_events()
    runs = tmp_path / "runs"
    cache = tmp_path / "cache"
    monkeypatch.setattr(
        "qresearch.pipeline.get_settings",
        lambda: AppSettings(runs_dir=runs, cache_dir=cache),
    )
    monkeypatch.setattr(
        "qresearch.pipeline.load_research_config",
        lambda _path=None, overrides=None: ResearchConfig.model_validate(
            {
                "ic_horizons": [5],
                "factors": {"min_non_null": 3, "max_features": 8},
                "benchmark": {"instrument": ""},
            }
        ),
    )
    monkeypatch.setattr("qresearch.pipeline.load_events", lambda *_a, **_k: events)
    monkeypatch.setattr("qresearch.pipeline.load_price_panel", lambda *_a, **_k: panel)

    result = pipeline_factor_compare("dummy.csv", run_id="factor_cmp_demo")
    assert result["run_id"] == "factor_cmp_demo"
    root = Path(result["artifacts"]["run_dir"])
    assert root.exists()
    assert (root / "meta.json").exists()
    assert (root / "artifacts" / "sample_profile.json").exists()
    assert (root / "artifacts" / "ic_summary.csv").exists()
    assert (root / "artifacts" / "icir_summary.csv").exists()
    assert result["summary"]["n_features"] >= 1
    assert result["summary"]["promotable"] is False
