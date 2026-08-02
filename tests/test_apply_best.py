from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from qresearch.engines.experiment.best_params import (
    ApplyBestError,
    apply_best_to_yaml,
    apply_patches,
    optimize_params_to_patches,
)


def test_optimize_to_patches():
    bp = optimize_params_to_patches(
        {"feature": "features.pre_r1", "op": "le", "threshold": -1.0, "side": "low", "keep_frac": 0.2}
    )
    assert bp["patches"][0]["match"]["field"] == "features.pre_r1"
    assert bp["patches"][0]["set"]["value"] == -1.0


def test_apply_patches_only_touched_keys():
    base = {
        "signals": {
            "filters": [
                {"field": "features.pre_r1", "op": "le", "value": 0.0},
                {"field": "features.box_quality", "op": "ge", "value": 0.9},
            ]
        },
        "portfolio": {"max_weight": 0.35, "sizing_base": "cash"},
        "risk": {"stop_loss": -0.1},
    }
    out = apply_patches(
        base,
        [
            {
                "path": "signals.filters",
                "match": {"field": "features.pre_r1"},
                "set": {"value": -1.0},
            },
            {"path": "portfolio.max_weight", "value": 0.2},
        ],
    )
    assert out["signals"]["filters"][0]["value"] == -1.0
    assert out["signals"]["filters"][1]["value"] == 0.9
    assert out["portfolio"]["max_weight"] == 0.2
    assert out["portfolio"]["sizing_base"] == "cash"
    assert out["risk"]["stop_loss"] == -0.1


def test_apply_best_writes_new_and_rejects_examples(tmp_path: Path):
    run = tmp_path / "run1"
    art = run / "artifacts"
    art.mkdir(parents=True)
    snap = {
        "signals": {"filters": [{"field": "features.pre_r1", "op": "le", "value": 0.0}]},
        "portfolio": {"max_weight": 0.35},
    }
    (run / "config.snapshot.yaml").write_text(yaml.safe_dump(snap), encoding="utf-8")
    (art / "sweep_summary.json").write_text(
        json.dumps(
            {
                "best_params": {
                    "patches": [
                        {
                            "path": "signals.filters",
                            "match": {"field": "features.pre_r1"},
                            "set": {"value": -1.5},
                        }
                    ],
                    "source": "pipeline.sweep",
                    "metric": "sharpe",
                    "best_value": 0.7,
                },
                "best_value": 0.7,
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "configs" / "experiments" / "x_v1.yaml"
    result = apply_best_to_yaml(
        run_dir=run, out_path=out, examples_dir=tmp_path / "configs" / "examples"
    )
    assert Path(result["out"]).exists()
    loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert loaded["signals"]["filters"][0]["value"] == -1.5

    with pytest.raises(ApplyBestError, match="examples"):
        apply_best_to_yaml(
            run_dir=run,
            out_path=tmp_path / "configs" / "examples" / "bad.yaml",
            examples_dir=tmp_path / "configs" / "examples",
        )

    with pytest.raises(ApplyBestError, match="already exists"):
        apply_best_to_yaml(
            run_dir=run, out_path=out, examples_dir=tmp_path / "configs" / "examples"
        )
