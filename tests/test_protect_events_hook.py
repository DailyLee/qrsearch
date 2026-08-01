from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".cursor" / "hooks" / "protect_events.py"


def _load():
    spec = importlib.util.spec_from_file_location("protect_events", HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pe():
    return _load()


def test_protected_path_detection(pe):
    assert pe._is_protected_path("workspace/events/plat_2019.csv")
    assert pe._is_protected_path(r"c:\Users\x\qrsearch\workspace\events_ascii\plat_2019.csv")
    assert pe._is_protected_path("workspace/events")
    assert not pe._is_protected_path("workspace/runs/foo/artifacts/events.parquet")
    assert not pe._is_protected_path("configs/examples/x.yaml")


def test_shell_allows_qr_csv_read(pe):
    cmd = 'qr factor compare --csv workspace/events_ascii/plat_2019.csv --format json --quiet'
    assert pe._shell_touches_protected_write(cmd) is False


def test_shell_denies_redirect_and_setcontent(pe):
    assert pe._shell_touches_protected_write(
        'echo bad > workspace/events/plat_2019.csv'
    )
    assert pe._shell_touches_protected_write(
        'Set-Content -Path workspace/events_ascii/x.csv -Value "a"'
    )
    assert pe._shell_touches_protected_write(
        'Remove-Item workspace/events/plat_2019.csv'
    )


def test_main_write_tool_deny(pe, capsys, monkeypatch):
    import io

    payload = {
        "tool_name": "Write",
        "tool_input": {"path": "workspace/events/plat_2019.csv", "contents": "x"},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert pe.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["permission"] == "deny"


def test_main_shell_qr_allow(pe, capsys, monkeypatch):
    import io

    payload = {
        "command": "qr data validate-events --csv workspace/events/plat_2019.csv --format json --quiet"
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert pe.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["permission"] == "allow"
