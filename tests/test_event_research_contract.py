"""Golden contract for the deterministic event-research pipeline baseline."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from qresearch.config.models import (
    AppSettings,
    FilterRule,
    GatesConfig,
    PortfolioConfig,
    RankBy,
    ResearchConfig,
    RiskConfig,
    SignalsConfig,
)
from qresearch.engines.data.panel import load_price_panel
from qresearch.pipeline import pipeline_research
from tests.fixtures.make_synth import make_event_contract_case

GOLDEN_PATH = Path(__file__).parent / "golden" / "event_research_contract.json"


def _date_key(value: object) -> str:
    return str(value)[:10]


def _event_key(row: dict[str, object], *, ranked: bool = False) -> str:
    key = "|".join(
        [
            str(row["instrument"]),
            _date_key(row["entry_intent_date"]),
            _date_key(row["exit_intent_date"]),
            f"{float(row['features.continuous_factor']):.3f}",
            str(row["features.discrete_factor"]),
        ]
    )
    return f"{key}|rank={float(row['rank_score']):.1f}" if ranked else key


def _trade_key(row: dict[str, object]) -> str:
    return "|".join(
        [
            str(row["session"]),
            str(row["instrument"]),
            str(row["side"]),
            str(row["reason"]),
            str(row["qty"]),
            f"{float(row['price']):.4f}",
        ]
    )


def run_event_contract_case(tmp_path: Path) -> dict[str, object]:
    """Run the real pipeline against only the deterministic in-memory fixture."""
    events, bars, calendar = make_event_contract_case()
    config = ResearchConfig(
        signals=SignalsConfig(
            filters=[FilterRule(field="features.trade_eligible", op="ge", value=1)],
            rank_by=[RankBy(field="features.continuous_factor", ascending=False)],
        ),
        portfolio=PortfolioConfig(
            starting_cash=100_000.0,
            max_weight=0.5,
            max_names=2,
            max_new_entries_per_day=2,
            lot_size=100,
        ),
        risk=RiskConfig(stop_loss=None, take_profit=None, max_hold_sessions=3),
        adjustment={"mode": "none"},
        benchmark={"instrument": ""},
        gates=GatesConfig(min_oos_folds=0, min_trades=1, min_oos_sharpe=None),
        factors={"min_non_null": 2, "max_features": 8},
    )
    settings = AppSettings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("qresearch.pipeline.get_settings", lambda: settings)
        monkeypatch.setattr(
            "qresearch.pipeline.load_research_config", lambda *_args, **_kwargs: config
        )
        monkeypatch.setattr("qresearch.pipeline.load_events", lambda *_args, **_kwargs: events)
        monkeypatch.setattr(
            "qresearch.pipeline.load_price_panel",
            lambda loaded_events, loaded_config, **_kwargs: load_price_panel(
                loaded_events,
                loaded_config,
                bars_override=bars,
                calendar_override=calendar,
            ),
        )
        pipeline_result = pipeline_research(
            "unused.csv", run_id="event_contract", do_ic=False, do_wf=False
        )

    run_dir = Path(pipeline_result["artifacts"]["run_dir"])
    sample = pl.read_parquet(run_dir / "artifacts" / "events.parquet")
    ranked = pl.read_parquet(run_dir / "artifacts" / "ranked_events.parquet")
    trades = pl.read_csv(run_dir / "artifacts" / "trades.csv")
    metrics = json.loads((run_dir / "artifacts" / "metrics.json").read_text(encoding="utf-8"))
    metric_names = [
        "n_trades",
        "n_sessions",
        "n_return_obs",
        "total_return",
        "ann_return",
        "sharpe",
        "max_dd",
        "end_nav",
        "avg_daily_turnover",
        "ann_turnover",
        "turnover_days",
        "avg_turnover_on_trade_days",
        "median_participation",
        "p90_participation",
        "mean_participation",
        "n_capacity_samples",
        "deflated_sharpe",
        "dsr_prob",
        "sr_null_max",
        "mean_invested",
        "empty_cash_share",
    ]
    return {
        "sample_keys": [_event_key(row) for row in sample.to_dicts()],
        "ranked_keys": [_event_key(row, ranked=True) for row in ranked.to_dicts()],
        "trade_keys": [_trade_key(row) for row in trades.to_dicts()],
        "metrics": {name: metrics[name] for name in metric_names},
    }


def test_event_research_contract_is_frozen(tmp_path: Path) -> None:
    """Catches accidental changes to event selection, ranking, fills, or metrics."""
    result = run_event_contract_case(tmp_path)
    if not GOLDEN_PATH.exists():
        print("Candidate event research contract payload:")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        pytest.fail(f"missing reviewed golden: {GOLDEN_PATH}")
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    assert result["sample_keys"] == golden["sample_keys"]
    assert result["ranked_keys"] == golden["ranked_keys"]
    assert result["trade_keys"] == golden["trade_keys"]
    assert result["metrics"] == pytest.approx(golden["metrics"], rel=1e-10, abs=1e-12)
