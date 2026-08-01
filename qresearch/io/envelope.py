"""Agent-friendly CLI result envelope."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1
    CONFIG = 2
    DATA = 3
    BLOCKED = 4
    DEPENDENCY = 5


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ResultEnvelope(BaseModel):
    schema_version: str = "1.0"
    ok: bool = True
    command: str
    started_at: str
    finished_at: str
    elapsed_ms: int = 0
    run_id: str | None = None
    status: str = "succeeded"
    summary: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    next_actions: list[dict[str, Any]] = Field(default_factory=list)
    error: ErrorBody | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(
    envelope: ResultEnvelope,
    *,
    format: str = "json",
    quiet: bool = False,
    exit_code: int = ExitCode.OK,
) -> int:
    if format == "json":
        sys.stdout.write(envelope.model_dump_json(indent=2))
        sys.stdout.write("\n")
    else:
        if envelope.ok:
            print(f"[{envelope.status}] {envelope.command} run_id={envelope.run_id}")
            if envelope.summary and not quiet:
                for k, v in envelope.summary.items():
                    print(f"  {k}: {v}")
            if envelope.artifacts:
                print("artifacts:")
                for k, v in envelope.artifacts.items():
                    print(f"  {k}: {v}")
        else:
            err = envelope.error
            print(f"[failed] {envelope.command}: {err.message if err else 'unknown'}", file=sys.stderr)
    return int(exit_code)


def fail_envelope(
    command: str,
    started_at: str,
    *,
    code: str,
    message: str,
    exit_code: ExitCode,
    run_id: str | None = None,
    details: dict | None = None,
    artifacts: dict | None = None,
) -> tuple[ResultEnvelope, ExitCode]:
    finished = utc_now_iso()
    env = ResultEnvelope(
        ok=False,
        command=command,
        started_at=started_at,
        finished_at=finished,
        run_id=run_id,
        status="failed" if exit_code != ExitCode.BLOCKED else "blocked",
        artifacts=artifacts or {},
        error=ErrorBody(code=code, message=message, details=details),
    )
    return env, exit_code
