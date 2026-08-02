"""Unified best_params patches schema + YAML apply."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml


class ApplyBestError(ValueError):
    """Invalid apply-best request or run artifacts."""


def optimize_params_to_patches(best: dict[str, Any], *, metric: str = "sharpe", best_value: Any = None) -> dict[str, Any]:
    """Convert signal_threshold_search best_params into unified patches."""
    feat = best.get("feature")
    if not feat:
        raise ApplyBestError("optimize best_params missing feature")
    op = best.get("op")
    thr = best.get("threshold")
    if op is None or thr is None:
        raise ApplyBestError("optimize best_params missing op/threshold")
    return {
        "patches": [
            {
                "path": "signals.filters",
                "match": {"field": feat},
                "set": {"op": op, "value": thr},
            }
        ],
        "source": "pipeline.optimize",
        "metric": metric,
        "best_value": best_value,
        "legacy": dict(best),
    }


def sensitivity_row_to_patches(row: dict[str, Any], *, metric: str = "sharpe") -> dict[str, Any]:
    """Build patches from a sensitivity grid best row."""
    patches: list[dict[str, Any]] = []
    mapping = [
        ("cost_mult", None),  # costs scaled — not a single path; skip unless present as absolute
        ("stop_loss", "risk.stop_loss"),
        ("take_profit", "risk.take_profit"),
        ("max_hold_sessions", "risk.max_hold_sessions"),
        ("max_weight", "portfolio.max_weight"),
        ("max_new_entries_per_day", "portfolio.max_new_entries_per_day"),
        ("sizing_base", "portfolio.sizing_base"),
        ("max_names_per_industry", "portfolio.max_names_per_industry"),
        ("max_new_per_industry_per_day", "portfolio.max_new_per_industry_per_day"),
    ]
    for key, path in mapping:
        if path is None:
            continue
        if key not in row:
            continue
        patches.append({"path": path, "value": row[key]})
    # absolute cost rates if present
    for key, path in [
        ("commission_rate", "costs.commission_rate"),
        ("stamp_duty_rate", "costs.stamp_duty_rate"),
        ("transfer_fee_rate", "costs.transfer_fee_rate"),
        ("slippage_bps", "costs.slippage_bps"),
    ]:
        if key in row:
            patches.append({"path": path, "value": row[key]})
    return {
        "patches": patches,
        "source": "pipeline.sensitivity",
        "metric": metric,
        "best_value": row.get("sharpe"),
        "legacy": {k: row[k] for k in row if k != "patches"},
    }


def normalize_best_params(payload: dict[str, Any]) -> dict[str, Any]:
    """Ensure payload has patches list (convert optimize legacy if needed)."""
    if not payload:
        raise ApplyBestError("empty best_params")
    if payload.get("patches"):
        return payload
    # optimize legacy at top level
    if "feature" in payload and "threshold" in payload:
        return optimize_params_to_patches(
            payload,
            metric=str(payload.get("metric") or "sharpe"),
            best_value=payload.get("best_value"),
        )
    raise ApplyBestError("best_params missing patches and not optimize legacy")


def load_best_params_from_run(run_dir: Path) -> dict[str, Any]:
    """Prefer sweep → signal_threshold → sensitivity summaries."""
    run_dir = Path(run_dir)
    candidates = [
        ("artifacts/sweep_summary.json", "pipeline.sweep"),
        ("artifacts/signal_threshold_trials.json", "pipeline.optimize"),
        ("artifacts/optuna_trials.json", "pipeline.optimize"),
        ("artifacts/sensitivity_summary.json", "pipeline.sensitivity"),
    ]
    for rel, source in candidates:
        path = run_dir / rel
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("best_params_unified") and isinstance(data["best_params_unified"], dict):
            bp = data["best_params_unified"]
            return {**bp, "source": bp.get("source") or source}
        if "best_params" in data and data["best_params"] is not None:
            bp = data["best_params"]
            if isinstance(bp, dict) and bp.get("patches"):
                # optimize may embed patches alongside legacy keys
                if bp.get("feature") and bp.get("threshold") is not None:
                    return optimize_params_to_patches(
                        bp,
                        metric=str(data.get("metric") or "sharpe"),
                        best_value=data.get("best_value"),
                    )
                return {**bp, "source": bp.get("source") or source}
            if isinstance(bp, dict) and "feature" in bp:
                return optimize_params_to_patches(
                    bp,
                    metric=str(data.get("metric") or "sharpe"),
                    best_value=data.get("best_value"),
                )
        if source == "pipeline.sensitivity" and data.get("best_by_sharpe"):
            out = sensitivity_row_to_patches(data["best_by_sharpe"])
            if data.get("best_params") and isinstance(data["best_params"], dict):
                # prefer explicit if present without patches handled above
                pass
            if out["patches"]:
                return out
        if data.get("best_params") and source == "pipeline.sensitivity":
            return normalize_best_params(data["best_params"])
    raise ApplyBestError(f"no best_params found under {run_dir}")


def _set_dotted(cfg: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Any = cfg
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def apply_patches(config_dict: dict[str, Any], patches: list[dict[str, Any]]) -> dict[str, Any]:
    """Deep-copy config and apply unified patches."""
    out = copy.deepcopy(config_dict)
    for patch in patches:
        path = patch.get("path")
        if not path:
            continue
        if path == "signals.filters":
            match = patch.get("match") or {}
            field = match.get("field")
            sets = patch.get("set") or {}
            filters = list((out.get("signals") or {}).get("filters") or [])
            idx = next((i for i, f in enumerate(filters) if f.get("field") == field), None)
            if idx is None:
                # insert new filter
                filters.append({"field": field, **sets})
            else:
                filters[idx] = {**filters[idx], **sets}
            out.setdefault("signals", {})["filters"] = filters
            continue
        if "value" in patch:
            _set_dotted(out, str(path), patch["value"])
            continue
        if "set" in patch and isinstance(patch["set"], dict) and "." in str(path):
            for k, v in patch["set"].items():
                _set_dotted(out, f"{path}.{k}", v)
    return out


def apply_best_to_yaml(
    *,
    run_dir: Path,
    out_path: Path,
    examples_dir: Path | None = None,
) -> dict[str, Any]:
    """Write patched YAML from run snapshot + best_params. Never overwrite examples/."""
    out_path = Path(out_path)
    examples_dir = Path(examples_dir) if examples_dir else Path("configs/examples")
    try:
        out_resolved = out_path.resolve()
        ex_resolved = examples_dir.resolve()
        if out_resolved == ex_resolved or ex_resolved in out_resolved.parents:
            raise ApplyBestError(f"refusing to write under configs/examples: {out_path}")
    except ApplyBestError:
        raise
    except OSError:
        # path may not exist yet — check string prefix
        parts = out_path.as_posix().replace("\\", "/")
        if "/configs/examples/" in f"/{parts}" or parts.startswith("configs/examples/"):
            raise ApplyBestError(f"refusing to write under configs/examples: {out_path}")

    if out_path.exists():
        raise ApplyBestError(f"output already exists (choose a new name): {out_path}")

    snap = Path(run_dir) / "config.snapshot.yaml"
    if not snap.exists():
        raise ApplyBestError(f"missing config.snapshot.yaml in {run_dir}")
    base = yaml.safe_load(snap.read_text(encoding="utf-8")) or {}
    bp = normalize_best_params(load_best_params_from_run(run_dir))
    patched = apply_patches(base, list(bp.get("patches") or []))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        yaml.safe_dump(patched, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return {
        "out": str(out_path),
        "source": bp.get("source"),
        "n_patches": len(bp.get("patches") or []),
        "best_params": bp,
    }
