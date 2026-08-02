from __future__ import annotations

import json
from pathlib import Path

from qresearch.engines.analysis.split_comparison import (
    build_split_comparison,
    latest_run_ids_from_decisions,
)


def _write_run(
    runs: Path,
    run_id: str,
    *,
    sharpe: float,
    ret: float,
    years: dict[str, int] | None = None,
) -> None:
    root = runs / run_id
    (root / "artifacts").mkdir(parents=True)
    (root / "report").mkdir(parents=True)
    (root / "meta.json").write_text(
        json.dumps({"run_id": run_id, "promotable": sharpe > 0, "n_events": 10}),
        encoding="utf-8",
    )
    (root / "artifacts" / "metrics.json").write_text(
        json.dumps(
            {
                "sharpe": sharpe,
                "total_return": ret,
                "ann_return": ret / 2,
                "max_dd": -0.1,
                "n_trades": 50,
                "excess_return": 0.01,
            }
        ),
        encoding="utf-8",
    )
    if years:
        ys = sorted(int(y) for y in years)
        (root / "artifacts" / "sample_profile.json").write_text(
            json.dumps(
                {
                    "years": years,
                    "entry_min": f"{ys[0]}-01-02",
                    "entry_max": f"{ys[-1]}-12-30",
                    "n_events": sum(years.values()),
                }
            ),
            encoding="utf-8",
        )
    (root / "report" / "research_report_zh.html").write_text("<html></html>", encoding="utf-8")


def test_latest_run_ids_from_decisions():
    decisions = [
        {"stage": "backtest_train", "run_id": "train_old", "created_at": "2026-01-01T00:00:00"},
        {"stage": "backtest_train", "run_id": "train_new", "created_at": "2026-02-01T00:00:00"},
        {"stage": "holdout", "run_id": "ho1", "created_at": "2026-02-02T00:00:00"},
        {"stage": "full_sample", "run_id": "full1", "created_at": "2026-02-03T00:00:00"},
    ]
    got = latest_run_ids_from_decisions(decisions)
    assert got == {"train": "train_new", "holdout": "ho1", "full": "full1"}


def test_build_split_comparison_table(tmp_path: Path):
    runs = tmp_path / "runs"
    _write_run(
        runs, "r_train", sharpe=0.7, ret=0.3, years={"2019": 1, "2020": 1, "2024": 1}
    )
    _write_run(runs, "r_ho", sharpe=-0.1, ret=-0.01, years={"2025": 1})
    _write_run(
        runs,
        "r_full",
        sharpe=0.5,
        ret=0.25,
        years={"2019": 1, "2020": 1, "2024": 1, "2025": 1},
    )
    decisions = [
        {"stage": "backtest_train", "run_id": "r_train", "created_at": "t1"},
        {"stage": "holdout", "run_id": "r_ho", "created_at": "t2"},
        {"stage": "full_sample", "run_id": "r_full", "created_at": "t3"},
    ]
    out = build_split_comparison(
        runs_dir=runs, study_id="demo", decisions=decisions
    )
    assert out is not None
    assert out["n_present"] == 3
    by_role = {r["role"]: r for r in out["rows"]}
    assert by_role["train"]["metrics"]["sharpe"] == 0.7
    assert by_role["train"]["years_label"] == "2019-2024"
    assert by_role["holdout"]["years_label"] == "2025"
    assert by_role["full"]["years_label"] == "2019-2025"
    assert by_role["holdout"]["metrics"]["sharpe"] == -0.1
    assert by_role["full"]["run_id"] == "r_full"


def test_override_and_partial(tmp_path: Path):
    runs = tmp_path / "runs"
    _write_run(runs, "a", sharpe=1.0, ret=0.2)
    _write_run(runs, "b", sharpe=0.2, ret=0.05)
    out = build_split_comparison(
        runs_dir=runs,
        decisions=[],
        train_run="a",
        holdout_run="b",
    )
    assert out is not None
    assert out["n_present"] == 2
    assert out["rows"][2]["missing"] is True  # full missing
