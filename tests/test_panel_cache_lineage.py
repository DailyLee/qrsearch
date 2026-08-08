from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from qresearch.config.models import ResearchConfig
from qresearch.engines.data import vendor
from qresearch.engines.data.panel import load_price_panel


def _events() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "entry_intent_date": [date(2024, 1, 10)],
            "exit_intent_date": [date(2024, 1, 12)],
            "instrument": ["000001.SZ"],
        }
    )


def _config() -> ResearchConfig:
    return ResearchConfig(adjustment={"mode": "none"}, benchmark={"instrument": ""})


def _bars() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "instrument": ["000001.SZ"],
            "trade_date": [date(2024, 1, 10)],
            "open": [10.0],
            "high": [10.2],
            "low": [9.8],
            "close": [10.1],
            "vol": [100.0],
            "amount": [1_000.0],
            "adj_factor": [1.0],
        }
    )


def _sidecar_path(cache_dir: Path) -> Path:
    return next(cache_dir.glob("*.meta.json"))


def test_cache_hit_restores_source_fingerprint_without_reloading(monkeypatch, tmp_path):
    """Catches returning the cache marker instead of the source data fingerprint."""
    events = _events()
    config = _config()
    monkeypatch.setattr(vendor, "load_trade_calendar", lambda *_args: [date(2024, 1, 10)])
    monkeypatch.setattr(
        vendor, "load_daily_long", lambda *_args, **_kwargs: (_bars(), "source-fp-1")
    )

    first = load_price_panel(events, config, cache_dir=tmp_path)
    sidecar = _sidecar_path(tmp_path)

    assert first.data_fingerprint == "source-fp-1"
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {
        "data_fingerprint": "source-fp-1",
        "cache_key": sidecar.name.removesuffix(".meta.json"),
    }

    def fail_if_reloaded(*_args, **_kwargs):
        raise AssertionError("cache hit unexpectedly reloaded daily prices")

    monkeypatch.setattr(vendor, "load_daily_long", fail_if_reloaded)
    second = load_price_panel(events, config, cache_dir=tmp_path)

    assert second.data_fingerprint == "source-fp-1"


def test_missing_sidecar_reloads_prices_and_recreates_lineage(monkeypatch, tmp_path):
    """Catches treating a parquet-only legacy cache as a valid cache hit."""
    events = _events()
    config = _config()
    monkeypatch.setattr(vendor, "load_trade_calendar", lambda *_args: [date(2024, 1, 10)])
    monkeypatch.setattr(
        vendor, "load_daily_long", lambda *_args, **_kwargs: (_bars(), "source-fp-1")
    )
    load_price_panel(events, config, cache_dir=tmp_path)
    _sidecar_path(tmp_path).unlink()

    monkeypatch.setattr(
        vendor, "load_daily_long", lambda *_args, **_kwargs: (_bars(), "source-fp-2")
    )
    panel = load_price_panel(events, config, cache_dir=tmp_path)
    sidecar = _sidecar_path(tmp_path)

    assert panel.data_fingerprint == "source-fp-2"
    assert json.loads(sidecar.read_text(encoding="utf-8"))["data_fingerprint"] == "source-fp-2"


@pytest.mark.parametrize(
    ("invalid_sidecar", "case"),
    [
        ("{not-json", "corrupt"),
        (json.dumps({"data_fingerprint": "source-fp-1", "cache_key": "wrong-key"}), "mismatched"),
        (json.dumps({"cache_key": "missing-fingerprint"}), "missing-fingerprint"),
    ],
)
def test_invalid_sidecar_reloads_prices_and_replaces_lineage(
    monkeypatch, tmp_path, invalid_sidecar, case
):
    """Catches accepting corrupt or incomplete lineage metadata as a cache hit."""
    events = _events()
    config = _config()
    monkeypatch.setattr(vendor, "load_trade_calendar", lambda *_args: [date(2024, 1, 10)])
    monkeypatch.setattr(
        vendor, "load_daily_long", lambda *_args, **_kwargs: (_bars(), "source-fp-1")
    )
    load_price_panel(events, config, cache_dir=tmp_path)
    sidecar = _sidecar_path(tmp_path)
    sidecar.write_text(invalid_sidecar, encoding="utf-8")

    monkeypatch.setattr(
        vendor, "load_daily_long", lambda *_args, **_kwargs: (_bars(), f"reloaded-{case}")
    )
    panel = load_price_panel(events, config, cache_dir=tmp_path)

    assert panel.data_fingerprint == f"reloaded-{case}"
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {
        "data_fingerprint": f"reloaded-{case}",
        "cache_key": sidecar.name.removesuffix(".meta.json"),
    }
