"""Event CSV ingest with aliases and strict validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import polars as pl

class IngestError(ValueError):
    """Hard-fail ingest error."""


@dataclass(frozen=True)
class _EventIngestOptions:
    instrument: str = "code"
    decision_date: str | None = None
    entry_intent_date: str = "buy_date"
    exit_intent_date: str = "sell_date"
    feature_aliases: dict[str, str] | None = None
    coalesce: str = "last"
    date_formats: tuple[str, ...] = ("%Y/%m/%d", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d")
    board: str = "limit10"


_CODE_RE = re.compile(r"^(?:(?P<ex>sh|sz|bj)\.)?(?P<code>\d{6})(?:\.(?P<sfx>SH|SZ|BJ|SSE|SZSE))?$", re.I)


def resolve_event_paths(patterns: str | Path | Iterable[str | Path]) -> list[Path]:
    if isinstance(patterns, (str, Path)):
        patterns = [patterns]
    paths: list[Path] = []
    for pat in patterns:
        p = Path(pat)
        if any(ch in str(pat) for ch in "*?[]"):
            parent = p.parent if str(p.parent) != "." else Path(".")
            paths.extend(sorted(parent.glob(p.name)))
        else:
            if not p.exists():
                raise IngestError(f"event file not found: {p}")
            paths.append(p)
    if not paths:
        raise IngestError(f"no event files matched: {patterns}")
    return paths


def to_ts_code(raw: str) -> str:
    s = str(raw).strip()
    m = _CODE_RE.match(s)
    if not m:
        raise IngestError(f"cannot parse instrument code: {raw!r}")
    code = m.group("code")
    ex = (m.group("ex") or "").lower()
    sfx = (m.group("sfx") or "").upper()
    if ex == "sh" or sfx in ("SH", "SSE"):
        return f"{code}.SH"
    if ex == "sz" or sfx in ("SZ", "SZSE"):
        return f"{code}.SZ"
    if ex == "bj" or sfx in ("BJ", "BSE"):
        return f"{code}.BJ"
    # bare 6 digits
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


# 20% limit boards: 科创 688/689 + 创业 300/301
_LIMIT20_PREFIXES = ("688", "689", "300", "301")


def is_limit20_board(instrument: str) -> bool:
    """科创板+创业板（约 20% 涨跌停）：688/689/300/301。"""
    code = str(instrument).strip().split(".", 1)[0]
    return code.startswith(_LIMIT20_PREFIXES)


def _limit20_mask() -> pl.Expr:
    prefix = pl.col("instrument").str.slice(0, 3)
    return prefix.is_in(list(_LIMIT20_PREFIXES))


def filter_events_by_board(df: pl.DataFrame, board: str) -> pl.DataFrame:
    if board == "all":
        return df
    if board == "limit20":
        return df.filter(_limit20_mask())
    if board == "limit10":
        return df.filter(~_limit20_mask())
    raise IngestError(f"unknown ingest.board={board!r}; use limit10|limit20|all")


def parse_date(value: object, formats: list[str]) -> date:
    if value is None or (isinstance(value, float) and str(value) == "nan"):
        raise IngestError("empty date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # tolerate 2019/1/2
    try:
        parts = s.replace("-", "/").split("/")
        if len(parts) == 3:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            return date(y, m, d)
    except Exception as e:
        raise IngestError(f"cannot parse date: {value!r}") from e
    raise IngestError(f"cannot parse date: {value!r}")


def _coalesce_numeric(text: str, policy: str) -> float | None:
    if text is None or text == "":
        return None
    s = str(text).strip()
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip() != ""]
        nums = []
        for p in parts:
            try:
                nums.append(float(p))
            except ValueError:
                continue
        if not nums:
            return None
        if policy == "max":
            return max(nums)
        if policy == "first":
            return nums[0]
        return nums[-1]  # last
    try:
        return float(s)
    except ValueError:
        return None


def load_events(
    paths: str | Path | Iterable[str | Path],
    config: _EventIngestOptions | None = None,
) -> pl.DataFrame:
    ingest = config or _EventIngestOptions()
    files = resolve_event_paths(paths)
    frames: list[pl.DataFrame] = []
    for fp in files:
        try:
            raw = pl.read_csv(fp, infer_schema_length=5000, ignore_errors=False)
        except Exception:
            raw = pl.read_csv(fp, encoding="utf8-lossy", infer_schema_length=5000)
        frames.append(_normalize_frame(raw, ingest, source=str(fp)))
    df = pl.concat(frames, how="diagonal_relaxed")
    # dedupe
    df = df.unique(subset=["instrument", "entry_intent_date"], keep="first")
    board = getattr(ingest, "board", "limit10") or "limit10"
    df = filter_events_by_board(df, board)
    if df.height == 0:
        raise IngestError(
            f"no valid events after ingest (board={board!r}); "
            "try ingest.board=all|limit20|limit10 or check CSV"
        )
    return df.sort(["entry_intent_date", "instrument"])


def _normalize_frame(raw: pl.DataFrame, ingest: _EventIngestOptions, source: str) -> pl.DataFrame:
    cols = {c.lower(): c for c in raw.columns}
    def col(name: str) -> str:
        # exact then casefold
        if name in raw.columns:
            return name
        key = name.lower()
        if key in cols:
            return cols[key]
        raise IngestError(f"missing column {name!r} in {source}; have={list(raw.columns)}")

    inst_col = col(ingest.instrument)
    entry_col = col(ingest.entry_intent_date)
    exit_col = col(ingest.exit_intent_date)
    decision_src = ingest.decision_date
    decision_col = col(decision_src) if decision_src else entry_col

    instruments = []
    decisions = []
    entries = []
    exits = []
    # select unique physical columns then project (decision may alias entry)
    needed = []
    for c in (inst_col, decision_col, entry_col, exit_col):
        if c not in needed:
            needed.append(c)
    base = raw.select(needed)
    for row in base.iter_rows(named=True):
        instruments.append(to_ts_code(row[inst_col]))
        decisions.append(parse_date(row[decision_col], ingest.date_formats))
        entries.append(parse_date(row[entry_col], ingest.date_formats))
        exits.append(parse_date(row[exit_col], ingest.date_formats))

    out = pl.DataFrame(
        {
            "instrument": instruments,
            "decision_date": decisions,
            "entry_intent_date": entries,
            "exit_intent_date": exits,
            "source_file": [source] * len(instruments),
        }
    )

    # feature columns: all other numeric-ish columns + mapped aliases
    reserved = {inst_col, entry_col, exit_col, decision_col}
    feature_map = dict(ingest.feature_aliases or {})
    # auto-map common names
    for c in raw.columns:
        if c in reserved:
            continue
        key = c
        if c == "%B":
            key = "pct_b"
        elif c.lower() in ("box_quality", "bandwidth_percent", "pre_cum2", "pre_r1", "rsi_value"):
            key = c.lower() if c != "%B" else "pct_b"
        feature_map.setdefault(key if key != c else c, c)

    feat_data: dict[str, list] = {}
    for feat_name, src_name in feature_map.items():
        if src_name not in raw.columns:
            # try case-insensitive / %B
            src_actual = None
            for c in raw.columns:
                if c == src_name or c.lower() == src_name.lower() or (src_name in ("%B", "pct_b") and c == "%B"):
                    src_actual = c
                    break
            if src_actual is None:
                continue
            src_name = src_actual
        vals: list[Any] = []
        keep_as_str = False
        for v in raw.get_column(src_name).to_list():
            num = _coalesce_numeric(v, ingest.coalesce)
            if num is not None:
                vals.append(num)
                continue
            if v is None or str(v).strip() == "":
                vals.append(None)
                continue
            # Preserve categoricals (e.g. industry names) that are not numeric
            keep_as_str = True
            vals.append(str(v).strip())
        if keep_as_str:
            vals = [None if x is None else str(x) for x in vals]
        feat_data[f"features__{feat_name if feat_name != '%B' else 'pct_b'}"] = vals

    if feat_data:
        feats_df = pl.DataFrame(feat_data)
        out = out.hstack(feats_df)

    # rename features__x -> keep as features.x via struct later; for simplicity use flat names with prefix
    rename = {c: c.replace("features__", "features.") for c in out.columns if c.startswith("features__")}
    out = out.rename(rename)
    return out


def validate_events(paths: str | Path | Iterable[str | Path]) -> dict:
    ingest = _EventIngestOptions()
    # Count boards before filter (same parse path, board=all) for disclosure.
    all_cfg = replace(ingest, board="all")
    raw = load_events(paths, all_cfg)
    n_limit20 = int(raw.filter(_limit20_mask()).height)
    n_limit10 = int(raw.height - n_limit20)
    board = ingest.board
    df = filter_events_by_board(raw, board)
    if df.height == 0:
        raise IngestError(
            f"no valid events after ingest (board={board!r}); "
            "try ingest.board=all|limit20|limit10 or check CSV"
        )
    return {
        "n_events": df.height,
        "n_instruments": df["instrument"].n_unique(),
        "board": board,
        "n_limit20": n_limit20,
        "n_limit10": n_limit10,
        "entry_min": str(df["entry_intent_date"].min()),
        "entry_max": str(df["entry_intent_date"].max()),
        "feature_cols": [c for c in df.columns if c.startswith("features.")],
    }
