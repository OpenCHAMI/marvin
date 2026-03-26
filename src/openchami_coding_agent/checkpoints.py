"""Checkpoint discovery, restore, and resume helpers for Marvin."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .ursa_compat import snapshot_sqlite_db


def checkpoint_dir(workspace: Path) -> Path:
    directory = workspace / "checkpoints"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def parse_snapshot_indices(path: Path) -> tuple[int | None, int | None]:
    match = re.match(r"(?:executor|executor_checkpoint)_(\d+)(?:_(\d+))?\.db$", path.name)
    if not match:
        return None, None
    main_step = int(match.group(1))
    sub_step = int(match.group(2)) if match.group(2) else None
    return main_step, sub_step


def _checkpoint_sort_key(path: Path) -> tuple[int, float, float, str]:
    match = re.match(r"executor_checkpoint_(\d+)(?:_(\d+))?\.db$", path.name)
    if match:
        main_step = int(match.group(1))
        sub_step = int(match.group(2) or 0)
        return (0, float(main_step), float(sub_step), path.name)
    if path.name == "executor_checkpoint.db":
        return (1, float("inf"), float("inf"), path.name)
    return (2, float("inf"), float("inf"), path.name)


def list_executor_checkpoints(workspace: Path) -> list[Path]:
    ckpt_dir = checkpoint_dir(workspace)
    seen: dict[Path, Path] = {}
    for base in (ckpt_dir, workspace):
        for pattern in ("executor_checkpoint_*.db", "executor_checkpoint.db"):
            for path in base.glob(pattern):
                seen[path.resolve()] = path
    return sorted(seen.values(), key=_checkpoint_sort_key)


def resolve_resume_checkpoint(workspace: Path, resume_from: str | None) -> Path | None:
    if resume_from:
        user_path = Path(resume_from)
        if not user_path.is_absolute():
            ckpt_candidate = checkpoint_dir(workspace) / user_path
            if ckpt_candidate.exists():
                return ckpt_candidate
            user_path = workspace / user_path
        if user_path.exists():
            return user_path
        return None

    default_live = checkpoint_dir(workspace) / "executor_checkpoint.db"
    if default_live.exists():
        return default_live

    checkpoints = list_executor_checkpoints(workspace)
    return checkpoints[-1] if checkpoints else None


def restore_executor_from_snapshot(workspace: Path, snapshot: Path) -> Path:
    live_checkpoint = checkpoint_dir(workspace) / "executor_checkpoint.db"
    if not snapshot.exists():
        return live_checkpoint
    if snapshot.resolve() == live_checkpoint.resolve():
        return live_checkpoint

    snapshot_sqlite_db(snapshot, live_checkpoint)
    for suffix in ("-wal", "-shm"):
        sidecar = live_checkpoint.with_name(live_checkpoint.name + suffix)
        if sidecar.exists():
            sidecar.unlink()
    return live_checkpoint


def sync_progress_for_snapshot_single(
    workspace: Path,
    snapshot: Path,
    plan_hash: str,
    progress_rel_path: str,
) -> None:
    step_index, _ = parse_snapshot_indices(snapshot)
    if not step_index:
        return

    progress_path = (workspace / progress_rel_path).resolve()
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "next_index": int(step_index),
        "plan_hash": str(plan_hash),
        "last_summary": f"Resumed from snapshot {snapshot.name}",
    }
    progress_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
