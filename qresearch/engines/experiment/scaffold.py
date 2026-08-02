"""Scaffold experiment YAML from configs/examples (never write examples/)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from qresearch.config.models import ResearchConfig


class ScaffoldError(ValueError):
    """Invalid config.new scaffold request."""


def _posix(p: Path) -> str:
    return p.as_posix().replace("\\", "/")


def _under_dir(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        pp = _posix(path)
        rp = _posix(root)
        return pp == rp or pp.startswith(rp.rstrip("/") + "/")


def assert_out_under_experiments(out_path: Path, experiments_dir: Path) -> None:
    if not _under_dir(out_path, experiments_dir):
        raise ScaffoldError(f"refusing to write outside configs/experiments: {out_path}")


def assert_not_under_examples(out_path: Path, examples_dir: Path) -> None:
    if _under_dir(out_path, examples_dir):
        raise ScaffoldError(f"refusing to write under configs/examples: {out_path}")


def assert_from_under_examples(from_path: Path, examples_dir: Path) -> None:
    if not from_path.exists():
        raise ScaffoldError(f"template not found: {from_path}")
    if not _under_dir(from_path, examples_dir):
        raise ScaffoldError(
            f"template must be under configs/examples (got {from_path}); "
            "do not scaffold from experiments/"
        )


def parse_set_item(item: str) -> tuple[str, Any]:
    if "=" not in item:
        raise ScaffoldError(f"invalid --set {item!r}; expected key=value")
    key, raw = item.split("=", 1)
    key = key.strip()
    if not key:
        raise ScaffoldError(f"invalid --set {item!r}; empty key")
    try:
        value = yaml.safe_load(raw)
    except Exception as e:
        raise ScaffoldError(f"invalid --set value for {key!r}: {raw!r}") from e
    return key, value


def _set_dotted(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Any = data
    for p in parts[:-1]:
        if not isinstance(cur, dict):
            raise ScaffoldError(f"cannot set {path!r}: parent is not a mapping")
        nxt = cur.get(p)
        if nxt is None:
            cur[p] = {}
            nxt = cur[p]
        elif not isinstance(nxt, dict):
            raise ScaffoldError(f"cannot set {path!r}: {p!r} is not a mapping")
        cur = nxt
    if not isinstance(cur, dict):
        raise ScaffoldError(f"cannot set {path!r}: parent is not a mapping")
    cur[parts[-1]] = value


def clear_signals(data: dict[str, Any]) -> None:
    sig = data.get("signals")
    if not isinstance(sig, dict):
        data["signals"] = {"filters": [], "rank_by": []}
        return
    sig["filters"] = []
    sig["rank_by"] = []
    comp = sig.get("composite")
    if isinstance(comp, dict):
        comp["enabled"] = False
        if "components" in comp:
            comp["components"] = []


def ensure_evaluation(data: dict[str, Any]) -> bool:
    """Inject empty evaluation skeleton if missing. Returns whether injected."""
    if isinstance(data.get("evaluation"), dict):
        return False
    data["evaluation"] = {
        "primary_metric": "absolute",
        "train_years": [],
        "validate_years": [],
        "holdouts": [],
        "statement_hint": "",
    }
    return True


def scaffold_experiment_yaml(
    *,
    from_path: Path,
    out_path: Path,
    study_id: str,
    sets: list[str] | None = None,
    examples_dir: Path | None = None,
    experiments_dir: Path | None = None,
) -> dict[str, Any]:
    """Copy example scaffold to experiments with empty signals + study_id."""
    from_path = Path(from_path)
    out_path = Path(out_path)
    examples_dir = Path(examples_dir) if examples_dir else Path("configs/examples")
    experiments_dir = Path(experiments_dir) if experiments_dir else Path("configs/experiments")
    study_id = str(study_id).strip()
    if not study_id:
        raise ScaffoldError("study_id is required")

    assert_from_under_examples(from_path, examples_dir)
    assert_not_under_examples(out_path, examples_dir)
    assert_out_under_experiments(out_path, experiments_dir)

    if out_path.exists():
        raise ScaffoldError(f"output already exists (choose a new name): {out_path}")

    raw = yaml.safe_load(from_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ScaffoldError(f"template root must be a mapping: {from_path}")
    data = copy.deepcopy(raw)

    evaluation_injected = ensure_evaluation(data)

    sets_applied: list[dict[str, Any]] = []
    for item in sets or []:
        key, value = parse_set_item(item)
        _set_dotted(data, key, value)
        sets_applied.append({"path": key, "value": value})

    # Always last: never allow non-empty signals from template or --set
    clear_signals(data)

    hyp = data.get("hypothesis")
    if not isinstance(hyp, dict):
        hyp = {}
        data["hypothesis"] = hyp
    hyp["study_id"] = study_id
    if not hyp.get("id"):
        hyp["id"] = study_id

    try:
        ResearchConfig.model_validate(data)
    except Exception as e:
        raise ScaffoldError(f"scaffolded config failed validation: {e}") from e

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return {
        "out": str(out_path),
        "from": str(from_path),
        "study_id": study_id,
        "signals_cleared": True,
        "evaluation_injected": evaluation_injected,
        "sets_applied": sets_applied,
    }
