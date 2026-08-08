from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from conftest import research_config
from qresearch.config.models import AppSettings, RiskConfig
from qresearch.engines.backtest.session import BacktestResult
from qresearch.research.domain import FeatureSnapshot, ResearchDataset
from qresearch.research.pipeline import run_research_strategy


def test_run_research_strategy_uses_frozen_dataset_and_writes_backtest_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    import qresearch.research.pipeline as pipeline

    run_dir = tmp_path / "runs" / "frozen-run"
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    config = research_config(risk=RiskConfig(max_hold_sessions=2))
    config_path = tmp_path / "market.yaml"
    config_path.write_text(config.model_dump_json(), encoding="utf-8")
    dataset = ResearchDataset(
        pl.DataFrame(
            {
                "sample_id": ["sample"],
                "instrument": ["000001.SZ"],
                "asof_session": [date(2024, 1, 2)],
                "effective_session": [date(2024, 1, 3)],
                "features.alpha": [1.0],
            }
        ),
        {"input_hashes": {"features": "snapshot-sha"}},
    )
    snapshot = FeatureSnapshot(dataset.frame.select(dataset.frame.columns[:4]), {"feature_snapshot_hash": "snapshot-sha"})
    settings = AppSettings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache")
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(
        pipeline,
        "materialize_research",
        lambda _config_path, run_id=None: {"run_id": run_id or "frozen-run"},
    )
    monkeypatch.setattr(
        pipeline,
        "_load_frozen_run",
        lambda _run_dir: (snapshot, dataset, {"run_dir": str(run_dir), "dataset": str(artifacts_dir / "dataset.parquet")}),
    )
    monkeypatch.setattr(
        pipeline,
        "_calendar_for",
        lambda _config: [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)],
    )
    monkeypatch.setattr(pipeline, "load_price_panel", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        pipeline,
        "run_backtest",
        lambda ranked, _panel, _config: BacktestResult(
            trades=[{"instrument": "000001.SZ", "side": "buy"}],
            equity=[{"session": "2024-01-03", "cash": 50.0, "nav": 100.0, "n_positions": 1}],
            rejects=[{"reason": "none"}],
            metrics={"sharpe": 1.0, "n_trades": 1},
        ),
    )

    result = run_research_strategy(config_path, run_id="frozen-run", n_trials_assumed=3)

    assert result["run_id"] == "frozen-run"
    assert result["summary"]["sample_kind"] == "market"
    assert result["summary"]["snapshot_sha256"] == "snapshot-sha"
    assert result["summary"]["metrics"]["n_trials"] == 3
    assert set(result["artifacts"]) >= {
        "ranked_signals",
        "equity",
        "trades",
        "metrics",
        "rejects_summary",
    }
    assert not (artifacts_dir / "ranked_events.parquet").exists()
    assert (artifacts_dir / "ranked_signals.parquet").exists()
    assert (artifacts_dir / "equity.csv").exists()
    assert (artifacts_dir / "trades.csv").exists()
