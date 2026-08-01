from __future__ import annotations

import json
from pathlib import Path

from qresearch.engines.data.vendor import is_index_ts_code
from qresearch.engines.experiment.decision_log import (
    list_decisions,
    load_decisions_for_report,
    write_decision,
)


def test_is_index_ts_code():
    assert is_index_ts_code("000852.SH")
    assert is_index_ts_code("000300.SH")
    assert is_index_ts_code("399006.SZ")
    assert not is_index_ts_code("000001.SZ")  # 平安银行
    assert not is_index_ts_code("600519.SH")


def test_write_decision(tmp_path: Path):
    studies = tmp_path / "studies"
    out = write_decision(
        studies,
        study_id="demo_study",
        stage="factor_analysis",
        summary="pick pre_r1",
        rationale="highest |ICIR|",
        evidence={"icir": -0.05},
        next_action="strategy_design",
    )
    assert Path(out["md_path"]).exists()
    assert Path(out["json_path"]).exists()
    assert Path(out["index_path"]).exists()
    rows = list_decisions(studies, "demo_study")
    assert len(rows) == 1
    assert rows[0]["stage"] == "factor_analysis"


def test_decision_mirrors_into_run_and_report_loader(tmp_path: Path):
    studies = tmp_path / "studies"
    runs = tmp_path / "runs"
    run_id = "20260801_120000_001"
    run_dir = runs / run_id
    (run_dir / "report").mkdir(parents=True)
    (run_dir / "meta.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    (run_dir / "report" / "research_report_zh.html").write_text("<html/>", encoding="utf-8")

    write_decision(
        studies,
        study_id="plat_demo",
        stage="full_sample",
        summary="final disclose",
        rationale="frozen params",
        run_id=run_id,
        runs_dir=runs,
    )
    assert (run_dir / "decisions").exists()
    assert list((run_dir / "decisions").glob("*.json"))
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["study_id"] == "plat_demo"
    assert (studies / "plat_demo" / "RUNS.md").exists()

    # earlier stage without run still appears via study trail
    write_decision(
        studies,
        study_id="plat_demo",
        stage="factor_analysis",
        summary="ic pick",
        rationale="excess ic",
    )
    trail = load_decisions_for_report(run_dir, studies_dir=studies, study_id="plat_demo")
    stages = {d["stage"] for d in trail}
    assert "factor_analysis" in stages
    assert "full_sample" in stages
