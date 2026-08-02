from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from qresearch.cli import app
from qresearch.engines.experiment.scaffold import ScaffoldError, scaffold_experiment_yaml


def _write_template(path: Path, *, with_signals: bool = False, with_eval: bool = True) -> None:
    data: dict = {
        "ingest": {"board": "limit10", "aliases": {"instrument": "code"}},
        "signals": {
            "filters": (
                [{"field": "features.box_quality", "op": "ge", "value": 0.94}]
                if with_signals
                else []
            ),
            "rank_by": (
                [{"field": "features.bandwidth_percent", "ascending": True}]
                if with_signals
                else []
            ),
        },
        "portfolio": {"starting_cash": 1_000_000},
        "hypothesis": {"id": "tmpl", "statement": "scaffold"},
    }
    if with_eval:
        data["evaluation"] = {
            "primary_metric": "absolute",
            "train_years": [],
            "validate_years": [],
            "holdouts": [],
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_scaffold_clears_signals_and_sets_study_id(tmp_path: Path):
    examples = tmp_path / "configs" / "examples"
    experiments = tmp_path / "configs" / "experiments"
    tmpl = examples / "event_factors.yaml"
    _write_template(tmpl, with_signals=True)
    out = experiments / "plat_v1.yaml"
    result = scaffold_experiment_yaml(
        from_path=tmpl,
        out_path=out,
        study_id="plat_coil",
        examples_dir=examples,
        experiments_dir=experiments,
    )
    assert result["signals_cleared"] is True
    assert result["study_id"] == "plat_coil"
    loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert loaded["signals"]["filters"] == []
    assert loaded["signals"]["rank_by"] == []
    assert loaded["hypothesis"]["study_id"] == "plat_coil"
    assert loaded["hypothesis"]["id"] == "tmpl"  # existing id kept


def test_scaffold_default_id_equals_study_id(tmp_path: Path):
    examples = tmp_path / "configs" / "examples"
    experiments = tmp_path / "configs" / "experiments"
    tmpl = examples / "bare.yaml"
    _write_template(tmpl)
    data = yaml.safe_load(tmpl.read_text(encoding="utf-8"))
    del data["hypothesis"]["id"]
    tmpl.write_text(yaml.safe_dump(data), encoding="utf-8")
    out = experiments / "x.yaml"
    scaffold_experiment_yaml(
        from_path=tmpl,
        out_path=out,
        study_id="sid1",
        examples_dir=examples,
        experiments_dir=experiments,
    )
    loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert loaded["hypothesis"]["id"] == "sid1"


def test_scaffold_rejects_bad_paths(tmp_path: Path):
    examples = tmp_path / "configs" / "examples"
    experiments = tmp_path / "configs" / "experiments"
    tmpl = examples / "ok.yaml"
    _write_template(tmpl)
    bad_from = experiments / "hist.yaml"
    _write_template(bad_from)

    with pytest.raises(ScaffoldError, match="examples"):
        scaffold_experiment_yaml(
            from_path=bad_from,
            out_path=experiments / "a.yaml",
            study_id="s",
            examples_dir=examples,
            experiments_dir=experiments,
        )

    with pytest.raises(ScaffoldError, match="examples"):
        scaffold_experiment_yaml(
            from_path=tmpl,
            out_path=examples / "nope.yaml",
            study_id="s",
            examples_dir=examples,
            experiments_dir=experiments,
        )

    with pytest.raises(ScaffoldError, match="experiments"):
        scaffold_experiment_yaml(
            from_path=tmpl,
            out_path=tmp_path / "elsewhere" / "x.yaml",
            study_id="s",
            examples_dir=examples,
            experiments_dir=experiments,
        )

    out = experiments / "once.yaml"
    scaffold_experiment_yaml(
        from_path=tmpl,
        out_path=out,
        study_id="s",
        examples_dir=examples,
        experiments_dir=experiments,
    )
    with pytest.raises(ScaffoldError, match="already exists"):
        scaffold_experiment_yaml(
            from_path=tmpl,
            out_path=out,
            study_id="s",
            examples_dir=examples,
            experiments_dir=experiments,
        )


def test_scaffold_injects_evaluation_and_set(tmp_path: Path):
    examples = tmp_path / "configs" / "examples"
    experiments = tmp_path / "configs" / "experiments"
    tmpl = examples / "no_eval.yaml"
    _write_template(tmpl, with_eval=False)
    out = experiments / "e.yaml"
    result = scaffold_experiment_yaml(
        from_path=tmpl,
        out_path=out,
        study_id="s",
        sets=["hypothesis.id=custom", "evaluation.primary_metric=excess"],
        examples_dir=examples,
        experiments_dir=experiments,
    )
    assert result["evaluation_injected"] is True
    loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert loaded["evaluation"]["train_years"] == []
    assert loaded["evaluation"]["primary_metric"] == "excess"
    assert loaded["hypothesis"]["id"] == "custom"
    assert loaded["signals"]["filters"] == []

    with pytest.raises(ScaffoldError, match="invalid --set"):
        scaffold_experiment_yaml(
            from_path=tmpl,
            out_path=experiments / "e2.yaml",
            study_id="s",
            sets=["not_a_pair"],
            examples_dir=examples,
            experiments_dir=experiments,
        )


def test_cli_config_new_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path
    examples = root / "configs" / "examples"
    experiments = root / "configs" / "experiments"
    tmpl = examples / "event_factors.yaml"
    _write_template(tmpl, with_signals=True)
    monkeypatch.chdir(root)
    out = "configs/experiments/cli_v1.yaml"
    runner = CliRunner()
    # Global --format/--quiet are stripped at process argv bootstrap; omit in CliRunner.
    result = runner.invoke(
        app,
        [
            "config",
            "new",
            "--out",
            out,
            "--study-id",
            "cli_study",
            "--set",
            "hypothesis.id=cli_id",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (root / out).exists()
    loaded = yaml.safe_load((root / out).read_text(encoding="utf-8"))
    assert loaded["hypothesis"]["study_id"] == "cli_study"
    assert loaded["signals"]["filters"] == []
