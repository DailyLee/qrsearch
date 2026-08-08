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
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    if meta.get("sample_kind") == "market":
        required = {
            "feature_snapshot_sha256": meta.get("feature_snapshot_sha256"),
            "zer0share_fingerprint": meta.get("zer0share_fingerprint"),
            "zer0factor_revision": meta.get("zer0factor_revision"),
        }
        missing = [name for name, value in required.items() if not value]
        artifacts = run_dir / "artifacts"
        for name in ("feature_manifest.json", "split_summary.json", "factor_screening_manifest.json"):
            if not (artifacts / name).is_file():
                missing.append(name)
        if missing:
            raise PermissionError("market lineage is incomplete: " + ", ".join(missing))
        if meta.get("st_filter_status") != "full":
            raise PermissionError(
                "market ST filter is not full: "
                + str(meta.get("st_filter_status") or "unknown")
            )
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
    if meta.get("sample_kind") == "market":
        evidence_dir = dest / "evidence"
        evidence_dir.mkdir(exist_ok=True)
        for name in ("feature_manifest.json", "split_summary.json", "factor_screening_manifest.json"):
            shutil.copy2(run_dir / "artifacts" / name, evidence_dir / name)
        screening = run_dir / "artifacts" / "zer0factor_evaluation"
        if screening.is_dir():
            shutil.copytree(screening, evidence_dir / "zer0factor_evaluation")

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
        "sample_kind": meta.get("sample_kind"),
        "universe": meta.get("universe"),
        "feature_snapshot_sha256": meta.get("feature_snapshot_sha256"),
        "zer0share_fingerprint": meta.get("zer0share_fingerprint"),
        "zer0factor_revision": meta.get("zer0factor_revision"),
        "st_filter_status": meta.get("st_filter_status"),
    }
    (dest / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dest
