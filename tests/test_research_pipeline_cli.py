from __future__ import annotations

import json

from typer.testing import CliRunner

from qresearch.cli import app


def test_pipeline_research_uses_market_runner_and_emits_market_envelope(monkeypatch) -> None:
    monkeypatch.setattr(
        "qresearch.cli.pipeline_research",
        lambda config, *, run_id, role, n_trials_assumed=None: {
            "run_id": "market-run",
            "summary": {"sample_kind": "market", "snapshot_sha256": "sha", "role": role},
            "artifacts": {},
        },
    )

    result = CliRunner().invoke(
        app,
        ["pipeline", "research", "--config", "market.yaml", "--run-id", "market-run", "--role", "train"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["command"] == "pipeline.research"
    assert payload["summary"]["sample_kind"] == "market"
    assert payload["summary"]["role"] == "train"


def test_pipeline_optimization_commands_require_config_without_csv() -> None:
    runner = CliRunner()
    for command in ("research", "optimize", "sweep", "sensitivity"):
        result = runner.invoke(app, ["pipeline", command, "--help"])
        assert result.exit_code == 0, result.output
        assert "--config" in result.output
        assert "--run-id" in result.output
        assert "--role" in result.output
        assert "--csv" not in result.output


def test_pipeline_research_requires_a_frozen_run_and_explicit_role(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_pipeline(config, *, run_id, role, n_trials_assumed=None):
        seen.update(config=config, run_id=run_id, role=role, n_trials_assumed=n_trials_assumed)
        return {"run_id": run_id, "summary": {"role": role}, "artifacts": {}}

    monkeypatch.setattr("qresearch.cli.pipeline_research", fake_pipeline)

    result = CliRunner().invoke(
        app,
        [
            "pipeline", "research", "--config", "market.yaml", "--run-id", "frozen-1", "--role", "validate",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == {
        "config": "market.yaml",
        "run_id": "frozen-1",
        "role": "validate",
        "n_trials_assumed": None,
    }
