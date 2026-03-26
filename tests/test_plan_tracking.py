from pathlib import Path

from openchami_coding_agent.models import PlanStep
from openchami_coding_agent.plan_tracking import (
    initialize_plan_artifacts,
    plan_step_names,
    read_tracker_activity,
    structured_plan_from_data,
    structured_plan_from_markdown,
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


def test_structured_plan_from_data_normalizes_dict_payload() -> None:
    structured = structured_plan_from_data(
        {
            "source": "planner",
            "steps": [
                {
                    "name": "Recon repo layout",
                    "description": "Inspect modules and tests.",
                    "expected_outputs": ["file inventory"],
                    "success_criteria": ["all touched paths identified"],
                    "requires_code": False,
                }
            ],
        }
    )

    assert structured.source == "planner"
    assert plan_step_names(structured) == ["Recon repo layout"]
    assert structured.steps[0].expected_outputs == ["file inventory"]
    assert structured.steps[0].requires_code is False


def test_initialize_plan_artifacts_prefers_structured_plan_names(tmp_path: Path) -> None:
    steps = initialize_plan_artifacts(
        tmp_path,
        "1. Wrong markdown step",
        structured_plan=[
            PlanStep(name="Correct structured step one"),
            PlanStep(name="Correct structured step two"),
        ],
    )

    assert steps == ["Correct structured step one", "Correct structured step two"]
    tracker = tracker_markdown_path(tmp_path)
    text = tracker.read_text(encoding="utf-8")
    assert "Correct structured step one" in text
    assert "Wrong markdown step" not in text


def test_structured_plan_from_markdown_normalizes_numbered_steps() -> None:
    structured = structured_plan_from_markdown(
        """
        1. Inspect the repository layout
        2. Add focused tests
        """
    )

    assert structured.source == "markdown-normalized"
    assert plan_step_names(structured) == [
        "Inspect the repository layout",
        "Add focused tests",
    ]