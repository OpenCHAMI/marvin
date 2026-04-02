from pathlib import Path

import pytest

from openchami_coding_agent.config import (
    build_workspace_analysis_config,
    default_working_directory,
    ensure_within_workspace,
    parse_config,
    resolve_repo,
)
from openchami_coding_agent.models import AgentConfig, RepoSpec


def test_ensure_within_workspace_rejects_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="outside"):
        ensure_within_workspace(outside, workspace)


def test_resolve_repo_uses_workspace_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    repo = resolve_repo(workspace, {"name": "foo"})
    assert repo.path == workspace / "repos" / "foo"
    assert repo.source_path is None


def test_resolve_repo_uses_source_path_when_outside_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external_repo = tmp_path / "external-repo"
    external_repo.mkdir()

    repo = resolve_repo(workspace, {"name": "foo", "path": str(external_repo)})
    assert repo.path == workspace / "repos" / "foo"
    assert repo.source_path == external_repo


def test_resolve_repo_reads_hyphenated_read_only_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    repo = resolve_repo(workspace, {"name": "docs", "read-only": True})

    assert repo.read_only is True
    assert repo.execution_enabled is False


def test_agent_config_from_raw_reads_planning_mode(tmp_path: Path) -> None:
    cfg = AgentConfig.from_raw(
        {
            "project": "OpenCHAMI",
            "problem": "Improve planner",
            "planning": {"mode": "hierarchical"},
        },
        workspace=tmp_path,
        workspace_reused=False,
        repos=[],
    )

    assert cfg.planning_mode == "hierarchical"


def test_parse_config_loads_agent_appendix_files_and_repo_brief(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    support_dir = tmp_path / "support"
    support_dir.mkdir()

    (support_dir / "shared.md").write_text("Shared OpenCHAMI guidance.", encoding="utf-8")
    (support_dir / "planner.md").write_text("Planner OpenCHAMI guidance.", encoding="utf-8")
    (support_dir / "executor.md").write_text("Executor Fabrica guidance.", encoding="utf-8")
    (support_dir / "repair.md").write_text("Repair guidance.", encoding="utf-8")
    (support_dir / "fabrica.md").write_text(
        "Fabrica is the deployment authority. Check source definitions before generated outputs.",
        encoding="utf-8",
    )

    config_path = config_dir / "task.yaml"
    config_path.write_text(
        """
project: OpenCHAMI
problem: Improve planning with cached repo knowledge.
workspace: workspace
models:
  default: openai:gpt-5.4
agent:
  prompt_appendix: Inline shared guidance.
  prompt_appendix_path: ../support/shared.md
  planner_prompt_appendix_path: ../support/planner.md
  executor_prompt_appendix_path: ../support/executor.md
  repair_prompt_appendix_path: ../support/repair.md
repos:
  - name: fabrica
    description: Fabrica deployment repository
    brief_path: ../support/fabrica.md
""".strip(),
        encoding="utf-8",
    )

    cfg = parse_config(config_path)

    assert cfg.prompt_appendix == "Inline shared guidance.\n\nShared OpenCHAMI guidance."
    assert cfg.planner_prompt_appendix == "Planner OpenCHAMI guidance."
    assert cfg.executor_prompt_appendix == "Executor Fabrica guidance."
    assert cfg.repair_prompt_appendix == "Repair guidance."
    assert cfg.repos[0].description == "Fabrica deployment repository"
    assert cfg.repos[0].brief.startswith("Fabrica is the deployment authority")


def test_parse_config_preserves_read_only_repo_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        """
project: OpenCHAMI
problem: Use a reference repo without executing in it.
workspace: workspace
repos:
  - name: writable
  - name: reference
    read-only: true
""".strip(),
        encoding="utf-8",
    )

    cfg = parse_config(config_path)

    assert [repo.name for repo in cfg.execution_repos] == ["writable"]
    assert [repo.name for repo in cfg.reference_repos] == ["reference"]
    assert cfg.raw_config["repos"][1]["read-only"] is True


def test_parse_config_preserves_source_config_path_and_recommended_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        """
project: OpenCHAMI
problem: Analyze a previous workspace.
workspace: workspace
outputs:
  recommended_config_yaml: artifacts/recommended.yaml
repos:
  - name: writable
""".strip(),
        encoding="utf-8",
    )

    cfg = parse_config(config_path)

    assert cfg.config_path == config_path.resolve()
    assert cfg.recommended_config_yaml == "artifacts/recommended.yaml"
    assert cfg.raw_config["project"] == "OpenCHAMI"


def test_build_workspace_analysis_config_from_saved_source_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    artifacts = workspace / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "marvin_source_config.yaml").write_text(
        """
config_path: null
raw_config:
  project: OpenCHAMI
  problem: Inspect prior run.
  mode: plan_and_execute
  repos:
    - name: svc
      path: /tmp/placeholder
      checkout: false
  models:
    default: openai:gpt-5.4
""".strip(),
        encoding="utf-8",
    )

    cfg = build_workspace_analysis_config(workspace)

    assert cfg.mode == "analyze_workspace"
    assert cfg.planning_mode == "hierarchical"
    assert cfg.raw_config["project"] == "OpenCHAMI"
    assert cfg.repos[0].name == "svc"


def test_build_workspace_analysis_config_synthesizes_when_no_saved_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / "repos" / "svc").mkdir(parents=True)

    cfg = build_workspace_analysis_config(workspace, model_name="openai:gpt-5.4")

    assert cfg.mode == "analyze_workspace"
    assert cfg.planning_mode == "hierarchical"
    assert cfg.raw_config["workspace"] == str(workspace.resolve())
    assert cfg.repos[0].name == "svc"


def test_default_working_directory_uses_single_execution_repo_when_references_exist() -> None:
    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="test",
        workspace=Path("/tmp/workspace"),
        repos=[
            RepoSpec(name="reference", path=Path("/tmp/reference"), read_only=True),
            RepoSpec(name="service", path=Path("/tmp/service")),
        ],
    )

    assert default_working_directory(cfg) == Path("/tmp/service")


def test_parse_config_raises_for_missing_appendix_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        """
project: OpenCHAMI
problem: Improve planning with cached repo knowledge.
agent:
  prompt_appendix_path: ./missing.md
repos: []
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="agent.prompt_appendix_path"):
        parse_config(config_path)


def test_parse_config_falls_back_to_packaged_prompt_library(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        """
project: OpenCHAMI
problem: Use packaged Marvin prompt assets.
workspace: workspace
agent:
  prompt_appendix_path: prompt-library/appendices/openchami-shared.md
repos:
  - name: fabrica
    brief_path: prompt-library/briefs/fabrica.md
""".strip(),
        encoding="utf-8",
    )

    cfg = parse_config(config_path)

    assert "Repository-first OpenCHAMI guidance:" in cfg.prompt_appendix
    assert cfg.repos[0].brief.startswith("repo: fabrica")
