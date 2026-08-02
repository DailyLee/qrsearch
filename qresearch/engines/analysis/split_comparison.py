"""Train / validate / holdout / stress / full-sample metrics side-by-side."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qresearch.engines.experiment.decision_log import list_decisions

# study decision stage → report role label
_STAGE_TO_ROLE = {
    "backtest_train": "train",
    "backtest_validate": "validate",
    "holdout": "holdout",
    "holdout_stress": "holdout_stress",
    "full_sample": "full",
}

_ROLE_ORDER = ("train", "validate", "holdout", "holdout_stress", "full")

_ROLE_LABEL_ZH = {
    "train": "训练年",
    "validate": "验证（冻结）",
    "holdout": "Holdout 终测",
    "holdout_stress": "Holdout 压力",
    "full": "全样本（仅披露）",
}


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def latest_run_ids_from_decisions(decisions: list[dict[str, Any]]) -> dict[str, str]:
    """Latest run_id per role from study decisions (by created_at)."""
    best: dict[str, tuple[str, str]] = {}  # role -> (created_at, run_id)
    for d in decisions:
        stage = str(d.get("stage") or "")
        role = _STAGE_TO_ROLE.get(stage)
        run_id = d.get("run_id")
        if not role or not run_id:
            continue
        ts = str(d.get("created_at") or "")
        prev = best.get(role)
        if prev is None or ts >= prev[0]:
            best[role] = (ts, str(run_id))
    return {role: rid for role, (_ts, rid) in best.items()}


def _years_label_from_profile(profile: dict[str, Any] | None) -> str:
    """e.g. '2019-2024' or '2025' from sample_profile.years / entry_min-max."""
    if not profile:
        return ""
    years_map = profile.get("years") or {}
    if isinstance(years_map, dict) and years_map:
        ys = sorted(int(y) for y in years_map.keys() if str(y).isdigit())
        if not ys:
            return ""
        if ys[0] == ys[-1]:
            return str(ys[0])
        return f"{ys[0]}-{ys[-1]}"
    emin = str(profile.get("entry_min") or "")[:4]
    emax = str(profile.get("entry_max") or "")[:4]
    if emin.isdigit() and emax.isdigit():
        if emin == emax:
            return emin
        return f"{emin}-{emax}"
    return ""


def _metrics_from_run(run_dir: Path) -> dict[str, Any]:
    meta = _load_json(run_dir / "meta.json") or {}
    metrics = _load_json(run_dir / "artifacts" / "metrics.json") or {}
    if not metrics and isinstance(meta.get("metrics"), dict):
        metrics = dict(meta["metrics"])
    profile = _load_json(run_dir / "artifacts" / "sample_profile.json") or {}
    gates = meta.get("gates") if isinstance(meta.get("gates"), dict) else {}
    years_label = _years_label_from_profile(profile)
    years_map = profile.get("years") if isinstance(profile.get("years"), dict) else {}
    return {
        "n_events": meta.get("n_events") or profile.get("n_events"),
        "n_trades": metrics.get("n_trades"),
        "total_return": metrics.get("total_return"),
        "ann_return": metrics.get("ann_return"),
        "sharpe": metrics.get("sharpe"),
        "max_dd": metrics.get("max_dd"),
        "excess_return": metrics.get("excess_return"),
        "ann_excess": metrics.get("ann_excess"),
        "information_ratio": metrics.get("information_ratio"),
        "mean_invested": metrics.get("mean_invested"),
        "empty_cash_share": metrics.get("empty_cash_share"),
        "deflated_sharpe": metrics.get("deflated_sharpe"),
        "end_nav": metrics.get("end_nav"),
        "promotable": meta.get("promotable"),
        "structural_passed": gates.get("structural_passed"),
        "economic_passed": gates.get("economic_passed"),
        "absolute_ok": gates.get("absolute_ok"),
        "excess_ok": gates.get("excess_ok"),
        "years_label": years_label,
        "years": {str(k): int(v) for k, v in years_map.items()} if years_map else {},
        "entry_min": profile.get("entry_min"),
        "entry_max": profile.get("entry_max"),
    }


def build_split_comparison(
    *,
    runs_dir: Path,
    studies_dir: Path | None = None,
    study_id: str | None = None,
    decisions: list[dict[str, Any]] | None = None,
    train_run: str | None = None,
    validate_run: str | None = None,
    holdout_run: str | None = None,
    holdout_stress_run: str | None = None,
    full_run: str | None = None,
) -> dict[str, Any] | None:
    """Build comparison table; None if fewer than 2 splits resolve."""
    runs_dir = Path(runs_dir)
    resolved: dict[str, str] = {}
    if decisions is None and study_id and studies_dir:
        decisions = list_decisions(studies_dir, study_id)
    if decisions:
        resolved.update(latest_run_ids_from_decisions(decisions))
    if train_run:
        resolved["train"] = train_run
    if validate_run:
        resolved["validate"] = validate_run
    if holdout_run:
        resolved["holdout"] = holdout_run
    if holdout_stress_run:
        resolved["holdout_stress"] = holdout_stress_run
    if full_run:
        resolved["full"] = full_run

    # Only include optional roles if present (or always show core three)
    roles = []
    for role in _ROLE_ORDER:
        if role in ("validate", "holdout_stress") and role not in resolved:
            continue
        roles.append(role)
    # Ensure at least train/holdout/full slots when any override exists
    if not roles:
        roles = list(_ROLE_ORDER)

    rows: list[dict[str, Any]] = []
    for role in roles:
        rid = resolved.get(role)
        label = _ROLE_LABEL_ZH[role]
        if not rid:
            rows.append(
                {
                    "role": role,
                    "label": label,
                    "years_label": "",
                    "run_id": None,
                    "missing": True,
                    "report_zh": "",
                    "metrics": {},
                }
            )
            continue
        root = runs_dir / rid
        if not root.exists():
            rows.append(
                {
                    "role": role,
                    "label": label,
                    "years_label": "",
                    "run_id": rid,
                    "missing": True,
                    "report_zh": "",
                    "metrics": {},
                    "error": "run_dir_missing",
                }
            )
            continue
        zh = root / "report" / "research_report_zh.html"
        m = _metrics_from_run(root)
        rows.append(
            {
                "role": role,
                "label": label,
                "years_label": m.get("years_label") or "",
                "run_id": rid,
                "missing": False,
                "report_zh": str(zh) if zh.exists() else "",
                "metrics": m,
            }
        )

    present = [r for r in rows if not r.get("missing")]
    if len(present) < 2:
        return None
    return {
        "study_id": study_id,
        "rows": rows,
        "n_present": len(present),
        "note": (
            "训练年用于调参；验证窗只冻结不搜参；Holdout 终测只评估一次；"
            "Holdout 压力标明角色（差≠机械否决）；全样本仅披露，不能代替 Holdout、不可单独 promote。"
        ),
    }
