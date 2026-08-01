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
from qresearch.config import get_settings, load_research_config
from qresearch.engines.analysis.report import write_report_from_run
from qresearch.engines.data.ingest import IngestError, load_events, validate_events
from qresearch.engines.data.vendor import VendorError, ping_vendor
from qresearch.engines.experiment.promote import promote_run
from qresearch.engines.experiment.registry import archive_run, list_runs, load_run_meta
from qresearch.engines.experiment.walkforward import run_walk_forward
from qresearch.engines.data.panel import load_price_panel
from qresearch.engines.factor.ic import compute_ic_table
from qresearch.engines.ops.runner import run_ops
from qresearch.io.envelope import ExitCode, ResultEnvelope, emit, fail_envelope, utc_now_iso
from qresearch.pipeline import pipeline_optimize, pipeline_research

app = typer.Typer(no_args_is_help=True, add_completion=False)
data_app = typer.Typer(no_args_is_help=True)
pipeline_app = typer.Typer(no_args_is_help=True)
factor_app = typer.Typer(no_args_is_help=True)
backtest_app = typer.Typer(no_args_is_help=True)
analyze_app = typer.Typer(no_args_is_help=True)
runs_app = typer.Typer(no_args_is_help=True)
ops_app = typer.Typer(no_args_is_help=True)
validate_app = typer.Typer(no_args_is_help=True)

app.add_typer(data_app, name="data")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(factor_app, name="factor")
app.add_typer(backtest_app, name="backtest")
app.add_typer(analyze_app, name="analyze")
app.add_typer(runs_app, name="runs")
app.add_typer(ops_app, name="ops")
app.add_typer(validate_app, name="validate")

# mutable CLI output prefs; accept --format/--quiet anywhere in argv
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


@data_app.command("validate-events")
def data_validate_events(
    csv: list[str] = typer.Option(..., "--csv"),
    config: Optional[str] = typer.Option(None, "--config"),
):
    started = utc_now_iso()
    try:
        cfg = load_research_config(config)
        summary = validate_events(csv, cfg)
        env = ResultEnvelope(
            command="data.validate-events",
            started_at=started,
            finished_at=utc_now_iso(),
            summary=summary,
        )
        _out(env)
    except IngestError as e:
        env, code = fail_envelope(
            "data.validate-events", started, code="data", message=str(e), exit_code=ExitCode.DATA
        )
        _out(env, int(code))


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


@pipeline_app.command("research")
def pipeline_research_cmd(
    csv: list[str] = typer.Option(..., "--csv"),
    config: Optional[str] = typer.Option(None, "--config"),
    run_id: Optional[str] = typer.Option(None, "--run-id"),
    n_trials_assumed: Optional[int] = typer.Option(
        None, "--n-trials-assumed", help="assumed independent trials for deflated Sharpe"
    ),
):
    started = utc_now_iso()
    t0 = time.time()
    try:
        result = pipeline_research(
            csv, config, run_id=run_id, n_trials_assumed=n_trials_assumed
        )
        env = ResultEnvelope(
            command="pipeline.research",
            started_at=started,
            finished_at=utc_now_iso(),
            elapsed_ms=int((time.time() - t0) * 1000),
            run_id=result["run_id"],
            summary=result["summary"],
            artifacts=result["artifacts"],
            next_actions=result["next_actions"],
            status="blocked" if not result["summary"].get("promotable") else "succeeded",
        )
        code = ExitCode.OK
        # blocked is still ok exit 0 for research completion; promote uses code 4
        _out(env, int(code))
    except typer.Exit:
        raise
    except IngestError as e:
        env, code = fail_envelope(
            "pipeline.research", started, code="data", message=str(e), exit_code=ExitCode.DATA
        )
        _out(env, int(code))
    except VendorError as e:
        env, code = fail_envelope(
            "pipeline.research",
            started,
            code="dependency",
            message=str(e),
            exit_code=ExitCode.DEPENDENCY,
        )
        _out(env, int(code))
    except Exception as e:
        env, code = fail_envelope(
            "pipeline.research", started, code="error", message=str(e), exit_code=ExitCode.ERROR
        )
        _out(env, int(code))


@pipeline_app.command("optimize")
def pipeline_optimize_cmd(
    csv: list[str] = typer.Option(..., "--csv"),
    config: Optional[str] = typer.Option(None, "--config"),
    n_trials: int = typer.Option(20, "--n-trials"),
    feature: str = typer.Option("features.box_quality", "--feature"),
):
    started = utc_now_iso()
    t0 = time.time()
    try:
        result = pipeline_optimize(csv, config, n_trials=n_trials, feature=feature)
        env = ResultEnvelope(
            command="pipeline.optimize",
            started_at=started,
            finished_at=utc_now_iso(),
            elapsed_ms=int((time.time() - t0) * 1000),
            run_id=result["run_id"],
            summary=result["summary"],
            artifacts=result["artifacts"],
            next_actions=result["next_actions"],
        )
        _out(env)
    except Exception as e:
        env, code = fail_envelope(
            "pipeline.optimize", started, code="error", message=str(e), exit_code=ExitCode.ERROR
        )
        _out(env, int(code))


@validate_app.command("rolling")
def validate_rolling(
    csv: list[str] = typer.Option(..., "--csv"),
    config: Optional[str] = typer.Option(None, "--config"),
):
    started = utc_now_iso()
    try:
        settings = get_settings()
        cfg = load_research_config(config)
        events = load_events(csv, cfg)
        panel = load_price_panel(events, cfg, cache_dir=settings.cache_dir / "prices")
        wf = run_walk_forward(events, panel, cfg)
        env = ResultEnvelope(
            command="validate.rolling",
            started_at=started,
            finished_at=utc_now_iso(),
            summary=wf.get("aggregate", {}),
            artifacts={"folds": wf.get("folds")},
        )
        _out(env)
    except Exception as e:
        env, code = fail_envelope(
            "validate.rolling", started, code="error", message=str(e), exit_code=ExitCode.ERROR
        )
        _out(env, int(code))


@factor_app.command("ic")
def factor_ic(
    csv: list[str] = typer.Option(..., "--csv"),
    config: Optional[str] = typer.Option(None, "--config"),
    feature: str = typer.Option("features.box_quality", "--feature"),
):
    started = utc_now_iso()
    try:
        settings = get_settings()
        cfg = load_research_config(config)
        events = load_events(csv, cfg)
        panel = load_price_panel(events, cfg, cache_dir=settings.cache_dir / "prices")
        ic = compute_ic_table(events, panel, [feature], cfg.ic_horizons)
        env = ResultEnvelope(
            command="factor.ic",
            started_at=started,
            finished_at=utc_now_iso(),
            summary={"rows": ic.to_dicts()},
        )
        _out(env)
    except Exception as e:
        env, code = fail_envelope(
            "factor.ic", started, code="error", message=str(e), exit_code=ExitCode.ERROR
        )
        _out(env, int(code))


@factor_app.command("compare")
def factor_compare(
    csv: list[str] = typer.Option(..., "--csv"),
    config: Optional[str] = typer.Option(None, "--config"),
):
    started = utc_now_iso()
    try:
        settings = get_settings()
        cfg = load_research_config(config)
        events = load_events(csv, cfg)
        panel = load_price_panel(events, cfg, cache_dir=settings.cache_dir / "prices")
        feats = [c for c in events.columns if c.startswith("features.")]
        ic = compute_ic_table(events, panel, feats, cfg.ic_horizons)
        env = ResultEnvelope(
            command="factor.compare",
            started_at=started,
            finished_at=utc_now_iso(),
            summary={"n_features": len(feats), "rows": ic.to_dicts()[:100]},
        )
        _out(env)
    except Exception as e:
        env, code = fail_envelope(
            "factor.compare", started, code="error", message=str(e), exit_code=ExitCode.ERROR
        )
        _out(env, int(code))


@backtest_app.command("run")
def backtest_run(
    csv: list[str] = typer.Option(..., "--csv"),
    config: Optional[str] = typer.Option(None, "--config"),
):
    # alias to pipeline research without forcing WF/IC heavy — still use pipeline
    pipeline_research_cmd(csv=csv, config=config, run_id=None)


@analyze_app.command("report")
def analyze_report(run: str = typer.Option(..., "--run")):
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
        html_path, json_path = write_report_from_run(run_dir)
        data = json.loads(json_path.read_text(encoding="utf-8"))
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
            },
            artifacts={
                "conclusion_json": str(json_path),
                "conclusion_html": str(html_path),
                "research_report_zh": str(run_dir / "report" / "research_report_zh.html"),
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


@ops_app.command("run")
def ops_run(
    asof: str = typer.Option(..., "--asof"),
    csv: str = typer.Option(..., "--csv"),
    mode: str = typer.Option("signal", "--mode"),
    package: Optional[str] = typer.Option(None, "--package"),
    state: Optional[str] = typer.Option(None, "--state"),
    config: Optional[str] = typer.Option(None, "--config"),
):
    started = utc_now_iso()
    settings = get_settings()
    try:
        pkg_dir = None
        if package:
            # model_id==version or path
            if "==" in package:
                mid, ver = package.split("==", 1)
                pkg_dir = settings.packages_dir / mid / ver
            else:
                pkg_dir = Path(package)
        cfg = load_research_config(config) if config else None
        result = run_ops(
            package_dir=pkg_dir,
            events_path=csv,
            asof=asof,
            mode=mode,
            state_path=state,
            config=cfg,
            cache_dir=settings.cache_dir / "prices",
        )
        out_dir = settings.runs_dir / f"ops_{asof}"
        out_dir.mkdir(parents=True, exist_ok=True)
        orders_path = out_dir / f"orders_{asof}.json"
        orders_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        env = ResultEnvelope(
            command="ops.run",
            started_at=started,
            finished_at=utc_now_iso(),
            summary={
                "n_intents": result["n_intents"],
                "n_rejected": result["n_rejected"],
                "live_ready": result["live_ready"],
                "warnings": result["warnings"],
                "stages": result["stages"],
            },
            artifacts={"orders": str(orders_path)},
            status="blocked" if mode == "signal" and not result["live_ready"] else "succeeded",
        )
        code = ExitCode.OK if result["live_ready"] or mode == "paper" else ExitCode.BLOCKED
        _out(env, int(code))
    except Exception as e:
        env, code = fail_envelope(
            "ops.run", started, code="error", message=str(e), exit_code=ExitCode.ERROR
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


if __name__ == "__main__":
    app()
