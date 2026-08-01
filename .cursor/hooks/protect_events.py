#!/usr/bin/env python
"""Block agent writes to original event CSVs under workspace/events*."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Relative roots (posix-normalized, case-insensitive match)
PROTECTED_ROOTS = (
    "workspace/events/",
    "workspace/events_ascii/",
)
PROTECTED_DIR_NAMES = (
    "workspace/events",
    "workspace/events_ascii",
)

SHELL_WRITE_RE = re.compile(
    r"(?i)("
    r"\bset-content\b|\badd-content\b|\bout-file\b|\btee-object\b|\btee\b|"
    r"\bremove-item\b|\bmove-item\b|\brename-item\b|\bcopy-item\b|\bnew-item\b|"
    r"\bdel\b|\berase\b|\brm\b|\brmdir\b|\bmv\b|\bcp\b|\bren\b|"
    r"\bsed\s+-i\b|\bperl\s+-i\b|"
    r"\bopen\s*\([^)]*['\"][wa]"  # python open(..., 'w'/'a')
    r")"
)

# File redirect to a path (exclude 2>&1 / *>&1 style)
REDIRECT_TO_RE = re.compile(
    r"(?<![0-9*])>{1,2}\s*['\"]?([^\s'\"|]+)",
    re.IGNORECASE,
)


def _norm(p: str) -> str:
    return p.replace("\\", "/").lower()


def _is_protected_path(path: str | None) -> bool:
    if not path:
        return False
    n = _norm(path)
    # strip file:// and resolve-ish absolute → find workspace/...
    for marker in PROTECTED_DIR_NAMES:
        idx = n.find(marker)
        if idx < 0:
            continue
        rest = n[idx:]
        # exact dir or under it
        if rest == marker or rest.startswith(marker + "/"):
            return True
    for root in PROTECTED_ROOTS:
        if root in n:
            return True
    return False


def _paths_from_tool_input(tool_input: dict) -> list[str]:
    keys = (
        "path",
        "target_notebook",
        "file_path",
        "filePath",
        "target",
    )
    out: list[str] = []
    for k in keys:
        v = tool_input.get(k)
        if isinstance(v, str):
            out.append(v)
    # Write sometimes nested; Delete uses path
    files = tool_input.get("files")
    if isinstance(files, list):
        for f in files:
            if isinstance(f, str):
                out.append(f)
            elif isinstance(f, dict) and isinstance(f.get("path"), str):
                out.append(f["path"])
    return out


def _shell_touches_protected_write(command: str) -> bool:
    if not command:
        return False
    ncmd = _norm(command)
    if not any(m in ncmd for m in PROTECTED_DIR_NAMES):
        return False
    # Legitimate: qr --csv <events> (read-only ingest)
    # Still block if also has write markers / redirect into events.
    for m in REDIRECT_TO_RE.finditer(command):
        if _is_protected_path(m.group(1)):
            return True
    if SHELL_WRITE_RE.search(command):
        return True
    return False


def _deny(msg: str) -> dict:
    return {
        "permission": "deny",
        "user_message": msg,
        "agent_message": msg,
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print(json.dumps({"permission": "allow"}))
        return 0

    # beforeShellExecution uses "command"; preToolUse uses tool_name + tool_input
    command = payload.get("command")
    if isinstance(command, str) and command.strip():
        if _shell_touches_protected_write(command):
            print(
                json.dumps(
                    _deny(
                        "Blocked: must not modify original event data under "
                        "workspace/events/ or workspace/events_ascii/. "
                        "Use --csv to read; derived outputs go to workspace/runs/."
                    )
                )
            )
            return 0
        print(json.dumps({"permission": "allow"}))
        return 0

    tool = str(payload.get("tool_name") or payload.get("toolName") or "")
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    # Edit tools
    write_tools = {"Write", "StrReplace", "Delete", "EditNotebook", "Edit", "WriteFile"}
    if tool in write_tools or tool.endswith("Write") or tool.endswith("Edit"):
        for p in _paths_from_tool_input(tool_input):
            if _is_protected_path(p):
                print(
                    json.dumps(
                        _deny(
                            f"Blocked: refusing to modify protected event file: {p}. "
                            "Original events are read-only; write derivatives under workspace/runs/."
                        )
                    )
                )
                return 0

    print(json.dumps({"permission": "allow"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
