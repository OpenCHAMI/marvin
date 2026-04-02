from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from openchami_coding_agent.models import AgentConfig, InvocationCapture, RepoSpec
from openchami_coding_agent.pipeline import run_pipeline


def _dummy_agent() -> object:
    return SimpleNamespace(
        telemetry=SimpleNamespace(llm=SimpleNamespace(samples=[])),
        thread_id=None,
    )


def test_run_pipeline_analyze_workspace_writes_analysis_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "artifacts").mkdir()
    (workspace / "proposal.md").write_text("Initial proposal", encoding="utf-8")
    (workspace / "artifacts" / "marvin_execution_summary.json").write_text(
        '{"failed_repos": ["svc"], "all_checks_passed": false}',
        encoding="utf-8",
    )

    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="Inspect a failed run.",
        mode="analyze_workspace",
        raw_config={
            "project": "OpenCHAMI",
            "problem": "Inspect a failed run.",
            "mode": "analyze_workspace",
            "workspace": "workspace",
            "execution": {"max_check_retries": 1},
        },
        workspace=workspace,
        planner_model="openai:gpt-5.4",
        repos=[RepoSpec(name="svc", path=workspace / "repos" / "svc")],
    )

    responses = iter(
        [
            InvocationCapture(
                content=(
                    "## Workspace Assessment\nIssue found.\n\n"
                    "## Failure Signals\n- checks failed\n\n"
                    "## Recommended YAML Updates\n- increase retries\n\n"
                    "## Suggested YAML Snippet\n```yaml\nexecution:\n  max_check_retries: 2\n  skip_failed_repos: true\n```\n\n"
                    "## Suggested Operator Feedback\n- none\n\n"
                    "## Clarifications Needed\n1. Should the run stay interactive?"
                )
            ),
            InvocationCapture(
                content=(
                    "## Workspace Assessment\nIssue confirmed.\n\n"
                    "## Failure Signals\n- checks failed\n\n"
                    "## Recommended YAML Updates\n- increase retries\n\n"
                    "## Suggested YAML Snippet\n```yaml\nexecution:\n  max_check_retries: 2\n  skip_failed_repos: true\n```\n\n"
                    "## Suggested Operator Feedback\n```markdown\n# Marvin Operator Feedback\n\nworkspace: workspace\nrefresh_subplans: current\n\n## Notes For Next Repair Or Resume\n- Confirm the svc fixture path before rerunning validation.\n```\n\n"
                    "## Clarifications Needed\n- none"
                )
            ),
        ]
    )

    monkeypatch.setattr("openchami_coding_agent.pipeline.make_agent_llm", lambda cfg, role: object())
    monkeypatch.setattr("openchami_coding_agent.pipeline.get_agent_class", lambda name: object)
    monkeypatch.setattr("openchami_coding_agent.pipeline.instantiate_agent", lambda *args, **kwargs: _dummy_agent())
    monkeypatch.setattr("openchami_coding_agent.pipeline.invoke_agent", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr("builtins.input", lambda prompt="": "No, keep it non-interactive.")
    monkeypatch.setattr(
        "openchami_coding_agent.pipeline.ensure_repo",
        lambda repo: (_ for _ in ()).throw(AssertionError("ensure_repo should not run in analysis mode")),
    )

    assert run_pipeline(cfg) == 0

    analysis_md = (workspace / cfg.workspace_analysis_markdown).read_text(encoding="utf-8")
    analysis_json = (workspace / cfg.workspace_analysis_json).read_text(encoding="utf-8")
    recommended_yaml = (workspace / cfg.recommended_config_yaml).read_text(encoding="utf-8")
    recommended_operator_feedback = (
        workspace / cfg.recommended_operator_feedback_markdown
    ).read_text(encoding="utf-8")
    source_yaml = (workspace / "artifacts" / "marvin_source_config.yaml").read_text(encoding="utf-8")

    assert "Issue confirmed." in analysis_md
    assert "max_check_retries: 2" in analysis_md
    assert "No, keep it non-interactive." in analysis_json
    assert "skip_failed_repos: true" in recommended_yaml
    assert "max_check_retries: 2" in recommended_yaml
    assert "refresh_subplans: current" in recommended_operator_feedback
    assert "Confirm the svc fixture path" in recommended_operator_feedback
    assert "raw_config:" in source_yaml


def test_run_pipeline_analyze_workspace_skips_clarifications_in_non_interactive_mode(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="Inspect a failed run.",
        mode="analyze_workspace",
        raw_config={
            "project": "OpenCHAMI",
            "problem": "Inspect a failed run.",
            "mode": "analyze_workspace",
        },
        workspace=workspace,
        planner_model="openai:gpt-5.4",
        allow_user_prompts=False,
    )

    monkeypatch.setattr("openchami_coding_agent.pipeline.make_agent_llm", lambda cfg, role: object())
    monkeypatch.setattr("openchami_coding_agent.pipeline.get_agent_class", lambda name: object)
    monkeypatch.setattr("openchami_coding_agent.pipeline.instantiate_agent", lambda *args, **kwargs: _dummy_agent())
    monkeypatch.setattr(
        "openchami_coding_agent.pipeline.invoke_agent",
        lambda *args, **kwargs: InvocationCapture(
            content=(
                "## Workspace Assessment\nIssue found.\n\n"
                "## Failure Signals\n- checks failed\n\n"
                "## Recommended YAML Updates\n- increase retries\n\n"
                "## Suggested YAML Snippet\n```yaml\nexecution:\n  max_check_retries: 2\n```\n\n"
                "## Suggested Operator Feedback\n- none\n\n"
                "## Clarifications Needed\n1. Should the run stay interactive?"
            )
        ),
    )

    assert run_pipeline(cfg) == 0
    analysis_json = (workspace / cfg.workspace_analysis_json).read_text(encoding="utf-8")
    recommended_yaml = (workspace / cfg.recommended_config_yaml).read_text(encoding="utf-8")
    assert "Should the run stay interactive?" in analysis_json
    assert "max_check_retries: 2" in recommended_yaml


def test_run_pipeline_analyze_workspace_includes_structured_run_trace_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "artifacts").mkdir()
    (workspace / "proposal.md").write_text("Initial proposal", encoding="utf-8")
    (workspace / "artifacts" / "marvin_execution_summary.json").write_text(
                (
                        '{"failed_repos": ["svc"], "all_checks_passed": false, '
                        '"run_trace": {"planning_mode": "single", "events": ['
                        '{"stage": "execution", "event_type": "step_completed", '
                        '"status": "completed", "title": "Update svc config", '
                        '"detail": "Applied the service config fix."}]}}'
                ),
        encoding="utf-8",
    )
    (workspace / "artifacts" / "marvin_operator_feedback.md").write_text(
        "# Marvin Operator Feedback\nrefresh_subplans: yes\n\n## Notes For Next Repair Or Resume\n- The svc repo needs local fixtures before resume.\n",
        encoding="utf-8",
    )

    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="Inspect a failed run.",
        mode="analyze_workspace",
        raw_config={
            "project": "OpenCHAMI",
            "problem": "Inspect a failed run.",
            "mode": "analyze_workspace",
        },
        workspace=workspace,
        planner_model="openai:gpt-5.4",
        repos=[RepoSpec(name="svc", path=workspace / "repos" / "svc")],
    )

    captured_prompts: list[str] = []

    def fake_invoke_agent(*args, **kwargs):
        captured_prompts.append(args[1])
        return InvocationCapture(
            content=(
                "## Workspace Assessment\nIssue found.\n\n"
                "## Failure Signals\n- checks failed\n\n"
                "## Recommended YAML Updates\n- increase retries\n\n"
                "## Suggested YAML Snippet\n```yaml\nexecution:\n  max_check_retries: 2\n```\n\n"
                "## Suggested Operator Feedback\n```markdown\n# Marvin Operator Feedback\n\nworkspace: workspace\nrefresh_subplans: yes\n\n## Config Adjustments To Try\n- Keep the svc fixture note in the next repair cycle.\n```\n\n"
                "## Clarifications Needed\n- none"
            )
        )

    monkeypatch.setattr("openchami_coding_agent.pipeline.make_agent_llm", lambda cfg, role: object())
    monkeypatch.setattr("openchami_coding_agent.pipeline.get_agent_class", lambda name: object)
    monkeypatch.setattr("openchami_coding_agent.pipeline.instantiate_agent", lambda *args, **kwargs: _dummy_agent())
    monkeypatch.setattr("openchami_coding_agent.pipeline.invoke_agent", fake_invoke_agent)
    monkeypatch.setattr(
        "openchami_coding_agent.pipeline.ensure_repo",
        lambda repo: (_ for _ in ()).throw(AssertionError("ensure_repo should not run in analysis mode")),
    )

    assert run_pipeline(cfg) == 0

    assert captured_prompts
    assert "Structured run trace:" in captured_prompts[0]
    assert "Partial-success artifact:" in captured_prompts[0]
    assert "Operator feedback artifact:" in captured_prompts[0]
    assert "The svc repo needs local fixtures before resume." in captured_prompts[0]
    assert "Update svc config" in captured_prompts[0]
    recommended_operator_feedback = (
        workspace / cfg.recommended_operator_feedback_markdown
    ).read_text(encoding="utf-8")
    assert "refresh_subplans: yes" in recommended_operator_feedback


def test_run_pipeline_writes_partial_success_artifact_for_execution(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo_path = workspace / "svc"
    repo_path.mkdir()
    (workspace / "proposal.md").write_text("1. Update svc\n", encoding="utf-8")

    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="Execute the prepared plan.",
        mode="execute",
        raw_config={
            "project": "OpenCHAMI",
            "problem": "Execute the prepared plan.",
            "mode": "execute",
        },
        workspace=workspace,
        planner_model="openai:gpt-5.4",
        executor_model="openai:gpt-5.4",
        repos=[RepoSpec(name="svc", path=repo_path)],
    )

    summary_payload = {
        "project": "OpenCHAMI",
        "workspace": str(workspace),
        "planning_mode": "single",
        "summary": "Updated svc but checks still fail.",
        "completed_repos": ["svc"],
        "failed_repos": ["svc"],
        "all_checks_passed": False,
        "token_usage": {"input_tokens": 100, "output_tokens": 25, "total_tokens": 125},
        "token_cache_summary": {
            "input_tokens": 100,
            "cached_input_tokens": 50,
            "uncached_input_tokens": 50,
            "cache_hit_ratio": 0.5,
        },
        "token_events": [],
        "token_usage_by_stage": {},
        "duration_sec": 12,
        "run_trace": {
            "planning_mode": "single",
            "events": [
                {
                    "stage": "execution",
                    "event_type": "step_completed",
                    "status": "completed",
                    "title": "Update svc",
                    "detail": "Applied the change.",
                },
                {
                    "stage": "validation",
                    "event_type": "validation_attempt_completed",
                    "status": "failed",
                    "title": "Validation attempt 1",
                    "detail": "0 repos passed and 1 repo failed.",
                    "metadata": {"failed_repos": ["svc"]},
                },
            ],
        },
    }

    monkeypatch.setattr("openchami_coding_agent.pipeline.ensure_repo", lambda repo: None)
    monkeypatch.setattr("openchami_coding_agent.pipeline.emit_panel", lambda *args, **kwargs: None)
    monkeypatch.setattr("openchami_coding_agent.pipeline.emit_text", lambda *args, **kwargs: None)
    monkeypatch.setattr("openchami_coding_agent.pipeline.render_status", lambda cfg: None)
    monkeypatch.setattr("openchami_coding_agent.pipeline.make_agent_llm", lambda cfg, role: object())
    monkeypatch.setattr("openchami_coding_agent.pipeline.execute_plan", lambda *args, **kwargs: summary_payload)

    assert run_pipeline(cfg) == 1

    partial_success = json.loads(
        (workspace / cfg.partial_success_json).read_text(encoding="utf-8")
    )
    operator_feedback = (workspace / cfg.operator_feedback_markdown).read_text(encoding="utf-8")
    assert partial_success["status"] == "partial_success"
    assert partial_success["completed_step_count"] == 1
    assert partial_success["failed_repos"] == ["svc"]
    assert partial_success["resume_replan_scope"] == "none"
    assert "# Marvin Operator Feedback" in operator_feedback
    assert "refresh_subplans: no" in operator_feedback