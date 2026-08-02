"""Multi-knob signal filter grid (Cartesian product, max_grid capped)."""

from __future__ import annotations

import itertools
import re
from typing import Any

from qresearch.config.models import FilterRule, ResearchConfig
from qresearch.engines.data.panel import PricePanel
from qresearch.engines.experiment.optimize import OptimizeError, _score_config
from qresearch.engines.signal.engine import build_ranked

_SET_RE = re.compile(
    r"^signals\.filters\[field=(?P<field>[^\]]+)\]\.(?P<attr>value|op|between)=(?P<body>.+)$"
)


class SweepError(OptimizeError):
    """Invalid sweep --set specs."""


def parse_set_spec(spec: str) -> dict[str, Any]:
    """Parse one --set string into {field, attr, values: list}."""
    m = _SET_RE.match(str(spec).strip())
    if not m:
        raise SweepError(
            "invalid --set; expected signals.filters[field=<col>].value=v1,v2 "
            f"or .op=... or .between=lo:hi,...; got {spec!r}"
        )
    field = m.group("field").strip()
    attr = m.group("attr")
    body = m.group("body").strip()
    parts = [p.strip() for p in body.split(",") if p.strip() != ""]
    if not parts:
        raise SweepError(f"empty values in --set {spec!r}")
    if attr == "op":
        values: list[Any] = parts
    elif attr == "between":
        values = []
        for p in parts:
            if ":" not in p:
                raise SweepError(f"between pair must be lo:hi; got {p!r} in {spec!r}")
            a, b = p.split(":", 1)
            lo, hi = float(a.strip()), float(b.strip())
            if lo >= hi:
                raise SweepError(f"between requires lo < hi; got {lo}:{hi}")
            values.append((lo, hi))
    else:
        values = [float(p) for p in parts]
    return {"field": field, "attr": attr, "values": values}


def parse_set_specs(specs: list[str]) -> list[dict[str, Any]]:
    return [parse_set_spec(s) for s in specs]


def _apply_assignment(cfg: ResearchConfig, field: str, attr: str, value: Any) -> None:
    filters = list(cfg.signals.filters or [])
    idx = next((i for i, f in enumerate(filters) if f.field == field), None)
    if idx is None:
        raise SweepError(f"filter field not in config: {field}")
    f = filters[idx]
    data = f.model_dump()
    if attr == "value":
        data["value"] = value
    elif attr == "between":
        lo, hi = value
        data["op"] = "between"
        data["value"] = lo
        data["value_max"] = hi
    else:
        data["op"] = value
    filters[idx] = FilterRule.model_validate(data)
    cfg.signals.filters = filters


def _patch_for(field: str, attr: str, value: Any) -> dict[str, Any]:
    if attr == "between":
        lo, hi = value
        return {
            "path": "signals.filters",
            "match": {"field": field},
            "set": {"op": "between", "value": lo, "value_max": hi},
        }
    if attr == "value":
        return {
            "path": "signals.filters",
            "match": {"field": field},
            "set": {"value": value},
        }
    return {
        "path": "signals.filters",
        "match": {"field": field},
        "set": {"op": value},
    }


def run_signal_sweep(
    events,
    panel: PricePanel,
    base_config: ResearchConfig,
    *,
    set_specs: list[str],
    metric: str = "sharpe",
    max_grid: int = 64,
) -> dict[str, Any]:
    dims = parse_set_specs(set_specs)
    if not dims:
        raise SweepError("at least one --set required")

    for d in dims:
        if not any(f.field == d["field"] for f in (base_config.signals.filters or [])):
            raise SweepError(f"filter field not in config: {d['field']}")

    axes = [d["values"] for d in dims]
    combos = list(itertools.product(*axes))
    truncated = False
    if len(combos) > max_grid:
        combos = combos[:max_grid]
        truncated = True

    rows: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None

    for i, combo in enumerate(combos):
        cfg = base_config.model_copy(deep=True)
        patches: list[dict[str, Any]] = []
        flat: dict[str, Any] = {}
        for d, val in zip(dims, combo):
            _apply_assignment(cfg, d["field"], d["attr"], val)
            if d["attr"] == "between":
                lo, hi = val
                flat[f"{d['field']}.between_lo"] = lo
                flat[f"{d['field']}.between_hi"] = hi
            else:
                flat[f"{d['field']}.{d['attr']}"] = val
            patches.append(_patch_for(d["field"], d["attr"], val))
        ranked = build_ranked(events, cfg)
        n_events_kept = int(ranked.height)
        score, n_trades, mode = _score_config(events, panel, cfg)
        value = score if metric == "sharpe" else score
        row = {
            "number": i,
            **flat,
            "metric": metric,
            "value": value,
            "n_trades": n_trades,
            "n_events_kept": n_events_kept,
            "mode": mode,
            "patches": patches,
            "assignments": dict(flat),
        }
        rows.append(row)
        if best is None or float(value) > float(best["value"]):
            best = row

    assert best is not None
    best_params = {
        "patches": best["patches"],
        "source": "pipeline.sweep",
        "metric": metric,
        "best_value": best["value"],
        "assignments": dict(best["assignments"]),
    }
    return {
        "n_grid": len(rows),
        "truncated": truncated,
        "metric": metric,
        "rows": rows,
        "best_params": best_params,
        "best_value": best["value"],
        "method": "signal_sweep",
    }
