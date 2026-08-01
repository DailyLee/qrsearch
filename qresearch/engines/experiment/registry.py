"""Immutable run registry."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from qresearch import __version__


def new_run_id() -> str:
    """Local time YYYYMMDD_HHMMSS_mmm so folders sort and read with clock time."""
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")[:-3]


class RunWriter:
    def __init__(self, runs_dir: Path, run_id: str | None = None):
        self.run_id = run_id or new_run_id()
        self.root = Path(runs_dir) / self.run_id
        self.artifacts = self.root / "artifacts"
        self.report = self.root / "report"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.report.mkdir(parents=True, exist_ok=True)

    def write_meta(self, meta: dict[str, Any]) -> Path:
        payload = {
            "run_id": self.run_id,
            "qresearch_version": __version__,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **meta,
        }
        path = self.root / "meta.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_config_snapshot(self, config: Any) -> Path:
        path = self.root / "config.snapshot.yaml"
        data = config.model_dump() if hasattr(config, "model_dump") else config
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path

    def write_json(self, relative: str, data: Any) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path

    def write_text(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def artifact_path(self, name: str) -> Path:
        return self.artifacts / name


def list_runs(runs_dir: Path) -> list[dict[str, Any]]:
    runs_dir = Path(runs_dir)
    if not runs_dir.exists():
        return []
    rows = []
    for p in sorted(runs_dir.iterdir(), reverse=True):
        meta_path = p / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        rows.append(meta)
    return rows


def load_run_meta(runs_dir: Path, run_id: str) -> dict[str, Any]:
    path = Path(runs_dir) / run_id / "meta.json"
    if not path.exists():
        raise FileNotFoundError(f"run not found: {run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def archive_run(runs_dir: Path, run_id: str, out: Path) -> Path:
    src = Path(runs_dir) / run_id
    if not src.exists():
        raise FileNotFoundError(run_id)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    archive = shutil.make_archive(str(out.with_suffix("")), "zip", root_dir=src)
    return Path(archive)
