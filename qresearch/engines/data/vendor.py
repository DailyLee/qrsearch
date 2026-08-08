"""Market data vendor: zer0share LocalPro + file backend for tests."""

from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import polars as pl

from qresearch.config import get_settings
from qresearch.engines.data.fingerprint import fingerprint_paths


class VendorError(RuntimeError):
    pass


_pro = None


def get_local_pro():
    global _pro
    if _pro is not None:
        return _pro
    settings = get_settings()
    root = Path(os.environ.get("ZER0SHARE_ROOT", settings.zer0share_root))
    data = Path(os.environ.get("ZER0SHARE_DATA", settings.data_dir()))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from zer0share.api import LocalPro
    except Exception as e:
        raise VendorError(
            f"cannot import zer0share LocalPro (ZER0SHARE_ROOT={root}): {e}"
        ) from e
    if not data.is_dir():
        raise VendorError(f"zer0share data dir missing: {data}")
    _pro = LocalPro(str(data))
    return _pro


def ping_vendor() -> dict:
    settings = get_settings()
    root = Path(os.environ.get("ZER0SHARE_ROOT", settings.zer0share_root))
    data = Path(os.environ.get("ZER0SHARE_DATA", settings.data_dir()))
    info = {
        "zer0share_root": str(root),
        "zer0share_data": str(data),
        "root_exists": root.is_dir(),
        "data_exists": data.is_dir(),
        "import_ok": False,
        "fingerprint": "unavailable",
    }
    try:
        pro = get_local_pro()
        info["import_ok"] = True
        info["pro_type"] = type(pro).__name__
        sample = list(data.glob("**/data.parquet"))[:200]
        info["fingerprint"] = fingerprint_paths(sample) if sample else "unavailable"
    except Exception as e:
        info["error"] = str(e)
    return info


def _yyyymmdd(d: date | datetime | str) -> str:
    if isinstance(d, str):
        if len(d) == 8 and d.isdigit():
            return d
        return d.replace("-", "")[:8]
    if isinstance(d, datetime):
        return d.strftime("%Y%m%d")
    return d.strftime("%Y%m%d")


def load_trade_calendar(start: date, end: date, exchange: str = "SSE") -> list[date]:
    """Return open trading days between start and end inclusive."""
    pro = get_local_pro()
    df = pro.trade_cal(
        exchange=exchange,
        start_date=_yyyymmdd(start),
        end_date=_yyyymmdd(end),
        is_open="1",
    )
    if df is None or getattr(df, "empty", True):
        return []
    cal = []
    for x in df["cal_date"].tolist():
        s = str(x)
        cal.append(datetime.strptime(s, "%Y%m%d").date())
    return sorted(cal)


def is_index_ts_code(ts_code: str) -> bool:
    """Heuristic: SH indices are 000xxx.SH; SZ indices are 399xxx.SZ."""
    c = str(ts_code).strip().upper()
    if c.endswith(".SH") and c.startswith("000"):
        return True
    if c.endswith(".SZ") and c.startswith("399"):
        return True
    return False


_EMPTY_BARS = {
    "instrument": pl.Utf8,
    "trade_date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "vol": pl.Float64,
    "amount": pl.Float64,
    "adj_factor": pl.Float64,
    "up_limit": pl.Float64,
    "down_limit": pl.Float64,
}


def load_daily_long(
    instruments: Iterable[str],
    start: date,
    end: date,
    *,
    adj: str = "qfq",
) -> tuple[pl.DataFrame, str]:
    """Load unadjusted OHLCV + adj_factor for stocks.

    Prices are NOT window-end qfq'd here. PIT qfq/hfq is applied in PricePanel.get.
    `adj` is kept for API compatibility / cache labeling only.
    """
    _ = adj  # adjustment applied at read time in PricePanel
    codes = sorted({c for c in instruments if not is_index_ts_code(c)})
    if not codes:
        return pl.DataFrame(schema=_EMPTY_BARS), "unavailable"

    pro = get_local_pro()
    start_s, end_s = _yyyymmdd(start), _yyyymmdd(end)
    import pandas as pd

    daily_parts = []
    if len(codes) <= 80:
        for code in codes:
            part = pro.daily(ts_code=code, start_date=start_s, end_date=end_s)
            if part is not None and not getattr(part, "empty", True):
                daily_parts.append(part)
        daily = pd.concat(daily_parts, ignore_index=True) if daily_parts else pd.DataFrame()
    else:
        daily = pro.daily(start_date=start_s, end_date=end_s)
        if daily is not None and not getattr(daily, "empty", True):
            daily = daily[daily["ts_code"].isin(codes)].copy()
    if daily is None or getattr(daily, "empty", True):
        raise VendorError(f"no daily bars in [{start_s},{end_s}] for universe")

    if len(codes) <= 80:
        adj_parts = []
        for code in codes:
            part = pro.adj_factor(ts_code=code, start_date=start_s, end_date=end_s)
            if part is not None and not getattr(part, "empty", True):
                adj_parts.append(part)
        adj_df = pd.concat(adj_parts, ignore_index=True) if adj_parts else pd.DataFrame()
    else:
        adj_df = pro.adj_factor(start_date=start_s, end_date=end_s)
        if adj_df is not None and not getattr(adj_df, "empty", True):
            adj_df = adj_df[adj_df["ts_code"].isin(codes)]
    if adj_df is None or adj_df.empty:
        raise VendorError("adj_factor empty")

    limit_df = pro.stk_limit(
        ts_code=codes,
        start_date=start_s,
        end_date=end_s,
        fields="ts_code,trade_date,up_limit,down_limit",
    )
    if limit_df is None or getattr(limit_df, "empty", True):
        limit_df = pd.DataFrame(columns=["ts_code", "trade_date", "up_limit", "down_limit"])
    else:
        limit_df = limit_df[limit_df["ts_code"].isin(codes)][
            ["ts_code", "trade_date", "up_limit", "down_limit"]
        ]

    merged = daily.merge(
        adj_df[["ts_code", "trade_date", "adj_factor"]],
        on=["ts_code", "trade_date"],
        how="left",
    ).merge(
        limit_df,
        on=["ts_code", "trade_date"],
        how="left",
    ).sort_values(["ts_code", "trade_date"])
    # PIT-safe: only fill from the past (never bfill from future factors)
    merged["adj_factor"] = merged.groupby("ts_code")["adj_factor"].ffill()
    merged = merged.dropna(subset=["adj_factor"])

    merged["trade_date"] = pd.to_datetime(merged["trade_date"].astype(str), format="%Y%m%d")
    pl_df = pl.from_pandas(
        merged[
            [
                "ts_code",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "vol",
                "amount",
                "adj_factor",
                "up_limit",
                "down_limit",
            ]
        ].rename(columns={"ts_code": "instrument"})
    ).with_columns(
        pl.col("trade_date").cast(pl.Date),
        pl.col("adj_factor").cast(pl.Float64),
        pl.col("up_limit").cast(pl.Float64),
        pl.col("down_limit").cast(pl.Float64),
    )

    settings = get_settings()
    data_root = Path(os.environ.get("ZER0SHARE_DATA", settings.data_dir()))
    fp = fingerprint_paths(
        [
            *data_root.glob("stock/daily_kline/**/data.parquet"),
            *data_root.glob("stock/adj_factor/**/data.parquet"),
            *data_root.glob("stock/stk_limit/**/data.parquet"),
        ]
    )
    return pl_df, fp


def load_index_daily(ts_code: str, start: date, end: date) -> pl.DataFrame:
    """Load index OHLCV; adj_factor fixed at 1.0 (no stock-style adjustment)."""
    pro = get_local_pro()
    df = pro.index_daily(ts_code=ts_code, start_date=_yyyymmdd(start), end_date=_yyyymmdd(end))
    if df is None or getattr(df, "empty", True):
        return pl.DataFrame(schema=_EMPTY_BARS)
    import pandas as pd

    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d")
    if "vol" not in df.columns:
        df["vol"] = 0.0
    if "amount" not in df.columns:
        df["amount"] = 0.0
    df["adj_factor"] = 1.0
    df["up_limit"] = None
    df["down_limit"] = None
    return pl.from_pandas(
        df[
            [
                "ts_code",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "vol",
                "amount",
                "adj_factor",
                "up_limit",
                "down_limit",
            ]
        ].rename(columns={"ts_code": "instrument"})
    ).with_columns(
        pl.col("trade_date").cast(pl.Date),
        pl.col("vol").cast(pl.Float64),
        pl.col("amount").cast(pl.Float64),
        pl.col("adj_factor").cast(pl.Float64),
        pl.col("up_limit").cast(pl.Float64),
        pl.col("down_limit").cast(pl.Float64),
    )


def load_indices_long(instruments: Iterable[str], start: date, end: date) -> pl.DataFrame:
    frames = [load_index_daily(code, start, end) for code in sorted(set(instruments))]
    frames = [f for f in frames if f.height]
    if not frames:
        return pl.DataFrame(schema=_EMPTY_BARS)
    return pl.concat(frames, how="diagonal_relaxed")
