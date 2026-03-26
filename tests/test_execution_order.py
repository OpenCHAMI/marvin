from pathlib import Path

import pytest

from openchami_coding_agent.execution import (
    extract_repo_sequence_from_plan,
    marvin_plan_step_detail,
    normalize_next_step_index,
    resolve_repo_execution_order,
    select_execution_agent_class,
    summarize_token_events,
    topological_order,
)
from openchami_coding_agent.models import AgentConfig, RepoSpec
from openchami_coding_agent.plan_tracking import extract_plan_steps


def test_extract_repo_sequence_from_plan_uses_first_mention_order() -> None:
    plan = """
    Phase 1: update b
    Then work on A and finally c.
    """
    assert extract_repo_sequence_from_plan(plan, ["a", "b", "c"]) == ["b", "a", "c"]


def test_topological_order_applies_dependencies() -> None:
    ordered = topological_order(
        repo_names=["api", "lib", "docs"],
        dependencies={"api": ["lib"], "docs": ["api"]},
        preferred_order=["docs", "api", "lib"],
    )
    assert ordered == ["lib", "api", "docs"]


def test_topological_order_detects_cycle() -> None:
    with pytest.raises(ValueError, match="cycle"):
        topological_order(
            repo_names=["a", "b"],
            dependencies={"a": ["b"], "b": ["a"]},
            preferred_order=["a", "b"],
        )


def test_resolve_repo_execution_order_respects_repo_order_when_valid() -> None:
    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="test",
        repos=[
            RepoSpec(name="a", path=Path("/tmp/a")),
            RepoSpec(name="b", path=Path("/tmp/b")),
            RepoSpec(name="c", path=Path("/tmp/c")),
        ],
        repo_order=["c", "a", "b"],
        repo_dependencies={"a": ["b"]},
    )
    ordered = resolve_repo_execution_order(cfg, plan_markdown="")
    assert [repo.name for repo in ordered] == ["c", "b", "a"]


def test_extract_plan_steps_parses_numbered_and_checkbox_lines() -> None:
    plan = """
    # Plan
    1. Update CLI heartbeat detail output
    2) Add checkpoint-derived step detail
    - [ ] Validate with tests
    - plain note line
    """
    assert extract_plan_steps(plan) == [
        "Update CLI heartbeat detail output",
        "Add checkpoint-derived step detail",
        "Validate with tests",
        "plain note line",
    ]


def test_marvin_plan_step_detail_uses_checkpoint_step(tmp_path: Path) -> None:
    (tmp_path / "checkpoints").mkdir(parents=True, exist_ok=True)
    (tmp_path / "checkpoints" / "executor_checkpoint_2.db").write_text("x", encoding="utf-8")
    detail = marvin_plan_step_detail(
        plan_steps=["a", "b", "c", "d"],
        workspace=tmp_path,
    )
    assert "step 3/4" in detail
    assert "c" in detail


def test_marvin_plan_step_detail_falls_back_when_no_checkpoint(tmp_path: Path) -> None:
    detail = marvin_plan_step_detail(
        plan_steps=["first change", "second change"],
        workspace=tmp_path,
        fallback_step=0,
    )
    assert "step 1/2" in detail
    assert "first change" in detail


def test_normalize_next_step_index_accepts_zero_based_values() -> None:
    assert normalize_next_step_index(0, 5) == 0
    assert normalize_next_step_index(-1, 5) == 0


def test_normalize_next_step_index_accepts_one_based_values() -> None:
    assert normalize_next_step_index(1, 5) == 0
    assert normalize_next_step_index(5, 5) == 4


def test_select_execution_agent_class_defaults_to_execution_agent() -> None:
    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="test",
        repos=[RepoSpec(name="py", path=Path("/tmp/py"), language="python")],
    )
    assert select_execution_agent_class(cfg).__name__ == "ExecutionAgent"


def test_select_execution_agent_class_auto_selects_gitgo_for_go_repo() -> None:
    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="test",
        repos=[RepoSpec(name="go", path=Path("/tmp/go"), language="go")],
        execution={"executor_agent": "auto"},
    )
    assert select_execution_agent_class(cfg).__name__ == "GitGoAgent"


def test_select_execution_agent_class_respects_explicit_value() -> None:
    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="test",
        repos=[RepoSpec(name="go", path=Path("/tmp/go"), language="go")],
        execution={"executor_agent": "execution"},
    )
    assert select_execution_agent_class(cfg).__name__ == "ExecutionAgent"


def test_select_execution_agent_class_rejects_unknown_value() -> None:
    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="test",
        repos=[RepoSpec(name="x", path=Path("/tmp/x"), language="generic")],
        execution={"executor_agent": "mystery"},
    )
    with pytest.raises(ValueError, match="Unknown execution.executor_agent"):
        select_execution_agent_class(cfg)


def test_summarize_token_events_aggregates_by_stage() -> None:
    summary = summarize_token_events(
        [
            {
                "stage": "execution",
                "prompt_chars": 100,
                "prompt_estimated_tokens": 25,
                "input_tokens": 30,
                "output_tokens": 10,
                "total_tokens": 40,
            },
            {
                "stage": "execution",
                "prompt_chars": 60,
                "prompt_estimated_tokens": 15,
                "input_tokens": 12,
                "output_tokens": 4,
                "total_tokens": 16,
            },
            {
                "stage": "repair",
                "prompt_chars": 80,
                "prompt_estimated_tokens": 20,
                "input_tokens": 14,
                "output_tokens": 6,
                "total_tokens": 20,
            },
        ]
    )

    assert summary["execution"] == {
        "count": 2,
        "prompt_chars": 160,
        "prompt_estimated_tokens": 40,
        "input_tokens": 42,
        "output_tokens": 14,
        "total_tokens": 56,
    }
    assert summary["repair"]["count"] == 1
