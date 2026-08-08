from __future__ import annotations

import json
from pathlib import Path

import pytest

from qresearch.engines.experiment.promote import promote_run


def test_market_promote_force_cannot_bypass_missing_lineage(tmp_path: Path) -> None:
    run = tmp_path / "runs" / "market"
    (run / "report").mkdir(parents=True)
    (run / "meta.json").write_text(json.dumps({"sample_kind": "market"}), encoding="utf-8")
    (run / "report" / "conclusion.json").write_text(json.dumps({"promotable": True}), encoding="utf-8")

    with pytest.raises(PermissionError, match="lineage"):
        promote_run(tmp_path / "runs", tmp_path / "models", "market", "m", "1", force=True)


def test_market_promote_force_cannot_bypass_incomplete_st_filter(tmp_path: Path) -> None:
    run = tmp_path / "runs" / "market"
    artifacts = run / "artifacts"
    (run / "report").mkdir(parents=True)
    artifacts.mkdir()
    (run / "meta.json").write_text(
        json.dumps(
            {
                "sample_kind": "market",
                "feature_snapshot_sha256": "snapshot",
                "zer0share_fingerprint": "share",
                "zer0factor_revision": "factor",
                "st_filter_status": "listed_only",
            }
        ),
        encoding="utf-8",
    )
    (run / "report" / "conclusion.json").write_text(json.dumps({"promotable": True}), encoding="utf-8")
    for name in ("feature_manifest.json", "split_summary.json", "factor_screening_manifest.json"):
        (artifacts / name).write_text("{}", encoding="utf-8")

    with pytest.raises(PermissionError, match="ST filter"):
        promote_run(tmp_path / "runs", tmp_path / "models", "market", "m", "1", force=True)
