from pathlib import Path

from openchami_coding_agent.models import ProgressSnapshot
from openchami_coding_agent.tui import (
    _completion_personality_line,
    _token_observation,
    build_commentary_entry,
    build_commentary_tabs,
    build_completion_summary_text,
    build_marvin_commentary_from_progress,
    build_operational_context,
    build_token_report_text,
    format_commentary_log_entry,
    nudge_body_split,
    raw_commentary_log_path,
    token_hotspot_lines,
    token_stage_report_lines,
)


def test_token_stage_report_lines_formats_known_stages() -> None:
    lines = token_stage_report_lines(
        {
            "token_usage_by_stage": {
                "execution": {
                    "count": 3,
                    "prompt_estimated_tokens": 1200,
                    "input_tokens": 1800,
                    "output_tokens": 400,
                    "total_tokens": 2200,
                },
                "repair": {
                    "count": 1,
                    "prompt_estimated_tokens": 300,
                    "input_tokens": 500,
                    "output_tokens": 100,
                    "total_tokens": 600,
                },
            }
        }
    )

    assert lines[0].startswith("- Executing: calls=3")
    assert "prompt~1200" in lines[0]
    assert any("Repairing: calls=1" in line for line in lines)


def test_token_hotspot_lines_sorts_by_total_tokens_descending() -> None:
    lines = token_hotspot_lines(
        {
            "token_events": [
                {
                    "stage": "execution",
                    "label": "smaller",
                    "prompt_estimated_tokens": 200,
                    "total_tokens": 300,
                },
                {
                    "stage": "repair",
                    "label": "largest",
                    "repo": "svc",
                    "prompt_estimated_tokens": 600,
                    "total_tokens": 900,
                },
            ]
        }
    )

    assert lines[0].startswith("1. Repairing: largest")
    assert "repo=svc" in lines[0]
    assert lines[1].startswith("2. Executing: smaller")


def test_build_completion_summary_text_includes_stage_rollups_and_hotspots() -> None:
    text = build_completion_summary_text(
        "marvin-ws",
        {
            "completed_repos": ["svc"],
            "failed_repos": [],
            "token_usage": {
                "input_tokens": 1000,
                "output_tokens": 250,
                "total_tokens": 1250,
            },
            "duration_sec": 42,
            "summary": "Applied the change and ran tests.",
            "token_usage_by_stage": {
                "execution": {
                    "count": 2,
                    "prompt_estimated_tokens": 500,
                    "input_tokens": 900,
                    "output_tokens": 200,
                    "total_tokens": 1100,
                }
            },
            "token_events": [
                {
                    "stage": "execution",
                    "label": "step 1/2",
                    "prompt_estimated_tokens": 300,
                    "total_tokens": 700,
                }
            ],
        },
    )

    assert "Token stage breakdown:" in text
    assert "Highest-cost invocations:" in text
    assert "Executing: calls=2" in text
    assert "1. Executing: step 1/2" in text


def test_build_completion_summary_text_uses_failure_aware_tone() -> None:
    text = build_completion_summary_text(
        "marvin-ws",
        {
            "failed_repos": ["svc"],
            "token_usage": {"total_tokens": 500},
        },
    )

    assert "technical sense" in text


def test_build_token_report_text_shows_live_and_summary_sections() -> None:
    text = build_token_report_text(
        workspace_name="marvin-ws",
        stage="execution",
        planning_mode="hierarchical",
        current_step="main 2/4 | sub 1/2 | repo svc",
        current_repo="svc",
        checkpoint_label="executor_checkpoint_2_1.db",
        repo_progress="1/3",
        failures=0,
        retries=1,
        token_usage={"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500},
        token_delta_usage={"input_tokens": 200, "output_tokens": 40, "total_tokens": 240},
        elapsed_sec=15,
        payload={
            "token_usage_by_stage": {
                "execution": {
                    "count": 2,
                    "prompt_estimated_tokens": 450,
                    "input_tokens": 800,
                    "output_tokens": 220,
                    "total_tokens": 1020,
                }
            },
            "token_events": [
                {
                    "stage": "execution",
                    "label": "step 2/4",
                    "prompt_estimated_tokens": 250,
                    "total_tokens": 500,
                }
            ],
        },
    )

    assert "Token Report" in text
    assert "Stage: Executing    Planning: hierarchical" in text
    assert "Last delta sent/received/total: 200/40/240" in text
    assert "Per-stage rollups:" in text
    assert "Highest-cost invocations:" in text
    assert "Observation:" in text


def test_token_observation_notices_repair_heaviness() -> None:
    observation = _token_observation(
        stage="repair",
        token_usage={"total_tokens": 9000},
        token_delta_usage={"total_tokens": 400},
        payload={
            "token_usage_by_stage": {
                "execution": {"total_tokens": 1000},
                "repair": {"total_tokens": 1500},
            }
        },
    )

    assert "repair work is consuming as much thought as delivery" in observation


def test_completion_personality_line_varies_with_token_cost() -> None:
    line = _completion_personality_line(
        {"failed_repos": [], "token_usage": {"total_tokens": 22000}}
    )
    assert "extravagant amount of cognition" in line


def test_build_marvin_commentary_from_progress_includes_focus_and_pressure() -> None:
    text = build_marvin_commentary_from_progress(
        ProgressSnapshot(
            stage="repair",
            detail="Repairing validation fallout",
            planning_mode="hierarchical",
            current_main_step=2,
            current_main_total=4,
            current_sub_step=1,
            current_sub_total=3,
            current_repo="svc",
            checkpoint_label="executor_checkpoint_2_1.db",
            token_usage={"total_tokens": 12000},
            failed_repos=1,
            retries=2,
        ),
        0,
    )

    assert "Current focus:" in text
    assert "repo svc" in text
    assert "repo failure(s) are currently objecting" in text


def test_build_commentary_tabs_prioritize_raw_agent_signal() -> None:
    tabs = build_commentary_tabs(
        ProgressSnapshot(
            stage="execution",
            detail="Reading files and preparing edits",
            base_detail="Executing main step 2/4 sub-step 1/2",
            agent_feedback="Inspecting config_init.py and updating prompt payload construction.",
            current_main_step=2,
            current_main_total=4,
            current_sub_step=1,
            current_sub_total=2,
            current_repo="svc",
        ),
        0,
    )

    assert tabs["raw"].startswith("Inspecting config_init.py")
    assert tabs["marvin"].startswith("I am executing")
    assert "Focus: main 2/4, sub 1/2, repo svc" in tabs["context"]


def test_build_commentary_tabs_preserve_full_raw_commentary() -> None:
    long_feedback = "Inspect token interfaces. " * 30

    tabs = build_commentary_tabs(
        ProgressSnapshot(
            stage="execution",
            detail="Reading files and preparing edits",
            agent_feedback=long_feedback,
        ),
        0,
    )

    assert tabs["raw"] == long_feedback.strip()


def test_build_operational_context_uses_base_detail_before_runtime_detail() -> None:
    text = build_operational_context(
        ProgressSnapshot(
            stage="planning",
            detail="Generating markdown summary",
            base_detail="Planner is consolidating repo goals",
            token_usage={"total_tokens": 9000},
        )
    )

    assert "Planner is consolidating repo goals" in text
    assert "Generating markdown summary" in text
    assert "token usage is climbing" in text


def test_build_commentary_entry_combines_tabs_without_prefix_labels() -> None:
    text = build_commentary_entry(
        ProgressSnapshot(
            stage="execution",
            detail="Reading files and preparing edits",
            base_detail="Executing main step 2/4 sub-step 1/2",
            agent_feedback="Inspecting config_init.py and updating prompt payload construction.",
            current_main_step=2,
            current_main_total=4,
            current_sub_step=1,
            current_sub_total=2,
            current_repo="svc",
        ),
        0,
    )

    assert "Marvin:" not in text
    assert "Agent signal:" not in text
    assert "Inspecting config_init.py" in text


def test_nudge_body_split_applies_requested_delta() -> None:
    assert nudge_body_split(39.0, 4.0) == 43.0
    assert nudge_body_split(39.0, -4.0) == 35.0


def test_raw_commentary_log_path_derives_from_summary_artifact() -> None:
    path = raw_commentary_log_path(
        Path("/tmp/marvin-workspace"),
        "artifacts/example-summary.json",
    )

    assert path is not None
    assert path.name == "example-raw-commentary.log"
    assert path.parent.name == "artifacts"


def test_format_commentary_log_entry_includes_timestamp_and_text() -> None:
    entry = format_commentary_log_entry(
        "Inspecting repository state before editing.",
        timestamp="2026-03-27 12:34:56",
    )

    assert entry == "[2026-03-27 12:34:56] Inspecting repository state before editing.\n"