"""Persist per-stage research decisions for audit / replay.

Canonical store: workspace/studies/<study_id>/decisions/
When --run is provided, also mirror into workspace/runs/<run_id>/decisions/
and stamp study_id onto that run's meta.json so the final report can load the trail.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

_STAGES = (
    "factor_analysis",
    "strategy_design",
    "backtest_train",
    "sensitivity",
    "optimize",
    "holdout",
    "full_sample",
    "promote",
    "other",
)


def sanitize_study_id(study_id: str) -> str:
    s = re.sub(r"[^\w\-.]+", "_", study_id.strip(), flags=re.UNICODE)
    return s.strip("_") or "unnamed_study"


def write_decision(
    studies_dir: Path,
    *,
    study_id: str,
    stage: str,
    summary: str,
    rationale: str,
    evidence: dict[str, Any] | None = None,
    run_id: str | None = None,
    config_path: str | None = None,
    next_action: str | None = None,
    runs_dir: Path | None = None,
) -> dict[str, Any]:
    """Write JSON + Markdown under studies/; mirror into run when run_id+runs_dir set."""
    if stage not in _STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {_STAGES}")
    sid = sanitize_study_id(study_id)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    root = Path(studies_dir) / sid
    dec_dir = root / "decisions"
    dec_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "schema_version": "1.1",
        "study_id": sid,
        "stage": stage,
        "created_at": datetime.now().astimezone().isoformat(),
        "summary": summary,
        "rationale": rationale,
        "evidence": evidence or {},
        "run_id": run_id,
        "config_path": config_path,
        "next_action": next_action,
    }
    stem = f"{stamp}_{stage}"
    json_path = dec_dir / f"{stem}.json"
    md_path = dec_dir / f"{stem}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_to_markdown(payload), encoding="utf-8")
    index_path = _update_index(root, payload, md_path)

    run_mirror: dict[str, str] | None = None
    if run_id and runs_dir:
        run_mirror = _mirror_to_run(Path(runs_dir), run_id, sid, stem, json_path, md_path, payload)

    return {
        "study_id": sid,
        "stage": stage,
        "json_path": str(json_path),
        "md_path": str(md_path),
        "index_path": str(index_path),
        "run_id": run_id,
        "run_mirror": run_mirror,
    }


def _mirror_to_run(
    runs_dir: Path,
    run_id: str,
    study_id: str,
    stem: str,
    json_path: Path,
    md_path: Path,
    payload: dict[str, Any],
) -> dict[str, str] | None:
    run_root = Path(runs_dir) / run_id
    if not run_root.is_dir():
        return None
    run_dec = run_root / "decisions"
    run_dec.mkdir(parents=True, exist_ok=True)
    dst_json = run_dec / f"{stem}.json"
    dst_md = run_dec / f"{stem}.md"
    shutil.copy2(json_path, dst_json)
    shutil.copy2(md_path, dst_md)
    _update_index(run_root, payload, dst_md, heading=f"# Run `{run_id}` · study `{study_id}`\n\n## Decision log\n\n")
    _stamp_run_meta(run_root, study_id=study_id, run_id=run_id)
    report_zh = run_root / "report" / "research_report_zh.html"
    study_root = json_path.parent.parent  # .../studies/<id>
    _append_study_run_link(study_root, run_id, report_zh if report_zh.exists() else run_root)
    return {
        "run_dir": str(run_root),
        "json_path": str(dst_json),
        "md_path": str(dst_md),
        "report_zh": str(report_zh) if report_zh.exists() else "",
    }


def _stamp_run_meta(run_root: Path, *, study_id: str, run_id: str) -> None:
    meta_path = run_root / "meta.json"
    meta: dict[str, Any] = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["study_id"] = study_id
    meta["run_id"] = meta.get("run_id") or run_id
    stages: list[str] = []
    for p in sorted((run_root / "decisions").glob("*.json")):
        name = p.stem
        # YYYYMMDD_HHMMSS_stage — stage may contain underscores; strip first two time tokens
        parts = name.split("_")
        if len(parts) >= 3:
            stages.append("_".join(parts[2:]))
        else:
            stages.append(name)
    meta["decision_stages"] = stages
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _append_study_run_link(study_root: Path, run_id: str, report_or_run: Path) -> None:
    path = study_root / "RUNS.md"
    if not path.exists():
        path.write_text(f"# Linked runs\n\n", encoding="utf-8")
    line = f"- `{run_id}` → `{report_or_run}`\n"
    existing = path.read_text(encoding="utf-8")
    if run_id in existing and str(report_or_run) in existing:
        return
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def _to_markdown(p: dict[str, Any]) -> str:
    lines = [
        f"# Decision · {p['stage']}",
        "",
        f"- study: `{p['study_id']}`",
        f"- created_at: {p['created_at']}",
    ]
    if p.get("run_id"):
        lines.append(f"- run_id: `{p['run_id']}`")
    if p.get("config_path"):
        lines.append(f"- config: `{p['config_path']}`")
    if p.get("next_action"):
        lines.append(f"- next_action: {p['next_action']}")
    lines += ["", "## Summary", "", p.get("summary") or "(empty)", "", "## Rationale", "", p.get("rationale") or "(empty)", ""]
    ev = p.get("evidence") or {}
    if ev:
        lines += ["## Evidence", "", "```json", json.dumps(ev, ensure_ascii=False, indent=2, default=str), "```", ""]
    return "\n".join(lines)


def _update_index(
    root: Path,
    payload: dict[str, Any],
    md_path: Path,
    *,
    heading: str | None = None,
) -> Path:
    index = root / "INDEX.md"
    if not index.exists():
        index.write_text(
            heading
            or f"# Study `{payload['study_id']}`\n\n## Decision log\n\n",
            encoding="utf-8",
        )
    rel = md_path.name if md_path.parent.name == "decisions" else str(md_path)
    run_bit = f" · run=`{payload['run_id']}`" if payload.get("run_id") else ""
    line = (
        f"- `{payload['created_at'][:19]}` · **{payload['stage']}**{run_bit} · "
        f"{(payload.get('summary') or '')[:120]} · [{md_path.name}](decisions/{rel})\n"
    )
    with index.open("a", encoding="utf-8") as f:
        f.write(line)
    return index


def list_decisions(studies_dir: Path, study_id: str) -> list[dict[str, Any]]:
    sid = sanitize_study_id(study_id)
    dec_dir = Path(studies_dir) / sid / "decisions"
    if not dec_dir.exists():
        return []
    rows = []
    for p in sorted(dec_dir.glob("*.json")):
        rows.append(json.loads(p.read_text(encoding="utf-8")))
    return rows


def list_run_decisions(run_dir: Path) -> list[dict[str, Any]]:
    dec_dir = Path(run_dir) / "decisions"
    if not dec_dir.exists():
        return []
    rows = []
    for p in sorted(dec_dir.glob("*.json")):
        rows.append(json.loads(p.read_text(encoding="utf-8")))
    return rows


def load_decisions_for_report(
    run_dir: Path,
    *,
    studies_dir: Path | None = None,
    study_id: str | None = None,
) -> list[dict[str, Any]]:
    """Prefer full study trail when study_id known; else decisions mirrored on the run."""
    run_dir = Path(run_dir)
    sid = study_id
    if not sid:
        meta_path = run_dir / "meta.json"
        if meta_path.exists():
            sid = json.loads(meta_path.read_text(encoding="utf-8")).get("study_id")
    if sid and studies_dir:
        rows = list_decisions(studies_dir, sid)
        if rows:
            return rows
    return list_run_decisions(run_dir)
