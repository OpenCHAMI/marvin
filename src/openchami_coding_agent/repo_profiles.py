"""Declarative repository profile loading for OpenCHAMI-aware runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import AgentConfig


def _safe_load_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_repo_profiles(cfg: AgentConfig) -> dict[str, dict[str, Any]]:
    workspace = cfg.workspace
    if workspace is None:
        return {}

    profiles_dir = (workspace / cfg.repo_profiles_dir).resolve()
    if not profiles_dir.exists() or not profiles_dir.is_dir():
        return {}

    profiles: dict[str, dict[str, Any]] = {}
    for path in sorted(profiles_dir.glob("*.y*ml")):
        payload = _safe_load_yaml(path)
        name = str(payload.get("repo") or payload.get("name") or path.stem)
        if not name:
            continue
        payload["_path"] = str(path)
        profiles[name] = payload
    return profiles


def repo_profile_paths(cfg: AgentConfig) -> list[Path]:
    return [
        Path(payload["_path"])
        for payload in load_repo_profiles(cfg).values()
        if isinstance(payload.get("_path"), str)
    ]
