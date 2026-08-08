from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from qresearch.config.models import SampleConfig
from qresearch.research.providers.market import MarketSampleProvider, ResearchDataError


class FakeLocalPro:
    def __init__(self, memberships: list[dict[str, str]]) -> None:
        self.memberships = memberships
        self.universe_calls: list[dict[str, str]] = []
        self.stock_basic_called = False

    def universe(self, **kwargs: str) -> pd.DataFrame:
        self.universe_calls.append(kwargs)
        return pd.DataFrame(self.memberships, columns=["trade_date", "universe", "ts_code"])

    def stock_basic(self, **kwargs: str) -> pd.DataFrame:
        self.stock_basic_called = True
        raise AssertionError("market samples must use daily universe membership")


def _config() -> SampleConfig:
    return SampleConfig(universe="univ_research_base", start_date=date(2024, 1, 2), end_date=date(2024, 1, 5))


def _calendar() -> list[date]:
    return [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]


def test_materialize_uses_daily_membership_for_additions_removals_and_delistings() -> None:
    # Replacing daily membership with currently listed stocks would invent A on Jan 4 or retain B after Jan 2.
    pro = FakeLocalPro(
        [
            {"trade_date": "20240102", "universe": "univ_research_base", "ts_code": "000001.SZ"},
            {"trade_date": "20240102", "universe": "univ_research_base", "ts_code": "000002.SZ"},
            {"trade_date": "20240103", "universe": "univ_research_base", "ts_code": "000001.SZ"},
            {"trade_date": "20240103", "universe": "univ_research_base", "ts_code": "000003.SZ"},
            {"trade_date": "20240104", "universe": "univ_research_base", "ts_code": "000003.SZ"},
        ]
    )

    samples = MarketSampleProvider(pro, _calendar()).materialize(_config())

    assert samples.frame.select(
        "sample_id", "instrument", "asof_session", "effective_session", "sample_weight"
    ).to_dicts() == [
        {
            "sample_id": "market:univ_research_base:20240102:000001.SZ",
            "instrument": "000001.SZ",
            "asof_session": date(2024, 1, 2),
            "effective_session": date(2024, 1, 3),
            "sample_weight": 1.0,
        },
        {
            "sample_id": "market:univ_research_base:20240102:000002.SZ",
            "instrument": "000002.SZ",
            "asof_session": date(2024, 1, 2),
            "effective_session": date(2024, 1, 3),
            "sample_weight": 1.0,
        },
        {
            "sample_id": "market:univ_research_base:20240103:000001.SZ",
            "instrument": "000001.SZ",
            "asof_session": date(2024, 1, 3),
            "effective_session": date(2024, 1, 4),
            "sample_weight": 1.0,
        },
        {
            "sample_id": "market:univ_research_base:20240103:000003.SZ",
            "instrument": "000003.SZ",
            "asof_session": date(2024, 1, 3),
            "effective_session": date(2024, 1, 4),
            "sample_weight": 1.0,
        },
        {
            "sample_id": "market:univ_research_base:20240104:000003.SZ",
            "instrument": "000003.SZ",
            "asof_session": date(2024, 1, 4),
            "effective_session": date(2024, 1, 5),
            "sample_weight": 1.0,
        },
    ]
    assert pro.universe_calls == [
        {
            "universe": "univ_research_base",
            "start_date": "20240102",
            "end_date": "20240105",
            "fields": "trade_date,universe,ts_code",
        }
    ]
    assert not pro.stock_basic_called


def test_materialize_rejects_duplicate_daily_membership() -> None:
    # Removing duplicate validation would overweight a stock's same-day observation.
    pro = FakeLocalPro(
        [
            {"trade_date": "20240102", "universe": "univ_research_base", "ts_code": "000001.SZ"},
            {"trade_date": "20240102", "universe": "univ_research_base", "ts_code": "000001.SZ"},
        ]
    )

    with pytest.raises(ResearchDataError, match="duplicate"):
        MarketSampleProvider(pro, _calendar()).materialize(_config())


def test_materialize_rejects_membership_from_a_different_universe() -> None:
    # Ignoring the response universe would mislabel another universe's stock as this sample's membership.
    pro = FakeLocalPro(
        [
            {"trade_date": "20240102", "universe": "univ_trade_hs300", "ts_code": "000001.SZ"},
        ]
    )

    with pytest.raises(ResearchDataError, match="universe"):
        MarketSampleProvider(pro, _calendar()).materialize(_config())


def test_materialize_keeps_final_asof_when_calendar_has_its_next_session() -> None:
    # Using config.end_date as a hard cutoff would discard a valid next-session-effective final membership.
    pro = FakeLocalPro(
        [
            {"trade_date": "20240105", "universe": "univ_research_base", "ts_code": "000004.SZ"},
        ]
    )
    calendar = [*_calendar(), date(2024, 1, 8)]

    samples = MarketSampleProvider(pro, calendar).materialize(_config())

    assert samples.frame.select("asof_session", "effective_session").to_dicts() == [
        {"asof_session": date(2024, 1, 5), "effective_session": date(2024, 1, 8)}
    ]
    assert samples.manifest["dropped_no_effective_session"] == 0


def test_materialize_fingerprint_changes_when_relevant_universe_partition_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    # Dropping source data identity would make changed historical membership indistinguishable between runs.
    monkeypatch.delenv("ZER0SHARE_DATA", raising=False)
    monkeypatch.setattr(
        "qresearch.research.providers.market.get_settings",
        lambda: SimpleNamespace(data_dir=lambda: tmp_path),
    )
    partition = (
        tmp_path
        / "stock"
        / "universe"
        / "name=univ_research_base"
        / "date=20240102"
        / "data.parquet"
    )
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"membership-v1")
    pro = FakeLocalPro(
        [
            {"trade_date": "20240102", "universe": "univ_research_base", "ts_code": "000001.SZ"},
        ]
    )

    first = MarketSampleProvider(pro, _calendar()).materialize(_config())
    partition.write_bytes(b"membership-v2-with-revised-content")
    second = MarketSampleProvider(pro, _calendar()).materialize(_config())

    assert first.manifest["zer0share_data_fingerprint"] != second.manifest["zer0share_data_fingerprint"]


def test_materialize_drops_last_session_without_effective_date_and_records_lineage() -> None:
    # Keeping a final-day row would make an observation executable without an effective session.
    pro = FakeLocalPro(
        [
            {"trade_date": "20240105", "universe": "univ_research_base", "ts_code": "000004.SZ"},
        ]
    )

    samples = MarketSampleProvider(pro, _calendar()).materialize(_config())

    assert samples.frame.is_empty()
    assert samples.manifest["sample_kind"] == "market"
    assert samples.manifest["universe"] == "univ_research_base"
    assert samples.manifest["start_date"] == "2024-01-02"
    assert samples.manifest["end_date"] == "2024-01-05"
    assert samples.manifest["rows"] == 0
    assert samples.manifest["instruments"] == 0
    assert samples.manifest["dropped_no_effective_session"] == 1
    assert isinstance(samples.manifest["zer0share_data_fingerprint"], str)
    assert "latest" not in samples.manifest
    assert "cache_hit" not in samples.manifest


def test_materialize_records_non_full_st_filter_status(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("ZER0SHARE_DATA", raising=False)
    monkeypatch.setattr(
        "qresearch.research.providers.market.get_settings",
        lambda: SimpleNamespace(data_dir=lambda: tmp_path),
    )
    meta = tmp_path / "stock" / "universe" / "build_meta"
    meta.mkdir(parents=True)
    for raw_date, status in (("20240102", "full"), ("20240103", "listed_only"), ("20240104", "full"), ("20240105", "full")):
        (meta / f"date={raw_date}.json").write_text(
            '{"st_filter_status":"' + status + '"}', encoding="utf-8"
        )

    samples = MarketSampleProvider(
        FakeLocalPro([{"trade_date": "20240102", "universe": "univ_research_base", "ts_code": "000001.SZ"}]),
        _calendar(),
    ).materialize(_config())

    assert samples.manifest["st_filter_status"] == "listed_only"
    assert samples.manifest["st_filter_status_by_date"]["2024-01-03"] == "listed_only"
