from pathlib import Path

from openchami_coding_agent.plan_tracking import (
    initialize_plan_artifacts,
    read_tracker_activity,
    tracker_markdown_path,
    update_tracker_markdown,
)


def test_initialize_plan_artifacts_creates_tracker_and_step_files(tmp_path: Path) -> None:
    plan = """
    1. Create API endpoint
    2. Add tests
    """
    steps = initialize_plan_artifacts(tmp_path, plan)
    assert steps == ["Create API endpoint", "Add tests"]

    tracker = tracker_markdown_path(tmp_path)
    assert tracker.exists()
    text = tracker.read_text(encoding="utf-8")
    assert "# Marvin Execution Tracker" in text
    assert "## Plan Step Files" in text

    plan_dir = tmp_path / "plan"
    created_steps = sorted(path.name for path in plan_dir.glob("step-*.md"))
    assert len(created_steps) == 2


def test_update_tracker_markdown_records_completed_and_remaining(tmp_path: Path) -> None:
    update_tracker_markdown(
        workspace=tmp_path,
        stage="validation",
        activity="Running checks",
        plan_steps=["one", "two", "three"],
        completed_step_indices={0, 1},
        notes=["step 1 done", "step 2 done"],
        reconciliation="Remaining repos: three",
    )

    tracker = tracker_markdown_path(tmp_path)
    text = tracker.read_text(encoding="utf-8")
    assert "- Stage: validation" in text
    assert "- [x] 1. one" in text
    assert "- [x] 2. two" in text
    assert "- [ ] 3. three" in text
    assert "Remaining repos: three" in text


def test_read_tracker_activity_extracts_current_activity(tmp_path: Path) -> None:
    update_tracker_markdown(
        workspace=tmp_path,
        stage="execution",
        activity="Applying step 2 of 4",
        plan_steps=["one", "two", "three", "four"],
        completed_step_indices={0},
        notes=["running"],
        reconciliation="in progress",
    )
    assert read_tracker_activity(tmp_path) == "Applying step 2 of 4"


def test_extract_plan_steps_from_json_payload() -> None:
    from openchami_coding_agent.plan_tracking import extract_plan_steps

    payload = (
        '{"steps": ['
        '{"name": "Recon"}, '
        '{"name": "Write proposal"}, '
        '{"name": "Implement exchange"}'
        ']}'
    )
    assert extract_plan_steps(payload) == ["Recon", "Write proposal", "Implement exchange"]