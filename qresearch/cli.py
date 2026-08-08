"""Typer CLI entrypoint: qr."""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv

load_dotenv()

from qresearch import __version__
from qresearch.config import get_settings
from qresearch.engines.analysis.report import write_report_from_run
from qresearch.engines.data.vendor import VendorError, ping_vendor
from qresearch.engines.experiment.decision_log import list_decisions, write_decision
from qresearch.engines.experiment.promote import promote_run
from qresearch.engines.experiment.registry import archive_run, list_runs, load_run_meta
from qresearch.io.envelope import ExitCode, ResultEnvelope, emit, fail_envelope, utc_now_iso
from qresearch.engines.analysis.trades_diagnostics import analyze_trades_run
from qresearch.engines.experiment.best_params import ApplyBestError, apply_best_to_yaml
from qresearch.engines.experiment.scaffold import ScaffoldError, scaffold_experiment_yaml
from qresearch.research.pipeline import (
    ResearchConfigurationError,
    evaluate_research,
    materialize_research,
)
from qresearch.pipeline import (
    pipeline_optimize,
    pipeline_research,
    pipeline_sensitivity,
    pipeline_sweep,
)
from qresearch.research.providers.market import ResearchDataError
from qresearch.research.providers.zer0factor import (
    ResearchFeatureError,
    Zer0FactorDependencyError,
    get_factor_storage,
    list_available_factors,
)

app = typer.Typer(no_args_is_help=True, add_completion=False)
data_app = typer.Typer(no_args_is_help=True)
research_app = typer.Typer(no_args_is_help=True)
analyze_app = typer.Typer(no_args_is_help=True)
runs_app = typer.Typer(no_args_is_help=True)
study_app = typer.Typer(no_args_is_help=True)
config_app = typer.Typer(no_args_is_help=True)
pipeline_app = typer.Typer(no_args_is_help=True)

app.add_typer(data_app, name="data")
app.add_typer(research_app, name="research")
app.add_typer(analyze_app, name="analyze")
app.add_typer(runs_app, name="runs")
app.add_typer(study_app, name="study")
app.add_typer(config_app, name="config")
app.add_typer(pipeline_app, name="pipeline")

# mutable CLI prefs; accept --format/--quiet anywhere in argv
_CLI = {"format": "json", "quiet": False}


def _bootstrap_global_flags() -> None:
    """Parse and strip global I/O flags so they work after subcommands too."""
    out: list[str] = [sys.argv[0]]
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--quiet":
            _CLI["quiet"] = True
            i += 1
            continue
        if a == "--format" and i + 1 < len(args):
            _CLI["format"] = args[i + 1]
            i += 2
            continue
        if a.startswith("--format="):
            _CLI["format"] = a.split("=", 1)[1]
            i += 1
            continue
        out.append(a)
        i += 1
    sys.argv = out


_bootstrap_global_flags()


@app.callback()
def main():
    """qresearch CLI."""
    return None


def _out(env: ResultEnvelope, code: int = 0) -> None:
    rc = emit(
        env,
        format=_CLI.get("format", "json"),
        quiet=_CLI.get("quiet", False),
        exit_code=code,
    )
    raise SystemExit(rc)


@app.command("version")
def version_cmd():
    started = utc_now_iso()
    env = ResultEnvelope(
        command="version",
        started_at=started,
        finished_at=utc_now_iso(),
        summary={"version": __version__},
    )
    _out(env)


@data_app.command("ping")
def data_ping():
    started = utc_now_iso()
    t0 = time.time()
    info = ping_vendor()
    ok = bool(info.get("import_ok"))
    env = ResultEnvelope(
        ok=ok,
        command="data.ping",
        started_at=started,
        finished_at=utc_now_iso(),
        elapsed_ms=int((time.time() - t0) * 1000),
        status="succeeded" if ok else "failed",
        summary=info,
        error=None
        if ok
        else {"code": "dependency", "message": info.get("error") or "zer0share unavailable"},
    )
    # fix error type
    if not ok:
        env, code = fail_envelope(
            "data.ping",
            started,
            code="dependency",
            message=str(info.get("error") or "zer0share unavailable"),
            exit_code=ExitCode.DEPENDENCY,
        )
        env.summary = info
        _out(env, int(code))
    _out(env)


@data_app.command("clear-cache")
def data_clear_cache():
    started = utc_now_iso()
    settings = get_settings()
    if settings.cache_dir.exists():
        shutil.rmtree(settings.cache_dir)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    env = ResultEnvelope(
        command="data.clear-cache",
        started_at=started,
        finished_at=utc_now_iso(),
        summary={"cache_dir": str(settings.cache_dir), "cleared": True},
    )
    _out(env)


def _research_failure(command: str, started: str, error: Exception) -> None:
    if isinstance(error, ResearchConfigurationError):
        code = ExitCode.CONFIG
        error_code = "config"
    elif isinstance(error, (ResearchDataError, ResearchFeatureError)):
        code = ExitCode.DATA
        error_code = "data"
    elif isinstance(error, (VendorError, Zer0FactorDependencyError)):
        code = ExitCode.DEPENDENCY
        error_code = "dependency"
    else:
        code = ExitCode.ERROR
        error_code = "error"
    env, exit_code = fail_envelope(
        command,
        started,
        code=error_code,
        message=str(error),
        exit_code=code,
    )
    _out(env, int(exit_code))


@research_app.command("factors")
def research_factors() -> None:
    started = utc_now_iso()
    try:
        names = list_available_factors(get_factor_storage(get_settings()))
        _out(
            ResultEnvelope(
                command="research.factors",
                started_at=started,
                finished_at=utc_now_iso(),
                summary={"count": len(names), "factors": names},
            )
        )
    except Exception as error:
        _research_failure("research.factors", started, error)


def _run_research_command(
    command: str,
    operation,
    config: str,
    run_id: str | None,
) -> None:
    started = utc_now_iso()
    t0 = time.time()
    try:
        result = operation(config, run_id=run_id)
        _out(
            ResultEnvelope(
                command=command,
                started_at=started,
                finished_at=utc_now_iso(),
                elapsed_ms=int((time.time() - t0) * 1000),
                run_id=result["run_id"],
                summary=result["summary"],
                artifacts=result["artifacts"],
            )
        )
    except Exception as error:
        _research_failure(command, started, error)


@research_app.command("materialize")
def research_materialize(
    config: str = typer.Option(..., "--config"),
    run_id: Optional[str] = typer.Option(None, "--run-id"),
) -> None:
    _run_research_command(
        "research.materialize", materialize_research, config, run_id
    )


@research_app.command("evaluate")
def research_evaluate(
    config: str = typer.Option(..., "--config"),
    run_id: Optional[str] = typer.Option(None, "--run-id"),
) -> None:
    _run_research_command("research.evaluate", evaluate_research, config, run_id)


@pipeline_app.command("research")
def pipeline_research_cmd(
    config: str = typer.Option(..., "--config"),
    run_id: str = typer.Option(..., "--run-id"),
    role: str = typer.Option(..., "--role"),
    n_trials_assumed: Optional[int] = typer.Option(None, "--n-trials-assumed"),
) -> None:
    started = utc_now_iso()
    try:
        result = pipeline_research(config, run_id=run_id, role=role, n_trials_assumed=n_trials_assumed)
        _out(ResultEnvelope(command="pipeline.research", started_at=started, finished_at=utc_now_iso(), run_id=result["run_id"], summary=result["summary"], artifacts=result["artifacts"]))
    except Exception as error:
        _research_failure("pipeline.research", started, error)


def _run_pipeline_command(command: str, operation, *args, **kwargs) -> None:
    started = utc_now_iso()
    try:
        result = operation(*args, **kwargs)
        _out(ResultEnvelope(command=command, started_at=started, finished_at=utc_now_iso(), run_id=result["run_id"], summary=result["summary"], artifacts=result["artifacts"]))
    except Exception as error:
        _research_failure(command, started, error)


@pipeline_app.command("optimize")
def pipeline_optimize_cmd(config: str = typer.Option(..., "--config"), run_id: str = typer.Option(..., "--run-id"), role: str = typer.Option("train", "--role"), feature: Optional[str] = typer.Option(None, "--feature"), side: str = typer.Option("auto", "--side"), keep_frac: str = typer.Option("0.1,0.2,0.3,0.4", "--keep-frac"), n_trials: Optional[int] = typer.Option(None, "--n-trials")) -> None:
    _run_pipeline_command("pipeline.optimize", pipeline_optimize, config, run_id=run_id, role=role, feature=feature, side=side, keep_frac=keep_frac, n_trials=n_trials)


@pipeline_app.command("sweep")
def pipeline_sweep_cmd(config: str = typer.Option(..., "--config"), run_id: str = typer.Option(..., "--run-id"), role: str = typer.Option("train", "--role"), set_specs: list[str] = typer.Option(..., "--set"), metric: str = typer.Option("sharpe", "--metric"), max_grid: int = typer.Option(64, "--max-grid")) -> None:
    _run_pipeline_command("pipeline.sweep", pipeline_sweep, config, run_id=run_id, role=role, set_specs=set_specs, metric=metric, max_grid=max_grid)


@pipeline_app.command("sensitivity")
def pipeline_sensitivity_cmd(config: str = typer.Option(..., "--config"), run_id: str = typer.Option(..., "--run-id"), role: str = typer.Option("train", "--role"), cost_mult: str = typer.Option("1,1.5,2", "--cost-mult"), stop: str = typer.Option("-0.05,-0.086,-0.12", "--stop"), take: str = typer.Option("0.10,0.158,0.20", "--take"), max_grid: int = typer.Option(64, "--max-grid")) -> None:
    _run_pipeline_command("pipeline.sensitivity", pipeline_sensitivity, config, run_id=run_id, role=role, cost_mult=cost_mult, stop=stop, take=take, max_grid=max_grid)


@analyze_app.command("trades")
def analyze_trades(
    run: str = typer.Option(..., "--run"),
    role: Optional[str] = typer.Option(None, "--role"),
):
    """Read-only trade / invested / reject / yearly diagnostics for a finished run."""
    started = utc_now_iso()
    settings = get_settings()
    run_dir = settings.runs_dir / run
    if not run_dir.exists():
        env, code = fail_envelope(
            "analyze.trades",
            started,
            code="data",
            message=f"missing run dir {run_dir}",
            exit_code=ExitCode.DATA,
        )
        _out(env, int(code))
        return
    try:
        result = analyze_trades_run(run_dir, role=role)
        env = ResultEnvelope(
            command="analyze.trades",
            started_at=started,
            finished_at=utc_now_iso(),
            run_id=run,
            summary=result["summary"],
            artifacts=result["artifacts"],
        )
        _out(env)
    except Exception as e:
        env, code = fail_envelope(
            "analyze.trades", started, code="error", message=str(e), exit_code=ExitCode.ERROR
        )
        _out(env, int(code))


@config_app.command("new")
def config_new(
    out: str = typer.Option(..., "--out", help="new YAML under configs/experiments/"),
    study_id: str = typer.Option(..., "--study-id", help="hypothesis.study_id"),
    from_path: str = typer.Option(
        "configs/examples/market_factors.yaml",
        "--from",
        help="template under configs/examples/",
    ),
    sets: Optional[list[str]] = typer.Option(
        None,
        "--set",
        help="dotted key=value override (repeatable); signals still forced empty",
    ),
):
    """Scaffold experiment YAML from examples (empty signals; never write examples/)."""
    started = utc_now_iso()
    try:
        result = scaffold_experiment_yaml(
            from_path=Path(from_path),
            out_path=Path(out),
            study_id=study_id,
            sets=list(sets or []),
            examples_dir=Path("configs/examples"),
            experiments_dir=Path("configs/experiments"),
        )
        env = ResultEnvelope(
            command="config.new",
            started_at=started,
            finished_at=utc_now_iso(),
            summary={
                "out": result["out"],
                "from": result["from"],
                "study_id": result["study_id"],
                "signals_cleared": result["signals_cleared"],
                "evaluation_injected": result["evaluation_injected"],
                "sets_applied": result["sets_applied"],
            },
            artifacts={"config": result["out"]},
            next_actions=[
                {"op": "research.materialize", "config": result["out"]},
                {"op": "research.evaluate", "config": result["out"]},
                {"op": "study.decision", "stage": "factor_analysis", "config": result["out"]},
            ],
        )
        _out(env)
    except ScaffoldError as e:
        env, code = fail_envelope(
            "config.new",
            started,
            code="config",
            message=str(e),
            exit_code=ExitCode.CONFIG,
        )
        _out(env, int(code))
    except Exception as e:
        env, code = fail_envelope(
            "config.new", started, code="error", message=str(e), exit_code=ExitCode.ERROR
        )
        _out(env, int(code))


@config_app.command("apply-best")
def config_apply_best(
    from_run: str = typer.Option(..., "--from-run"),
    out: str = typer.Option(..., "--out", help="new YAML path under configs/experiments/"),
):
    """Patch searched keys from a sweep/optimize/sensitivity run into a new YAML (never examples/)."""
    started = utc_now_iso()
    settings = get_settings()
    run_dir = settings.runs_dir / from_run
    try:
        result = apply_best_to_yaml(
            run_dir=run_dir,
            out_path=Path(out),
            examples_dir=Path("configs/examples"),
        )
        env = ResultEnvelope(
            command="config.apply-best",
            started_at=started,
            finished_at=utc_now_iso(),
            run_id=from_run,
            summary={
                "out": result["out"],
                "source": result["source"],
                "n_patches": result["n_patches"],
                "best_params": result["best_params"],
            },
            artifacts={"config": result["out"]},
        )
        _out(env)
    except ApplyBestError as e:
        env, code = fail_envelope(
            "config.apply-best",
            started,
            code="config",
            message=str(e),
            exit_code=ExitCode.CONFIG,
        )
        _out(env, int(code))
    except Exception as e:
        env, code = fail_envelope(
            "config.apply-best", started, code="error", message=str(e), exit_code=ExitCode.ERROR
        )
        _out(env, int(code))


@analyze_app.command("report")
def analyze_report(
    run: str = typer.Option(..., "--run"),
    role: Optional[str] = typer.Option(None, "--role"),
    train_run: Optional[str] = typer.Option(
        None, "--train-run", help="override train split run_id for side-by-side table"
    ),
    validate_run: Optional[str] = typer.Option(
        None, "--validate-run", help="override validate (freeze) split run_id"
    ),
    holdout_run: Optional[str] = typer.Option(
        None, "--holdout-run", help="override holdout final split run_id"
    ),
    holdout_stress_run: Optional[str] = typer.Option(
        None, "--holdout-stress-run", help="override holdout stress split run_id"
    ),
    full_run: Optional[str] = typer.Option(
        None, "--full-run", help="override full-sample (disclose-only) split run_id"
    ),
):
    """Rebuild HTML/JSON report; study decisions add multi-window split table."""
    started = utc_now_iso()
    settings = get_settings()
    run_dir = settings.runs_dir / run
    if not run_dir.exists():
        env, code = fail_envelope(
            "analyze.report",
            started,
            code="data",
            message=f"missing run dir {run_dir}",
            exit_code=ExitCode.DATA,
        )
        _out(env, int(code))
    try:
        html_path, json_path = write_report_from_run(
            run_dir,
            train_run=train_run,
            validate_run=validate_run,
            holdout_run=holdout_run,
            holdout_stress_run=holdout_stress_run,
            full_run=full_run,
            role=role,
        )
        data = json.loads(json_path.read_text(encoding="utf-8"))
        split = data.get("split_comparison") or {}
        env = ResultEnvelope(
            command="analyze.report",
            started_at=started,
            finished_at=utc_now_iso(),
            run_id=run,
            summary={
                "promotable": data.get("promotable"),
                "metrics": data.get("metrics"),
                "trade_stats": data.get("trade_stats"),
                "title": data.get("title"),
                "locale": data.get("locale"),
                "split_comparison_n": split.get("n_present"),
                "split_roles": [
                    {"role": r.get("role"), "run_id": r.get("run_id"), "missing": r.get("missing")}
                    for r in (split.get("rows") or [])
                ],
            },
            artifacts={
                "conclusion_json": str(json_path),
                "conclusion_html": str(html_path),
                "research_report_zh": str(run_dir / "report" / "research_report_zh.html"),
                "split_comparison": str(run_dir / "artifacts" / "split_comparison.json")
                if (run_dir / "artifacts" / "split_comparison.json").exists()
                else "",
            },
        )
        _out(env)
    except Exception as e:
        env, code = fail_envelope(
            "analyze.report", started, code="error", message=str(e), exit_code=ExitCode.ERROR
        )
        _out(env, int(code))


@app.command("promote")
def promote_cmd(
    run: str = typer.Option(..., "--run"),
    model_id: str = typer.Option(..., "--model-id"),
    version: str = typer.Option(..., "--version"),
    force: bool = typer.Option(False, "--force"),
):
    started = utc_now_iso()
    settings = get_settings()
    try:
        dest = promote_run(
            settings.runs_dir,
            settings.packages_dir,
            run,
            model_id,
            version,
            force=force,
        )
        env = ResultEnvelope(
            command="promote",
            started_at=started,
            finished_at=utc_now_iso(),
            run_id=run,
            summary={"model_id": model_id, "version": version, "path": str(dest), "forced": force},
            artifacts={"package_dir": str(dest)},
        )
        _out(env)
    except PermissionError as e:
        env, code = fail_envelope(
            "promote", started, code="blocked", message=str(e), exit_code=ExitCode.BLOCKED
        )
        _out(env, int(code))
    except Exception as e:
        env, code = fail_envelope(
            "promote", started, code="error", message=str(e), exit_code=ExitCode.ERROR
        )
        _out(env, int(code))


@runs_app.command("list")
def runs_list():
    started = utc_now_iso()
    settings = get_settings()
    rows = list_runs(settings.runs_dir)
    env = ResultEnvelope(
        command="runs.list",
        started_at=started,
        finished_at=utc_now_iso(),
        summary={"n": len(rows), "runs": rows[:50]},
    )
    _out(env)


@runs_app.command("show")
def runs_show(run: str = typer.Option(..., "--run")):
    started = utc_now_iso()
    settings = get_settings()
    try:
        meta = load_run_meta(settings.runs_dir, run)
        env = ResultEnvelope(
            command="runs.show",
            started_at=started,
            finished_at=utc_now_iso(),
            run_id=run,
            summary=meta,
        )
        _out(env)
    except Exception as e:
        env, code = fail_envelope(
            "runs.show", started, code="data", message=str(e), exit_code=ExitCode.DATA
        )
        _out(env, int(code))


@runs_app.command("compare")
def runs_compare(runs: str = typer.Option(..., "--runs", help="comma-separated run ids")):
    started = utc_now_iso()
    settings = get_settings()
    ids = [x.strip() for x in runs.split(",") if x.strip()]
    rows = []
    for rid in ids:
        meta = load_run_meta(settings.runs_dir, rid)
        rows.append({"run_id": rid, "metrics": meta.get("metrics"), "promotable": meta.get("promotable")})
    env = ResultEnvelope(
        command="runs.compare",
        started_at=started,
        finished_at=utc_now_iso(),
        summary={"rows": rows},
    )
    _out(env)


@runs_app.command("archive")
def runs_archive(
    run: str = typer.Option(..., "--run"),
    out: str = typer.Option(..., "--out"),
):
    started = utc_now_iso()
    settings = get_settings()
    path = archive_run(settings.runs_dir, run, Path(out))
    env = ResultEnvelope(
        command="runs.archive",
        started_at=started,
        finished_at=utc_now_iso(),
        run_id=run,
        artifacts={"archive": str(path)},
    )
    _out(env)


@study_app.command("decision")
def study_decision(
    study: str = typer.Option(..., "--study", help="study id, e.g. plat_box_2019_2025"),
    stage: str = typer.Option(
        ...,
        "--stage",
        help="factor_analysis|strategy_design|backtest_train|backtest_validate|sensitivity|optimize|sweep|holdout|holdout_stress|full_sample|promote|other",
    ),
    summary: str = typer.Option(..., "--summary", help="one-line result / decision"),
    rationale: str = typer.Option(..., "--rationale", help="why this decision"),
    evidence: Optional[str] = typer.Option(
        None, "--evidence", help="JSON object string or path to .json file"
    ),
    run: Optional[str] = typer.Option(None, "--run", help="related run_id"),
    config: Optional[str] = typer.Option(None, "--config", help="YAML path"),
    next_action: Optional[str] = typer.Option(None, "--next-action"),
):
    """Archive a stage decision (factor / strategy / backtest) for audit trail."""
    started = utc_now_iso()
    settings = get_settings()
    ev: dict = {}
    if evidence:
        p = Path(evidence)
        try:
            if p.exists():
                ev = json.loads(p.read_text(encoding="utf-8"))
            else:
                ev = json.loads(evidence)
        except json.JSONDecodeError as e:
            env, code = fail_envelope(
                "study.decision",
                started,
                code="config",
                message=f"invalid --evidence JSON: {e}",
                exit_code=ExitCode.CONFIG,
            )
            _out(env, int(code))
    try:
        out = write_decision(
            settings.studies_dir,
            study_id=study,
            stage=stage,
            summary=summary,
            rationale=rationale,
            evidence=ev,
            run_id=run,
            config_path=config,
            next_action=next_action,
            runs_dir=settings.runs_dir,
        )
        arts = {
            "md": out["md_path"],
            "json": out["json_path"],
            "index": out["index_path"],
        }
        if out.get("run_mirror"):
            arts["run_decisions"] = out["run_mirror"].get("json_path") or ""
            arts["run_report"] = out["run_mirror"].get("report_zh") or ""
        env = ResultEnvelope(
            command="study.decision",
            started_at=started,
            finished_at=utc_now_iso(),
            run_id=run,
            summary=out,
            artifacts=arts,
        )
        _out(env)
    except Exception as e:
        env, code = fail_envelope(
            "study.decision", started, code="error", message=str(e), exit_code=ExitCode.ERROR
        )
        _out(env, int(code))


@study_app.command("list")
def study_list(study: str = typer.Option(..., "--study")):
    started = utc_now_iso()
    settings = get_settings()
    rows = list_decisions(settings.studies_dir, study)
    env = ResultEnvelope(
        command="study.list",
        started_at=started,
        finished_at=utc_now_iso(),
        summary={"study_id": study, "n": len(rows), "decisions": rows},
    )
    _out(env)


if __name__ == "__main__":
    app()
