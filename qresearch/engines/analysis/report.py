"""Chinese research report (HTML/JSON) from run artifacts."""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl
import yaml
from jinja2 import Environment, FileSystemLoader, Undefined, select_autoescape

from qresearch.config.models import GatesConfig, ResearchConfig

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def evaluate_gates(
    metrics: dict[str, Any],
    gates: GatesConfig,
    *,
    n_oos_folds: int = 0,
    pit_status: str | None = None,
) -> dict[str, Any]:
    """Split structural vs economic gates.

    - passed / structural_passed: research may continue
    - promotable: structural AND economic (when require_economic_for_promote)
    """
    structural_reasons: list[str] = []
    economic_reasons: list[str] = []

    if n_oos_folds < gates.min_oos_folds:
        structural_reasons.append(f"oos_folds<{gates.min_oos_folds}")
    if int(metrics.get("n_trades") or 0) < gates.min_trades:
        structural_reasons.append(f"trades<{gates.min_trades}")
    if gates.max_n_trials is not None and int(metrics.get("n_trials") or 1) > int(gates.max_n_trials):
        structural_reasons.append("n_trials_above_max")
    if gates.pit_strict and pit_status == "fail":
        structural_reasons.append("pit_audit_fail")

    if gates.min_oos_sharpe is not None and float(metrics.get("sharpe") or 0) < gates.min_oos_sharpe:
        economic_reasons.append("sharpe_below_min")
    if gates.max_oos_drawdown is not None and abs(float(metrics.get("max_dd") or 0)) > abs(
        gates.max_oos_drawdown
    ):
        economic_reasons.append("drawdown_above_max")
    if gates.min_deflated_sharpe is not None:
        dsr = metrics.get("deflated_sharpe")
        if dsr is None or float(dsr) < float(gates.min_deflated_sharpe):
            economic_reasons.append("deflated_sharpe_below_min")

    structural_passed = len(structural_reasons) == 0
    economic_passed = len(economic_reasons) == 0
    reasons = structural_reasons + economic_reasons
    if gates.require_economic_for_promote:
        promotable = structural_passed and economic_passed
    else:
        promotable = structural_passed
    return {
        "structural_passed": structural_passed,
        "economic_passed": economic_passed,
        "passed": structural_passed,
        "promotable": promotable,
        "reasons": reasons,
        "structural_reasons": structural_reasons,
        "economic_reasons": economic_reasons,
    }


def build_conclusion(
    *,
    run_id: str,
    config: ResearchConfig,
    metrics: dict[str, Any],
    n_events: int,
    adjustment_as_of: str,
    gates_result: dict[str, Any],
    ic_rows: list[dict] | None = None,
    wf: dict | None = None,
    pit_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.4",
        "locale": "zh-CN",
        "title": "量化研究报告",
        "run_id": run_id,
        "study_id": config.hypothesis.study_id,
        "promotable": bool(gates_result.get("promotable")),
        "gates": gates_result,
        "gates_cfg": config.gates.model_dump(),
        "hypothesis": config.hypothesis.model_dump(),
        "n_events": n_events,
        "metrics": metrics,
        "signals": config.signals.model_dump(),
        "execution": config.execution.model_dump(),
        "portfolio": config.portfolio.model_dump(),
        "costs": config.costs.model_dump(),
        "risk": config.risk.model_dump(),
        "adjustment": config.adjustment.model_dump(),
        "benchmark": config.benchmark.model_dump(),
        "adjustment_as_of": adjustment_as_of,
        "ic": ic_rows or [],
        "walk_forward": wf,
        "pit_audit": pit_audit,
        "overfit": metrics.get("overfit"),
        "behavior_subset": [],
        "decisions": [],
    }


def _safe_float(v: Any) -> float | None:
    if v is None or isinstance(v, Undefined):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _pctile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    w = pos - lo
    return sorted_vals[lo] * (1 - w) + sorted_vals[hi] * w


def summarize_ic(ic_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for r in ic_rows:
        ic = _safe_float(r.get("rank_ic"))
        if ic is None:
            continue
        rows.append(
            {
                "feature": r.get("feature"),
                "horizon": int(r.get("horizon") or 0),
                "n": int(r.get("n") or 0),
                "rank_ic": ic,
            }
        )
    best = sorted(rows, key=lambda x: abs(x["rank_ic"]), reverse=True)[:20]
    by_feat: dict[str, list[float]] = {}
    for r in rows:
        by_feat.setdefault(str(r["feature"]), []).append(r["rank_ic"])
    feature_summary = []
    for feat, vals in by_feat.items():
        feature_summary.append(
            {
                "feature": feat,
                "mean_ic": sum(vals) / len(vals),
                "max_abs_ic": max(abs(v) for v in vals),
                "n_points": len(vals),
            }
        )
    feature_summary.sort(key=lambda x: x["max_abs_ic"], reverse=True)
    return {"ic_best": best, "ic_by_feature": feature_summary, "ic_table": rows}


def summarize_trades(trades: list[dict[str, Any]] | pl.DataFrame | None) -> dict[str, Any] | None:
    if trades is None:
        return None
    if isinstance(trades, pl.DataFrame):
        rows = trades.to_dicts()
    else:
        rows = list(trades)
    if not rows:
        return None

    sells = [r for r in rows if str(r.get("side")) == "sell"]
    pnls = [p for r in sells if (p := _safe_float(r.get("pnl"))) is not None]
    fees = [_safe_float(r.get("fee")) or 0.0 for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    exit_ctr = Counter(str(r.get("reason") or "unknown") for r in sells)
    n_sell = max(len(sells), 1)
    exit_reasons = [
        {"reason": k, "count": v, "share": v / n_sell}
        for k, v in exit_ctr.most_common()
    ]
    sorted_pnls = sorted(pnls)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    if math.isinf(profit_factor):
        profit_factor = 999.0
    return {
        "n_buys": sum(1 for r in rows if r.get("side") == "buy"),
        "n_sells": len(sells),
        "win_rate": (len(wins) / len(pnls)) if pnls else 0.0,
        "avg_pnl": (sum(pnls) / len(pnls)) if pnls else 0.0,
        "profit_factor": profit_factor,
        "total_fees": sum(fees),
        "exit_reasons": exit_reasons,
        "pnl_min": sorted_pnls[0] if sorted_pnls else None,
        "pnl_p25": _pctile(sorted_pnls, 0.25),
        "pnl_p50": _pctile(sorted_pnls, 0.50),
        "pnl_p75": _pctile(sorted_pnls, 0.75),
        "pnl_max": sorted_pnls[-1] if sorted_pnls else None,
    }


def summarize_rejects(rejects: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not rejects:
        return {"total": 0, "by_reason": []}
    ctr = Counter(str(r.get("reason") or "unknown") for r in rejects)
    return {
        "total": len(rejects),
        "by_reason": [{"reason": k, "count": v} for k, v in ctr.most_common()],
    }


def summarize_equity(equity: list[dict[str, Any]] | pl.DataFrame | None) -> dict[str, Any] | None:
    if equity is None:
        return None
    if isinstance(equity, pl.DataFrame):
        rows = equity.to_dicts()
    else:
        rows = list(equity)
    if not rows:
        return None
    navs = [_safe_float(r.get("nav")) or 0.0 for r in rows]
    positions = [int(r.get("n_positions") or 0) for r in rows]
    return {
        "start_session": rows[0].get("session"),
        "end_session": rows[-1].get("session"),
        "peak_nav": max(navs) if navs else None,
        "min_nav": min(navs) if navs else None,
        "max_positions": max(positions) if positions else 0,
        "n_points": len(rows),
    }


def _equity_rows(equity: list[dict[str, Any]] | pl.DataFrame | None) -> list[dict[str, Any]]:
    if equity is None:
        return []
    if isinstance(equity, pl.DataFrame):
        return equity.to_dicts()
    return list(equity)


def equity_to_svg(
    equity: list[dict[str, Any]] | pl.DataFrame | None,
    *,
    width: int = 960,
    height: int = 260,
    bench_navs: list[float] | None = None,
) -> str:
    rows = _equity_rows(equity)
    if len(rows) < 2:
        return ""
    navs = [_safe_float(r.get("nav")) or 0.0 for r in rows]
    # Normalize to 1.0 for overlay readability when benchmark present
    if bench_navs and len(bench_navs) == len(navs) and navs[0] > 0 and bench_navs[0] > 0:
        series_a = [n / navs[0] for n in navs]
        series_b = [n / bench_navs[0] for n in bench_navs]
        lo = min(min(series_a), min(series_b))
        hi = max(max(series_a), max(series_b))
        label_hi, label_lo = f"{hi:.2f}", f"{lo:.2f}"
        aria = "策略 vs 基准（归一化净值）"
    else:
        series_a = navs
        series_b = None
        lo, hi = min(navs), max(navs)
        label_hi, label_lo = f"{hi:,.0f}", f"{lo:,.0f}"
        aria = "净值曲线"
    span = (hi - lo) or 1.0
    pad_l, pad_r, pad_t, pad_b = 48, 16, 16, 36
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    def xy(i: int, nav: float) -> tuple[float, float]:
        x = pad_l + plot_w * i / (len(series_a) - 1)
        y = pad_t + plot_h * (1 - (nav - lo) / span)
        return x, y

    pts = [xy(i, n) for i, n in enumerate(series_a)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{pad_l:.1f},{pad_t + plot_h:.1f} " + poly + f" {pad_l + plot_w:.1f},{pad_t + plot_h:.1f}"
    y0 = pad_t + plot_h * (1 - (series_a[0] - lo) / span)
    start_lbl = str(rows[0].get("session") or "")
    end_lbl = str(rows[-1].get("session") or "")
    bench_poly = ""
    if series_b is not None:
        bpts = [xy(i, n) for i, n in enumerate(series_b)]
        bench_poly = (
            f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in bpts)}" '
            f'fill="none" stroke="#9a6700" stroke-width="1.8" stroke-dasharray="5 3"/>'
        )
    return f"""<svg class="equity" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{aria}">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#fbfdff"/>
  <line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h}" stroke="#d8e0ea"/>
  <line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{pad_l + plot_w}" y2="{pad_t + plot_h}" stroke="#d8e0ea"/>
  <line x1="{pad_l}" y1="{y0:.1f}" x2="{pad_l + plot_w}" y2="{y0:.1f}" stroke="#c5d0dc" stroke-dasharray="4 4"/>
  <polygon points="{area}" fill="#0b5cab22"/>
  <polyline points="{poly}" fill="none" stroke="#0b5cab" stroke-width="2.2"/>
  {bench_poly}
  <text x="{pad_l}" y="{height - 10}" fill="#5b6b7c" font-size="11">{start_lbl}</text>
  <text x="{pad_l + plot_w}" y="{height - 10}" fill="#5b6b7c" font-size="11" text-anchor="end">{end_lbl}</text>
  <text x="8" y="{pad_t + 10}" fill="#5b6b7c" font-size="11">{label_hi}</text>
  <text x="8" y="{pad_t + plot_h}" fill="#5b6b7c" font-size="11">{label_lo}</text>
</svg>"""


def drawdown_to_svg(equity: list[dict[str, Any]] | pl.DataFrame | None, *, width: int = 960, height: int = 180) -> str:
    rows = _equity_rows(equity)
    if len(rows) < 2:
        return ""
    navs = [_safe_float(r.get("nav")) or 0.0 for r in rows]
    peak = navs[0]
    dds: list[float] = []
    for n in navs:
        peak = max(peak, n)
        dds.append(n / peak - 1.0 if peak > 0 else 0.0)
    lo, hi = min(dds), 0.0
    span = (hi - lo) or 1.0
    pad_l, pad_r, pad_t, pad_b = 48, 16, 16, 28
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    def xy(i: int, dd: float) -> tuple[float, float]:
        x = pad_l + plot_w * i / (len(dds) - 1)
        y = pad_t + plot_h * (1 - (dd - lo) / span)
        return x, y

    pts = [xy(i, d) for i, d in enumerate(dds)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{pad_l:.1f},{pad_t:.1f} " + poly + f" {pad_l + plot_w:.1f},{pad_t:.1f}"
    return f"""<svg class="equity" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="回撤曲线">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#fffaf8"/>
  <line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h}" stroke="#d8e0ea"/>
  <line x1="{pad_l}" y1="{pad_t}" x2="{pad_l + plot_w}" y2="{pad_t}" stroke="#d8e0ea"/>
  <polygon points="{area}" fill="#b4231822"/>
  <polyline points="{poly}" fill="none" stroke="#b42318" stroke-width="2"/>
  <text x="8" y="{pad_t + 10}" fill="#5b6b7c" font-size="11">0%</text>
  <text x="8" y="{pad_t + plot_h}" fill="#5b6b7c" font-size="11">{lo * 100:.1f}%</text>
</svg>"""


def yearly_bars_to_svg(yearly: list[dict[str, Any]] | None, *, width: int = 960, height: int = 240) -> str:
    rows = list(yearly or [])
    if not rows:
        return ""
    vals_p = [_safe_float(r.get("ann_return")) or 0.0 for r in rows]
    vals_e = [_safe_float(r.get("ann_excess")) for r in rows]
    has_excess = any(v is not None for v in vals_e)
    vals_e_f = [v if v is not None else 0.0 for v in vals_e]
    all_v = vals_p + (vals_e_f if has_excess else [])
    lo, hi = min(min(all_v), 0.0), max(max(all_v), 0.0)
    span = (hi - lo) or 1.0
    pad_l, pad_r, pad_t, pad_b = 48, 16, 20, 40
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    n = len(rows)
    group_w = plot_w / max(n, 1)
    bar_w = group_w * (0.28 if has_excess else 0.5)
    zero_y = pad_t + plot_h * (1 - (0.0 - lo) / span)
    parts = [
        f'<svg class="equity" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="分年年化">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fbfdff"/>',
        f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{pad_l + plot_w}" y2="{zero_y:.1f}" stroke="#c5d0dc"/>',
    ]
    for i, r in enumerate(rows):
        cx = pad_l + group_w * (i + 0.5)
        series = [(vals_p[i], "#0b5cab")]
        if has_excess:
            series.append((vals_e_f[i], "#1b7f4a" if vals_e_f[i] >= 0 else "#b42318"))
        for j, (val, color) in enumerate(series):
            y_val = pad_t + plot_h * (1 - (val - lo) / span)
            y = min(y_val, zero_y)
            h = abs(zero_y - y_val)
            if has_excess:
                x = cx - bar_w - 1.0 + j * (bar_w + 2.0)
            else:
                x = cx - bar_w / 2
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(h, 1):.1f}" fill="{color}" opacity="0.85"/>'
            )
        parts.append(
            f'<text x="{cx:.1f}" y="{height - 12}" fill="#5b6b7c" font-size="11" text-anchor="middle">{r.get("year")}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def _gate_reason_zh(reason: str) -> str:
    mapping = {
        "oos_folds<2": "样本外折数不足 2",
        "sharpe_below_min": "夏普低于门槛",
        "drawdown_above_max": "回撤超过上限",
        "deflated_sharpe_below_min": "试次调整后夏普低于门槛",
        "n_trials_above_max": "试次超过上限",
        "pit_audit_fail": "PIT 审计失败",
    }
    if reason in mapping:
        return mapping[reason]
    if reason.startswith("oos_folds<"):
        return f"样本外折数不足 {reason.split('<', 1)[1]}"
    if reason.startswith("trades<"):
        return f"成交笔数不足 {reason.split('<', 1)[1]}"
    return reason


def _jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )

    def fmt(v: Any, digits: int = 2) -> str:
        x = _safe_float(v)
        if x is None:
            return "-"
        return f"{x:.{digits}f}"

    def pct(v: Any, digits: int = 2) -> str:
        x = _safe_float(v)
        if x is None:
            return "-"
        return f"{x * 100:.{digits}f}%"

    def money(v: Any) -> str:
        x = _safe_float(v)
        if x is None:
            return "-"
        return f"{x:,.2f}"

    def op_zh(op: str) -> str:
        return {
            "ge": "≥",
            "gt": ">",
            "le": "≤",
            "lt": "<",
            "eq": "=",
            "ne": "≠",
            "between": "介于",
        }.get(op, op)

    def exit_zh(reason: str) -> str:
        return {
            "entry": "开仓",
            "stop": "止损",
            "take_profit": "止盈",
            "max_hold": "到期持有",
            "exit_intent": "计划退出",
            "deferred_exit": "延期退出",
        }.get(reason, reason)

    def reject_zh(reason: str) -> str:
        return {
            "max_new_entries_or_max_names": "触及单日开仓/持股上限",
            "max_weight": "触及单标的权重上限",
            "limit_up": "涨停不可买",
            "limit_down": "跌停不可卖",
            "suspend": "停牌",
            "cash": "现金不足",
            "lot_size": "不足一手",
            "t1": "T+1 限制",
            "entry_filter": "开盘过滤未通过",
            "expired": "订单过期",
        }.get(reason, reason)

    def artifact_zh(key: str) -> str:
        return {
            "run_dir": "运行目录",
            "conclusion_json": "结论 JSON",
            "conclusion_html": "中文报告 HTML",
            "metrics_json": "绩效指标",
            "pit_audit": "PIT 审计",
            "trades": "成交明细",
            "equity": "净值序列",
            "ic_summary": "因子 IC",
            "events": "事件表",
            "ranked_events": "排序事件",
            "rejects": "拒单明细",
            "config": "策略快照",
        }.get(key, key)

    env.globals.update(
        fmt=fmt,
        pct=pct,
        money=money,
        op_zh=op_zh,
        exit_zh=exit_zh,
        reject_zh=reject_zh,
        artifact_zh=artifact_zh,
        sizing_zh=lambda s: {"equal_weight": "等权"}.get(s, s),
        sizing_base_zh=lambda s: {"cash": "现金", "nav": "净值"}.get(s, s),
        price_zh=lambda s: {"open": "开盘价", "close": "收盘价"}.get(s, s),
        ref_zh=lambda s: {
            "decision_prior_close": "决策日前收",
            "session_prior_close": "当日昨收",
        }.get(s, s),
        adj_zh=lambda s: {"qfq": "前复权", "hfq": "后复权", "none": "不复权"}.get(s, s),
    )
    return env


def enrich_conclusion(
    conclusion: dict[str, Any],
    *,
    trades: list[dict[str, Any]] | pl.DataFrame | None = None,
    equity: list[dict[str, Any]] | pl.DataFrame | None = None,
    rejects: list[dict[str, Any]] | None = None,
    artifact_links: dict[str, str] | None = None,
) -> dict[str, Any]:
    out = dict(conclusion)
    out["locale"] = out.get("locale") or "zh-CN"
    out["title"] = out.get("title") or "量化研究报告"
    out["generated_at"] = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")

    ic_info = summarize_ic(list(out.get("ic") or []))
    out.update(ic_info)

    trade_stats = summarize_trades(trades)
    if trade_stats:
        out["trade_stats"] = trade_stats

    eq_summary = summarize_equity(equity)
    if eq_summary:
        out["equity_summary"] = eq_summary
    metrics = out.get("metrics") or {}
    bench_nav = metrics.get("benchmark_nav_series")
    if isinstance(bench_nav, list) and bench_nav:
        out["equity_svg"] = equity_to_svg(equity, bench_navs=[float(x) for x in bench_nav])
    else:
        out["equity_svg"] = equity_to_svg(equity)
    out["drawdown_svg"] = drawdown_to_svg(equity)
    yearly = metrics.get("yearly") or out.get("yearly") or []
    out["yearly"] = yearly
    out["yearly_svg"] = yearly_bars_to_svg(yearly)

    out["reject_stats"] = summarize_rejects(rejects)

    if isinstance(trades, pl.DataFrame):
        trade_rows = trades.to_dicts()
    else:
        trade_rows = list(trades or [])
    out["sample_trades"] = trade_rows[-30:] if trade_rows else []

    reasons = list((out.get("gates") or {}).get("reasons") or [])
    out["gate_reasons"] = [_gate_reason_zh(r) for r in reasons]
    out["artifact_links"] = artifact_links or {}
    out["decisions"] = list(out.get("decisions") or [])
    return out


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_config_snapshot(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "config.snapshot.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_run_artifacts(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    art = run_dir / "artifacts"
    trades_path = art / "trades.csv"
    equity_path = art / "equity.csv"
    ic_path = art / "ic_summary.csv"
    trades = pl.read_csv(trades_path) if trades_path.exists() else None
    equity = pl.read_csv(equity_path) if equity_path.exists() else None
    ic_rows = pl.read_csv(ic_path).to_dicts() if ic_path.exists() else []
    rejects = _load_json(art / "rejects_summary.json") or []
    metrics = _load_json(art / "metrics.json") or {}
    wf = _load_json(art / "wf_folds.json")
    pit_audit = _load_json(art / "pit_audit.json")
    meta = _load_json(run_dir / "meta.json") or {}
    cfg = _load_config_snapshot(run_dir) or {}
    from qresearch.config import get_settings
    from qresearch.engines.experiment.decision_log import load_decisions_for_report

    settings = get_settings()
    study_id = (
        meta.get("study_id")
        or (cfg.get("hypothesis") or {}).get("study_id")
        or None
    )
    decisions = load_decisions_for_report(
        run_dir, studies_dir=settings.studies_dir, study_id=study_id
    )
    return {
        "trades": trades,
        "equity": equity,
        "ic_rows": ic_rows,
        "rejects": rejects if isinstance(rejects, list) else [],
        "metrics": metrics,
        "wf": wf,
        "pit_audit": pit_audit,
        "meta": meta,
        "config": cfg,
        "study_id": study_id,
        "decisions": decisions,
        "paths": {
            "run_dir": str(run_dir),
            "trades": str(trades_path) if trades_path.exists() else "",
            "equity": str(equity_path) if equity_path.exists() else "",
            "ic_summary": str(ic_path) if ic_path.exists() else "",
            "metrics_json": str(art / "metrics.json") if (art / "metrics.json").exists() else "",
            "pit_audit": str(art / "pit_audit.json") if (art / "pit_audit.json").exists() else "",
            "events": str(art / "events.parquet") if (art / "events.parquet").exists() else "",
            "ranked_events": str(art / "ranked_events.parquet")
            if (art / "ranked_events.parquet").exists()
            else "",
            "rejects": str(art / "rejects_summary.json")
            if (art / "rejects_summary.json").exists()
            else "",
            "config": str(run_dir / "config.snapshot.yaml"),
            "decisions": str(run_dir / "decisions") if (run_dir / "decisions").exists() else "",
            "study_index": str(settings.studies_dir / study_id / "INDEX.md")
            if study_id
            else "",
        },
    }


def build_conclusion_from_run(run_dir: Path, base: dict[str, Any] | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    loaded = load_run_artifacts(run_dir)
    cfg = loaded["config"] or {}
    meta = loaded["meta"] or {}
    existing = base
    if existing is None:
        conc_path = run_dir / "report" / "conclusion.json"
        existing = _load_json(conc_path) if conc_path.exists() else {}

    metrics = dict(existing.get("metrics") or loaded["metrics"] or meta.get("metrics") or {})
    if metrics.get("sharpe") is not None and metrics.get("deflated_sharpe") is None:
        from qresearch.engines.analysis.overfit import attach_overfit_metrics

        n_trials = int(
            meta.get("n_trials_assumed")
            or (cfg.get("gates") or {}).get("n_trials_assumed")
            or 1
        )
        metrics = attach_overfit_metrics(metrics, n_trials=n_trials)
    conclusion = {
        "schema_version": "1.2",
        "locale": "zh-CN",
        "title": "量化研究报告",
        "run_id": existing.get("run_id") or meta.get("run_id") or run_dir.name,
        "study_id": existing.get("study_id")
        or loaded.get("study_id")
        or meta.get("study_id")
        or (cfg.get("hypothesis") or {}).get("study_id"),
        "promotable": existing.get("promotable", meta.get("promotable", False)),
        "gates": existing.get("gates")
        or {"passed": False, "reasons": [], "promotable": False},
        "gates_cfg": existing.get("gates_cfg") or cfg.get("gates") or {},
        "n_events": existing.get("n_events") or meta.get("n_events") or 0,
        "metrics": metrics,
        "signals": existing.get("signals") or cfg.get("signals") or {},
        "execution": existing.get("execution") or cfg.get("execution") or {},
        "portfolio": existing.get("portfolio") or cfg.get("portfolio") or {},
        "costs": existing.get("costs") or cfg.get("costs") or {},
        "risk": existing.get("risk") or cfg.get("risk") or {},
        "adjustment": existing.get("adjustment") or cfg.get("adjustment") or {},
        "benchmark": existing.get("benchmark") or cfg.get("benchmark") or {},
        "adjustment_as_of": existing.get("adjustment_as_of")
        or meta.get("adjustment_as_of")
        or "",
        "ic": loaded["ic_rows"] or existing.get("ic") or [],
        "walk_forward": loaded["wf"] if loaded["wf"] is not None else existing.get("walk_forward"),
        "pit_audit": loaded.get("pit_audit") or existing.get("pit_audit"),
        "overfit": metrics.get("overfit") or existing.get("overfit"),
        "behavior_subset": existing.get("behavior_subset") or [],
        "decisions": loaded.get("decisions") or existing.get("decisions") or [],
        "hypothesis": existing.get("hypothesis") or cfg.get("hypothesis") or {},
    }
    links = {k: v for k, v in loaded["paths"].items() if v}
    return enrich_conclusion(
        conclusion,
        trades=loaded["trades"],
        equity=loaded["equity"],
        rejects=loaded["rejects"],
        artifact_links=links,
    )


def render_html(conclusion: dict[str, Any]) -> str:
    env = _jinja_env()
    tmpl = env.get_template("report_zh.html")
    return tmpl.render(
        run_id=conclusion.get("run_id"),
        study_id=conclusion.get("study_id"),
        locale=conclusion.get("locale"),
        generated_at=conclusion.get("generated_at"),
        promotable=conclusion.get("promotable"),
        n_events=conclusion.get("n_events"),
        metrics=conclusion.get("metrics") or {},
        gates=conclusion.get("gates") or {},
        gates_cfg=conclusion.get("gates_cfg") or {},
        gate_reasons=conclusion.get("gate_reasons") or [],
        ic_table=conclusion.get("ic_table") or conclusion.get("ic") or [],
        ic_best=conclusion.get("ic_best") or [],
        ic_by_feature=conclusion.get("ic_by_feature") or [],
        signals=conclusion.get("signals") or {},
        portfolio=conclusion.get("portfolio") or {},
        execution=conclusion.get("execution") or {},
        costs=conclusion.get("costs") or {},
        risk=conclusion.get("risk") or {},
        adjustment=conclusion.get("adjustment") or {},
        benchmark=conclusion.get("benchmark") or {},
        adjustment_as_of=conclusion.get("adjustment_as_of"),
        trade_stats=conclusion.get("trade_stats"),
        sample_trades=conclusion.get("sample_trades") or [],
        reject_stats=conclusion.get("reject_stats"),
        equity_svg=conclusion.get("equity_svg") or "",
        equity_summary=conclusion.get("equity_summary"),
        drawdown_svg=conclusion.get("drawdown_svg") or "",
        yearly=conclusion.get("yearly") or (conclusion.get("metrics") or {}).get("yearly") or [],
        yearly_svg=conclusion.get("yearly_svg") or "",
        walk_forward=conclusion.get("walk_forward"),
        wf_json=json.dumps(conclusion.get("walk_forward"), ensure_ascii=False, indent=2, default=str),
        pit_audit=conclusion.get("pit_audit"),
        overfit=conclusion.get("overfit") or (conclusion.get("metrics") or {}).get("overfit"),
        decisions=conclusion.get("decisions") or [],
        artifact_links=conclusion.get("artifact_links") or {},
    )


def write_report(
    report_dir: Path,
    conclusion: dict[str, Any],
    *,
    run_dir: Path | None = None,
) -> tuple[Path, Path]:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    if run_dir is not None:
        enriched = build_conclusion_from_run(run_dir, base=conclusion)
    else:
        enriched = enrich_conclusion(conclusion)

    # Keep machine JSON leaner: SVGs stay in HTML only.
    skip_svg = {"equity_svg", "drawdown_svg", "yearly_svg"}
    json_payload = {k: v for k, v in enriched.items() if k not in skip_svg}
    json_path = report_dir / "conclusion.json"
    html_path = report_dir / "conclusion.html"
    json_path.write_text(
        json.dumps(json_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    html_path.write_text(render_html(enriched), encoding="utf-8")
    # also write a dedicated Chinese alias
    zh_path = report_dir / "research_report_zh.html"
    zh_path.write_text(render_html(enriched), encoding="utf-8")
    return html_path, json_path


def write_report_from_run(run_dir: Path) -> tuple[Path, Path]:
    run_dir = Path(run_dir)
    report_dir = run_dir / "report"
    base = _load_json(report_dir / "conclusion.json")
    return write_report(report_dir, base or {}, run_dir=run_dir)
