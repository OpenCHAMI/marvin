import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from openchami_coding_agent.execution import (
    _maybe_refresh_hierarchical_subplans,
    execute_plan,
    extract_repo_sequence_from_plan,
    marvin_plan_step_detail,
    normalize_next_step_index,
    resolve_repo_execution_order,
    select_execution_agent_class,
    summarize_token_events,
    topological_order,
)
from openchami_coding_agent.models import AgentConfig, InvocationCapture, RepoSpec, RunTrace, RunTraceEvent
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


def test_resolve_repo_execution_order_excludes_read_only_repos() -> None:
    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="test",
        repos=[
            RepoSpec(name="reference", path=Path("/tmp/reference"), read_only=True),
            RepoSpec(name="service", path=Path("/tmp/service")),
            RepoSpec(name="worker", path=Path("/tmp/worker")),
        ],
        repo_order=["worker", "service"],
    )

    ordered = resolve_repo_execution_order(cfg, plan_markdown="1. Review reference\n2. Update worker")

    assert [repo.name for repo in ordered] == ["worker", "service"]


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
        repos=[
            RepoSpec(name="reference", path=Path("/tmp/reference"), read_only=True),
            RepoSpec(name="go", path=Path("/tmp/go"), language="go"),
        ],
        execution={"executor_agent": "auto"},
    )
    assert select_execution_agent_class(cfg).__name__ == "GitGoAgent"


def test_select_execution_agent_class_rejects_reference_only_configs() -> None:
    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="test",
        repos=[RepoSpec(name="ref", path=Path("/tmp/ref"), read_only=True)],
    )

    with pytest.raises(ValueError, match="writable repo"):
        select_execution_agent_class(cfg)


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
                "cached_input_tokens": 18,
                "output_tokens": 10,
                "total_tokens": 40,
            },
            {
                "stage": "execution",
                "prompt_chars": 60,
                "prompt_estimated_tokens": 15,
                "input_tokens": 12,
                "cached_input_tokens": 4,
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
        "cached_input_tokens": 22,
        "uncached_input_tokens": 20,
        "cache_hit_ratio": 0.5238,
        "output_tokens": 14,
        "total_tokens": 56,
    }
    assert summary["repair"]["count"] == 1


def test_run_trace_round_trip_preserves_event_structure() -> None:
    trace = RunTrace(
        planning_mode="hierarchical",
        events=[
            RunTraceEvent(
                stage="execution",
                event_type="step_completed",
                status="completed",
                title="Update service config",
                detail="Applied the config fix.",
                main_step=2,
                total_main_steps=4,
                sub_step=1,
                total_sub_steps=2,
                affected_repos=["svc"],
                token_usage={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
                metadata={"summary": "Applied the config fix."},
            )
        ],
    )

    restored = RunTrace.from_payload(trace.to_payload())

    assert restored == trace


def test_maybe_refresh_hierarchical_subplans_clears_pending_entries_from_partial_success() -> None:
    refreshed, reasons = _maybe_refresh_hierarchical_subplans(
        {
            "main_next_index": 1,
            "subplans": {"0": {"steps": []}, "1": {"steps": []}, "2": {"steps": []}},
        },
        total_main_steps=3,
        partial_success={"resume_replan_scope": "pending"},
        operator_feedback_text="",
    )

    assert reasons == ["partial-success artifact recommends refreshing pending subplans"]
    assert refreshed["subplans"] == {"0": {"steps": []}}


def test_maybe_refresh_hierarchical_subplans_refreshes_only_current_subplan_when_requested() -> None:
    refreshed, reasons = _maybe_refresh_hierarchical_subplans(
        {
            "main_next_index": 1,
            "subplans": {"0": {"steps": []}, "1": {"steps": []}, "2": {"steps": []}},
        },
        total_main_steps=4,
        partial_success={"resume_replan_scope": "current"},
        operator_feedback_text="",
    )

    assert reasons == ["partial-success artifact recommends refreshing the current subplan"]
    assert refreshed["subplans"] == {"0": {"steps": []}, "2": {"steps": []}}


def test_maybe_refresh_hierarchical_subplans_respects_operator_feedback_request() -> None:
    refreshed, reasons = _maybe_refresh_hierarchical_subplans(
        {
            "main_next_index": 2,
            "subplans": {"0": {"steps": []}, "1": {"steps": []}, "2": {"steps": []}},
        },
        total_main_steps=4,
        partial_success={},
        operator_feedback_text="# Marvin Operator Feedback\nrefresh_subplans: current\n",
    )

    assert reasons == ["operator feedback requested current-subplan refresh"]
    assert refreshed["subplans"] == {"0": {"steps": []}, "1": {"steps": []}}


def test_execute_plan_persists_structured_run_trace(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo_path = workspace / "svc"
    repo_path.mkdir()

    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="Update the service implementation.",
        workspace=workspace,
        repos=[RepoSpec(name="svc", path=repo_path)],
        commit_each_step=False,
    )

    dummy_agent = SimpleNamespace(
        telemetry=SimpleNamespace(llm=SimpleNamespace(samples=[])),
        thread_id=None,
    )
    invoked_prompts: list[str] = []

    (workspace / cfg.operator_feedback_markdown).parent.mkdir(parents=True, exist_ok=True)
    (workspace / cfg.operator_feedback_markdown).write_text(
        "# Marvin Operator Feedback\nrefresh_subplans: no\n\n## Notes For Next Repair Or Resume\n- Use the local svc fixtures before rerunning checks.\n",
        encoding="utf-8",
    )

    @contextmanager
    def no_heartbeat(*args, **kwargs):
        yield lambda *a, **k: None

    monkeypatch.setattr(
        "openchami_coding_agent.execution.select_execution_agent_class",
        lambda cfg: type("ExecutionAgent", (), {}),
    )
    monkeypatch.setattr(
        "openchami_coding_agent.execution.instantiate_agent",
        lambda *args, **kwargs: dummy_agent,
    )
    monkeypatch.setattr(
        "openchami_coding_agent.execution.build_executor_prompt",
        lambda *args, **kwargs: "base prompt",
    )
    monkeypatch.setattr(
        "openchami_coding_agent.execution.invoke_agent",
        lambda *args, **kwargs: (
            invoked_prompts.append(args[1])
            or InvocationCapture(content="Updated the service implementation.")
        ),
    )
    monkeypatch.setattr("openchami_coding_agent.execution.hash_plan", lambda payload: "plan-123")
    monkeypatch.setattr("openchami_coding_agent.execution.emit_panel", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "openchami_coding_agent.execution.render_check_status",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "openchami_coding_agent.execution.render_run_progress",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "openchami_coding_agent.execution.update_tracker_markdown",
        lambda **kwargs: workspace / "plan" / "marvin.md",
    )
    monkeypatch.setattr("openchami_coding_agent.execution.progress_heartbeat", no_heartbeat)
    monkeypatch.setattr(
        "openchami_coding_agent.execution.snapshot_sqlite_db",
        lambda *args, **kwargs: None,
    )

    payload = execute_plan(
        cfg,
        "1. Update the service implementation.",
        executor_llm=object(),
    )

    trace = payload["run_trace"]
    event_types = [event["event_type"] for event in trace["events"]]

    assert trace["planning_mode"] == "single"
    assert event_types == ["run_started", "step_completed", "run_completed"]
    assert trace["events"][1]["title"] == "Update the service implementation"
    assert invoked_prompts
    assert "Operator feedback for this resume cycle:" in invoked_prompts[0]
    assert "Use the local svc fixtures before rerunning checks." in invoked_prompts[0]

    progress_payload = json.loads((workspace / cfg.executor_progress_json).read_text(encoding="utf-8"))
    assert progress_payload["run_trace"]["events"][-1]["event_type"] == "run_completed"
