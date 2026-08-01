"""Promote a run to ModelPackage."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def promote_run(
    runs_dir: Path,
    packages_dir: Path,
    run_id: str,
    model_id: str,
    version: str,
    *,
    force: bool = False,
) -> Path:
    run_dir = Path(runs_dir) / run_id
    conclusion_path = run_dir / "report" / "conclusion.json"
    if not conclusion_path.exists():
        raise FileNotFoundError(f"missing conclusion: {conclusion_path}")
    conclusion = json.loads(conclusion_path.read_text(encoding="utf-8"))
    promotable = bool(conclusion.get("promotable"))
    if not promotable and not force:
        raise PermissionError("run is not promotable; pass force=True to override")

    dest = Path(packages_dir) / model_id / version
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    # copy snapshot + conclusion
    cfg = run_dir / "config.snapshot.yaml"
    if cfg.exists():
        shutil.copy2(cfg, dest / "spec.yaml")
    shutil.copy2(conclusion_path, dest / "report_conclusion.json")
    html = run_dir / "report" / "conclusion.html"
    if html.exists():
        shutil.copy2(html, dest / "conclusion.html")

    metrics = conclusion.get("metrics") or {}
    (dest / "metrics_oos.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    provenance = {
        "run_id": run_id,
        "model_id": model_id,
        "version": version,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "forced": bool(force),
        "promotable": promotable,
        "gates": conclusion.get("gates"),
    }
    (dest / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dest
