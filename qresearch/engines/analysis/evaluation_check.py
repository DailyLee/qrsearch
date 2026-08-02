"""Light evaluation.years vs sample_profile.years consistency (warn only)."""

from __future__ import annotations

from typing import Any

from qresearch.config.models import EvaluationConfig, ResearchConfig


def _years_from_profile(profile: dict[str, Any] | None) -> set[str]:
    if not profile:
        return set()
    years_map = profile.get("years") or {}
    if isinstance(years_map, dict):
        return {str(k) for k in years_map.keys() if str(k).isdigit()}
    return set()


def declared_years(evaluation: EvaluationConfig) -> set[str]:
    out: set[str] = set()
    for y in evaluation.train_years or []:
        out.add(str(y))
    for y in evaluation.validate_years or []:
        out.add(str(y))
    for h in evaluation.holdouts or []:
        for y in h.years or []:
            out.add(str(y))
    return out


def check_evaluation_years(
    config: ResearchConfig,
    sample_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return warn payload if sample years fall outside declared evaluation windows."""
    ev = config.evaluation
    declared = declared_years(ev)
    if not declared:
        return {"status": "skipped", "reason": "evaluation_years_empty"}
    actual = _years_from_profile(sample_profile)
    if not actual:
        return {"status": "skipped", "reason": "sample_years_empty"}
    unexpected = sorted(actual - declared)
    if unexpected:
        return {
            "status": "warn",
            "unexpected_years": unexpected,
            "declared_years": sorted(declared),
            "sample_years": sorted(actual),
            "message": "sample_profile.years not subset of evaluation.* years",
        }
    return {
        "status": "ok",
        "declared_years": sorted(declared),
        "sample_years": sorted(actual),
    }
