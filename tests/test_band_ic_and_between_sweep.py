from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest
from qresearch.config.models import (
    AppSettings,
    FilterRule,
    ResearchConfig,
    SignalsConfig,
)
from qresearch.engines.data.panel import PricePanel
from qresearch.engines.experiment.best_params import apply_patches
from qresearch.engines.experiment.sweep import SweepError, parse_set_spec, run_signal_sweep
from qresearch.engines.factor.band_ic import BandICError, filter_feature_band, run_band_ic
from qresearch.pipeline import pipeline_band_ic


def _panel_events(n: int = 30):
    start = date(2024, 1, 2)
    sessions: list[date] = []
    d = start
    while len(sessions) < n:
        if d.weekday() < 5:
            sessions.append(d)
        d += timedelta(days=1)
    rows = []
    for inst, base in [("AAA001.SZ", 10.0), ("AAA002.SZ", 20.0)]:
        for i, s in enumerate(sessions):
            close = base * (1.0 + 0.002 * i)
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
    panel = PricePanel(
        bars=pl.DataFrame(rows),
        calendar=sessions,
        adjustment_as_of="session_pit",
        data_fingerprint="t",
        start=sessions[0],
        end=sessions[-1],
        instruments=["AAA001.SZ", "AAA002.SZ"],
        adj_mode="none",
    )
    panel.build_index()
    # pct_b spread; pre_r1 for inside-feature
    events = pl.DataFrame(
        {
            "instrument": ["AAA001.SZ", "AAA002.SZ"] * 8,
            "entry_intent_date": [sessions[i % len(sessions)] for i in range(16)],
            "exit_intent_date": [sessions[min(i % len(sessions) + 3, len(sessions) - 1)] for i in range(16)],
            "features.pct_b": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8] * 2,
            "features.pre_r1": [-0.05, 0.02, -0.03, 0.01] * 4,
        }
    )
    return panel, events


def test_filter_band_and_lo_hi():
    panel, events = _panel_events()
    band = filter_feature_band(events, "features.pct_b", 0.2, 0.5)
    assert band.height < events.height
    assert band.height > 0
    with pytest.raises(BandICError, match="lo < hi"):
        filter_feature_band(events, "features.pct_b", 0.5, 0.2)


def test_run_band_ic_envelope_fields():
    panel, events = _panel_events()
    out = run_band_ic(
        events,
        panel,
        feature="features.pct_b",
        lo=0.2,
        hi=0.5,
        horizons=[5],
        inside_features=["features.pre_r1"],
    )
    assert out["n_band"] < out["n_full"]
    assert 0 < out["keep_frac"] < 1
    assert out["rows"]
    assert "band_stronger" in out
    assert out["inside_rows"]


def test_pipeline_band_ic_persist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    panel, events = _panel_events()
    monkeypatch.setattr(
        "qresearch.pipeline.get_settings",
        lambda: AppSettings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache"),
    )
    monkeypatch.setattr(
        "qresearch.pipeline.load_research_config",
        lambda *_a, **_k: ResearchConfig(
            ic_horizons=[5],
            factors={"min_non_null": 3, "max_features": 8},
            benchmark={"instrument": ""},
        ),
    )
    monkeypatch.setattr("qresearch.pipeline.load_events", lambda *_a, **_k: events)
    monkeypatch.setattr("qresearch.pipeline.load_price_panel", lambda *_a, **_k: panel)
    result = pipeline_band_ic(
        "dummy.csv",
        feature="features.pct_b",
        lo=0.2,
        hi=0.5,
        horizons="5",
        inside_feature=["features.pre_r1"],
        run_id="band_demo",
    )
    root = Path(result["artifacts"]["run_dir"])
    assert (root / "artifacts" / "band_ic_summary.json").exists()
    assert (root / "artifacts" / "band_ic_compare.csv").exists()
    assert result["summary"]["n_band"] < result["summary"]["n_full"]


def test_parse_between_and_sweep_grid(panel, events, base_config):
    d = parse_set_spec("signals.filters[field=features.pct_b].between=0.2:0.5,0.3:0.6")
    assert d["attr"] == "between"
    assert d["values"] == [(0.2, 0.5), (0.3, 0.6)]
    with pytest.raises(SweepError, match="lo < hi"):
        parse_set_spec("signals.filters[field=features.pct_b].between=0.5:0.2")

    # enrich events for pct_b
    ev = events.with_columns(pl.lit(0.3).alias("features.pct_b"))
    cfg = base_config.model_copy(deep=True)
    cfg.signals = SignalsConfig(
        filters=[
            FilterRule(field="features.pct_b", op="between", value=0.1, value_max=0.9),
            FilterRule(field="features.box_quality", op="ge", value=0.9),
        ]
    )
    out = run_signal_sweep(
        ev,
        panel,
        cfg,
        set_specs=[
            "signals.filters[field=features.pct_b].between=0.1:0.5,0.2:0.8",
            "signals.filters[field=features.box_quality].value=0.9,0.95",
        ],
        max_grid=64,
    )
    assert out["n_grid"] == 4
    assert all("n_events_kept" in r for r in out["rows"])
    assert out["best_params"]["patches"]
    # value_max in patches
    bet = [p for p in out["best_params"]["patches"] if p.get("set", {}).get("op") == "between"]
    assert bet and "value_max" in bet[0]["set"]

    trunc = run_signal_sweep(
        ev,
        panel,
        cfg,
        set_specs=["signals.filters[field=features.pct_b].between=0.1:0.5,0.2:0.8"],
        max_grid=1,
    )
    assert trunc["n_grid"] == 1 and trunc["truncated"] is True


def test_apply_patches_value_max():
    base = {
        "signals": {
            "filters": [{"field": "features.pct_b", "op": "ge", "value": 0.0}],
        }
    }
    out = apply_patches(
        base,
        [
            {
                "path": "signals.filters",
                "match": {"field": "features.pct_b"},
                "set": {"op": "between", "value": 0.2, "value_max": 0.5},
            }
        ],
    )
    f = out["signals"]["filters"][0]
    assert f["op"] == "between"
    assert f["value"] == 0.2
    assert f["value_max"] == 0.5
