import json
import sqlite3
from pathlib import Path

from openchami_coding_agent.checkpoints import (
    checkpoint_dir,
    list_executor_checkpoints,
    parse_snapshot_indices,
    resolve_resume_checkpoint,
    restore_executor_from_snapshot,
    sync_progress_for_snapshot_hierarchical,
    sync_progress_for_snapshot_single,
)


def test_parse_snapshot_indices() -> None:
    assert parse_snapshot_indices(Path("executor_checkpoint_5.db")) == (5, None)
    assert parse_snapshot_indices(Path("executor_checkpoint_3_2.db")) == (3, 2)
    assert parse_snapshot_indices(Path("executor_7.db")) == (7, None)
    assert parse_snapshot_indices(Path("executor_checkpoint.db")) == (None, None)


def test_list_executor_checkpoints_prefers_numbered_then_live(tmp_path: Path) -> None:
    ckpt = checkpoint_dir(tmp_path)
    (ckpt / "executor_checkpoint_2.db").write_text("a", encoding="utf-8")
    (ckpt / "executor_checkpoint_10_1.db").write_text("b", encoding="utf-8")
    (ckpt / "executor_checkpoint.db").write_text("live", encoding="utf-8")

    listed = list_executor_checkpoints(tmp_path)
    names = [p.name for p in listed]
    assert names[0] == "executor_checkpoint_2.db"
    assert names[1] == "executor_checkpoint_10_1.db"
    assert names[-1] == "executor_checkpoint.db"


def test_resolve_resume_checkpoint_explicit_and_default(tmp_path: Path) -> None:
    ckpt = checkpoint_dir(tmp_path)
    numbered = ckpt / "executor_checkpoint_4.db"
    numbered.write_text("checkpoint", encoding="utf-8")
    live = ckpt / "executor_checkpoint.db"
    live.write_text("live", encoding="utf-8")

    assert resolve_resume_checkpoint(tmp_path, "executor_checkpoint_4.db") == numbered
    assert resolve_resume_checkpoint(tmp_path, None) == live


def test_restore_executor_from_snapshot_copies_to_live(tmp_path: Path) -> None:
    ckpt = checkpoint_dir(tmp_path)
    src = ckpt / "executor_checkpoint_5.db"
    with sqlite3.connect(src) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t(v) VALUES ('checkpoint-data')")
        conn.commit()

    live = restore_executor_from_snapshot(tmp_path, src)
    assert live.exists()
    with sqlite3.connect(live) as conn:
        row = conn.execute("SELECT v FROM t").fetchone()
    assert row is not None
    assert row[0] == "checkpoint-data"


def test_sync_progress_for_snapshot_single_updates_progress_json(tmp_path: Path) -> None:
    snapshot = tmp_path / "executor_checkpoint_6.db"
    snapshot.write_text("db", encoding="utf-8")

    sync_progress_for_snapshot_single(
        workspace=tmp_path,
        snapshot=snapshot,
        plan_hash="abc123",
        progress_rel_path="artifacts/openchami_executor_progress.json",
    )

    payload = json.loads(
        (tmp_path / "artifacts" / "openchami_executor_progress.json").read_text(encoding="utf-8")
    )
    assert payload["next_index"] == 6
    assert payload["plan_hash"] == "abc123"
    assert "Resumed from snapshot" in payload["last_summary"]


def test_sync_progress_for_live_checkpoint_noop(tmp_path: Path) -> None:
    snapshot = tmp_path / "executor_checkpoint.db"
    snapshot.write_text("live", encoding="utf-8")

    sync_progress_for_snapshot_single(
        workspace=tmp_path,
        snapshot=snapshot,
        plan_hash="abc123",
        progress_rel_path="artifacts/openchami_executor_progress.json",
    )

    assert not (tmp_path / "artifacts" / "openchami_executor_progress.json").exists()


def test_sync_progress_for_snapshot_hierarchical_updates_substep_progress_json(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "executor_checkpoint_3_2.db"
    snapshot.write_text("db", encoding="utf-8")

    sync_progress_for_snapshot_hierarchical(
        workspace=tmp_path,
        snapshot=snapshot,
        plan_hash="abc123",
        progress_rel_path="artifacts/openchami_executor_progress.json",
    )

    payload = json.loads(
        (tmp_path / "artifacts" / "openchami_executor_progress.json").read_text(encoding="utf-8")
    )
    assert payload["planning_mode"] == "hierarchical"
    assert payload["plan_hash"] == "abc123"
    assert payload["main_next_index"] == 2
    assert payload["subplans"]["2"]["next_index"] == 2


def test_sync_progress_for_snapshot_hierarchical_main_checkpoint_advances_main_index(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "executor_checkpoint_4.db"
    snapshot.write_text("db", encoding="utf-8")

    sync_progress_for_snapshot_hierarchical(
        workspace=tmp_path,
        snapshot=snapshot,
        plan_hash="abc123",
        progress_rel_path="artifacts/openchami_executor_progress.json",
    )

    payload = json.loads(
        (tmp_path / "artifacts" / "openchami_executor_progress.json").read_text(encoding="utf-8")
    )
    assert payload["main_next_index"] == 4
    assert payload["subplans"] == {}
