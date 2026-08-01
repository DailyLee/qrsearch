"""Point-in-time / data audit checklist (disclosure + hard checks)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

import polars as pl

from qresearch.config.models import ResearchConfig
from qresearch.engines.data.panel import PricePanel, _cache_key, derive_panel_range


Status = Literal["pass", "warn", "fail"]


def _as_date(v: object) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def run_pit_audit(
    events: pl.DataFrame,
    panel: PricePanel,
    config: ResearchConfig,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """
    Build pit_audit payload.

    Current adjustment implementation is `qfq_window_end` (vendor uses last
    adj_factor in the loaded window). Full per-session adj_factor PIT is not implemented.
    """
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    failures: list[str] = []

    start, end = derive_panel_range(events, config)
    adj_as_of = panel.adjustment_as_of or config.adjustment.as_of or end.strftime("%Y%m%d")
    cache_key = _cache_key(
        sorted(panel.instruments),
        panel.start,
        panel.end,
        config.adjustment.mode,
        adj_as_of,
    )

    checks.append(
        {
            "id": "adjustment_methodology",
            "status": "warn",
            "detail": {
                "implemented": "qfq_window_end",
                "not_implemented": "adj_factor_asof_session",
                "mode": config.adjustment.mode,
                "as_of": adj_as_of,
                "cache_key": cache_key,
                "message": "前复权以研究窗口终点 adj_factor 为基准，不是逐日历史可得因子的完整 PIT。",
            },
        }
    )
    warnings.append("adjustment_is_qfq_window_end_not_full_pit")

    checks.append(
        {
            "id": "data_fingerprint",
            "status": "pass" if panel.data_fingerprint else "warn",
            "detail": {"fingerprint": panel.data_fingerprint},
        }
    )

    # decision_date <= entry_intent_date
    bad_decision = 0
    if "decision_date" in events.columns and "entry_intent_date" in events.columns:
        for r in events.select(["decision_date", "entry_intent_date"]).iter_rows(named=True):
            if _as_date(r["decision_date"]) > _as_date(r["entry_intent_date"]):
                bad_decision += 1
    if bad_decision:
        failures.append(f"decision_after_entry:{bad_decision}")
        checks.append(
            {
                "id": "decision_before_entry",
                "status": "fail",
                "detail": {"n_violations": bad_decision},
            }
        )
    else:
        checks.append(
            {
                "id": "decision_before_entry",
                "status": "pass",
                "detail": {"n_violations": 0},
            }
        )

    # IC / forward return start: entry_intent_date (no implicit +1) — disclose
    checks.append(
        {
            "id": "forward_return_anchor",
            "status": "pass",
            "detail": {
                "entry_semantics": "entry_intent_date_is_planned_entry_no_implicit_lag",
                "lag_sessions": config.execution.lag_sessions,
            },
        }
    )

    # lookback before decision: panel start may precede first decision — expected for bars
    if events.height:
        first_decision = _as_date(events["decision_date"].min())
        if panel.start < first_decision:
            warnings.append("panel_starts_before_first_decision_for_lookback")
            checks.append(
                {
                    "id": "lookback_before_decision",
                    "status": "warn",
                    "detail": {
                        "panel_start": str(panel.start),
                        "first_decision": str(first_decision),
                        "lookback_sessions": config.lookback_sessions,
                        "message": "面板起点早于首个决策日（用于回看/复权），属预期；特征若用未来信息需另行保证。",
                    },
                }
            )
        else:
            checks.append({"id": "lookback_before_decision", "status": "pass", "detail": {}})

    bench = config.benchmark.instrument
    bench_missing = False
    if bench:
        # any bar?
        n_bench = 0
        for d in panel.calendar[: min(20, len(panel.calendar))]:
            if panel.get(bench, d) is not None:
                n_bench += 1
        # also scan full if small
        if n_bench == 0:
            for d in panel.calendar:
                if panel.get(bench, d) is not None:
                    n_bench += 1
                    break
        if n_bench == 0:
            bench_missing = True
            warnings.append(f"benchmark_missing:{bench}")
            checks.append(
                {
                    "id": "benchmark_present",
                    "status": "fail" if strict else "warn",
                    "detail": {"instrument": bench, "present": False},
                }
            )
            if strict:
                failures.append(f"benchmark_missing:{bench}")
        else:
            checks.append(
                {
                    "id": "benchmark_present",
                    "status": "pass",
                    "detail": {"instrument": bench, "present": True},
                }
            )

    if failures:
        status: Status = "fail"
    elif warnings:
        status = "warn"
    else:
        status = "pass"

    return {
        "status": status,
        "strict": strict,
        "adjustment": {
            "mode": config.adjustment.mode,
            "as_of": adj_as_of,
            "methodology": "qfq_window_end",
            "full_pit_adj_factor_asof_session": False,
            "cache_key": cache_key,
        },
        "data_fingerprint": panel.data_fingerprint,
        "panel": {
            "start": str(panel.start),
            "end": str(panel.end),
            "n_instruments": len(panel.instruments),
            "n_sessions": len(panel.calendar),
        },
        "benchmark": {"instrument": bench, "missing": bench_missing},
        "checks": checks,
        "warnings": warnings,
        "failures": failures,
    }
