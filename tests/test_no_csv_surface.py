from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from qresearch.cli import app


def test_retained_cli_help_has_no_csv_options_and_removed_groups_are_absent() -> None:
    runner = CliRunner()
    retained = [
        [],
        ["data"],
        ["data", "ping"],
        ["data", "clear-cache"],
        ["research"],
        ["research", "factors"],
        ["research", "materialize"],
        ["research", "evaluate"],
        ["config"],
        ["config", "new"],
        ["config", "apply-best"],
        ["analyze"],
        ["analyze", "trades"],
        ["analyze", "report"],
        ["runs"],
        ["study"],
    ]
    for command in retained:
        result = runner.invoke(app, [*command, "--help"])
        assert result.exit_code == 0, (command, result.output)
        assert "--csv" not in result.output, command

    top_help = runner.invoke(app, ["--help"])
    assert top_help.exit_code == 0
    for removed in ("pipeline", "factor", "backtest", "validate", "ops"):
        assert removed not in top_help.output
    data_help = runner.invoke(app, ["data", "--help"])
    assert "validate-events" not in data_help.output


def test_package_has_no_retired_event_or_factor_analysis_symbols() -> None:
    root = Path(__file__).resolve().parents[1] / "qresearch"
    forbidden = (
        "load_events",
        "validate_events",
        "events_path",
        "EventSampleProvider",
        "compute_ic_table",
        "compute_quantile_returns",
        "apply_factor_preprocess",
    )
    offenders: dict[str, list[str]] = {}
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        hits = [symbol for symbol in forbidden if symbol in text]
        if hits:
            offenders[path.relative_to(root).as_posix()] = hits
    assert offenders == {}
