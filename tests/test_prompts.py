from pathlib import Path

from openchami_coding_agent.models import AgentConfig, PlanStep, RepoSpec
from openchami_coding_agent.prompts import (
    build_executor_control_suffix,
    build_executor_prompt,
    build_executor_step_prompt,
    build_planner_control_suffix,
    build_planner_prompt_from_prefix,
    build_planner_prompt_prefix,
    build_repo_fix_control_suffix,
    build_repo_fix_prompt_prefix,
    build_workspace_analysis_prompt,
    build_workspace_analysis_prompt_prefix,
    build_subplanner_control_suffix,
    build_subplanner_prompt_from_prefix,
    build_subplanner_prompt_prefix,
)


def _cfg() -> AgentConfig:
    return AgentConfig(
        project="OpenCHAMI",
        problem="Implement cache-aware prompt shaping.",
        workspace=Path("/tmp/marvin-ws"),
        repos=[RepoSpec(name="svc", path=Path("/tmp/marvin-ws/repos/svc"))],
        execution_requirements=["Keep prompts deterministic."],
    )


def test_build_executor_step_prompt_preserves_base_prefix() -> None:
    cfg = _cfg()
    plan = [PlanStep(name="Inspect", description="Inspect current prompt builders.")]
    base_prompt = build_executor_prompt(cfg, "1. Inspect", structured_plan=plan)

    prompt = build_executor_step_prompt(
        base_prompt,
        step_detail="Inspect\nInspect current prompt builders.",
        step_index=1,
        total_steps=3,
    )

    assert prompt.startswith(base_prompt)
    assert build_executor_control_suffix(
        step_detail="Inspect\nInspect current prompt builders.",
        step_index=1,
        total_steps=3,
    ) in prompt


def test_build_repo_fix_prompt_prefix_excludes_failure_details() -> None:
    cfg = _cfg()
    repo = cfg.repos[0]

    prefix = build_repo_fix_prompt_prefix(cfg, repo, "1. Inspect")

    assert "Validation failure details:" not in prefix
    assert "Repair control:" not in prefix
    assert "Preserve useful existing comments." in prefix


def test_build_repo_fix_control_suffix_is_only_dynamic_repair_context() -> None:
    suffix = build_repo_fix_control_suffix("tests failed in auth handler", 2)

    assert suffix.startswith("Repair control:")
    assert "Attempt: 2" in suffix
    assert "tests failed in auth handler" in suffix


def test_build_planner_prompt_prefix_excludes_task_context() -> None:
    cfg = _cfg()

    prefix = build_planner_prompt_prefix(cfg)

    assert "Planning context:" not in prefix
    assert f"Project: {cfg.project}" not in prefix
    assert "Available repositories:" not in prefix


def test_build_planner_prompt_from_prefix_appends_dynamic_context() -> None:
    cfg = _cfg()
    prefix = build_planner_prompt_prefix(cfg)

    prompt = build_planner_prompt_from_prefix(prefix, cfg=cfg)

    assert prompt.startswith(prefix)
    assert build_planner_control_suffix(cfg) in prompt
    assert "comments should remain" in prompt
    assert "Aim for about 4-10 main steps" in prompt
    assert "reviewable unit of work" in prompt


def test_build_subplanner_prompt_prefix_excludes_main_step_context() -> None:
    cfg = _cfg()

    prefix = build_subplanner_prompt_prefix(cfg)

    assert "Subplanning context:" not in prefix
    assert "Current main step to expand:" not in prefix
    assert "comments must stay accurate and relevant" in prefix
    assert "usually 1-3 and rarely more than 5" in prefix
    assert "Each sub-step may become its own commit" in prefix


def test_build_subplanner_prompt_from_prefix_appends_main_step_context() -> None:
    cfg = _cfg()
    prefix = build_subplanner_prompt_prefix(cfg)
    main_step = PlanStep(name="Inspect", description="Inspect current prompt builders.")

    prompt = build_subplanner_prompt_from_prefix(
        prefix,
        cfg=cfg,
        main_step=main_step,
        main_step_index=1,
        total_main_steps=3,
    )

    assert prompt.startswith(prefix)
    assert (
        build_subplanner_control_suffix(
            cfg,
            main_step=main_step,
            main_step_index=1,
            total_main_steps=3,
        )
        in prompt
    )


def test_build_executor_prompt_includes_comment_preservation_rule() -> None:
    cfg = _cfg()
    prompt = build_executor_prompt(cfg, "1. Inspect")

    assert "Preserve useful existing comments." in prompt
    assert "accurate and relevant to the code they describe" in prompt


def test_build_executor_prompt_labels_reference_repos_as_read_only() -> None:
    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="Implement cache-aware prompt shaping.",
        workspace=Path("/tmp/marvin-ws"),
        repos=[
            RepoSpec(name="svc", path=Path("/tmp/marvin-ws/repos/svc")),
            RepoSpec(
                name="docs",
                path=Path("/tmp/marvin-ws/repos/docs"),
                read_only=True,
            ),
        ],
    )

    prompt = build_executor_prompt(cfg, "1. Inspect")

    assert "role=reference-only" in prompt
    assert "read-only context; do not edit it" in prompt


def test_build_workspace_analysis_prompt_prefix_declares_expected_sections() -> None:
    cfg = _cfg()

    prompt = build_workspace_analysis_prompt_prefix(cfg)

    assert "Workspace Assessment" in prompt
    assert "Recommended YAML Updates" in prompt
    assert "Suggested YAML Snippet" in prompt
    assert "Suggested Operator Feedback" in prompt
    assert "Clarifications Needed" in prompt
    assert "hierarchical analysis" in prompt


def test_build_workspace_analysis_prompt_includes_evidence_and_answers() -> None:
    cfg = _cfg()

    prompt = build_workspace_analysis_prompt(
        cfg,
        workspace_evidence="summary: checks failed",
        clarification_answers="- Which repo failed?: boot-service",
    )

    assert "summary: checks failed" in prompt
    assert "Which repo failed?: boot-service" in prompt
    assert "Planning mode for this analysis: hierarchical" in prompt


def test_build_planner_control_suffix_includes_repo_profile_hints(tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        profiles_dir = workspace / "repo_profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "svc.yaml").write_text(
                """
repo: svc
language_toolchain:
    - python
validation_commands:
    - pytest -q
critical_files:
    - pyproject.toml
protected_paths:
    - .github/
""".strip(),
                encoding="utf-8",
        )
        cfg = AgentConfig(
                project="OpenCHAMI",
                problem="Plan with profile hints.",
                workspace=workspace,
                repos=[RepoSpec(name="svc", path=workspace / "repos" / "svc")],
        )

        prompt = build_planner_control_suffix(cfg)

        assert "Repository profile hints (OpenCHAMI context):" in prompt
        assert "svc: languages=python" in prompt
        assert "validation=pytest -q" in prompt


def test_build_subplanner_control_suffix_includes_repo_profile_hints(tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        profiles_dir = workspace / "repo_profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "svc.yaml").write_text(
                """
repo: svc
language_toolchain:
    - go
validation_commands:
    - go test ./...
""".strip(),
                encoding="utf-8",
        )
        cfg = AgentConfig(
                project="OpenCHAMI",
                problem="Subplan with profile hints.",
                workspace=workspace,
                repos=[RepoSpec(name="svc", path=workspace / "repos" / "svc")],
        )

        prompt = build_subplanner_control_suffix(
                cfg,
                main_step=PlanStep(name="Inspect", description="Inspect repo"),
                main_step_index=1,
                total_main_steps=2,
        )

        assert "Repository profile hints (OpenCHAMI context):" in prompt
        assert "svc: languages=go" in prompt
        assert "validation=go test ./..." in prompt