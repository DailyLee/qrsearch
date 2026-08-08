from __future__ import annotations

import json

from typer.testing import CliRunner

from qresearch.cli import app


def test_pipeline_research_uses_market_runner_and_emits_market_envelope(monkeypatch) -> None:
    monkeypatch.setattr(
        "qresearch.cli.pipeline_research",
        lambda config, run_id=None, n_trials_assumed=None: {
            "run_id": "market-run",
            "summary": {"sample_kind": "market", "snapshot_sha256": "sha"},
            "artifacts": {},
        },
    )

    result = CliRunner().invoke(app, ["pipeline", "research", "--config", "market.yaml"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["command"] == "pipeline.research"
    assert payload["summary"]["sample_kind"] == "market"


def test_pipeline_optimization_commands_require_config_without_csv() -> None:
    runner = CliRunner()
    for command in ("optimize", "sweep", "sensitivity"):
        result = runner.invoke(app, ["pipeline", command, "--help"])
        assert result.exit_code == 0, result.output
        assert "--config" in result.output
        assert "--csv" not in result.output
