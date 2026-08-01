from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from qresearch.config.models import AppSettings, ResearchConfig


def load_yaml(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config root must be mapping: {p}")
    return data


def load_research_config(path: str | Path | None = None, overrides: dict | None = None) -> ResearchConfig:
    data = load_yaml(path)
    if overrides:
        data = deep_merge(data, overrides)
    return ResearchConfig.model_validate(data)


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_settings() -> AppSettings:
    return AppSettings()


__all__ = [
    "AppSettings",
    "ResearchConfig",
    "load_research_config",
    "load_yaml",
    "get_settings",
    "deep_merge",
]
