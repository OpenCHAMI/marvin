from pathlib import Path

import pytest

from openchami_coding_agent.config import ensure_within_workspace, parse_config, resolve_repo
from openchami_coding_agent.models import AgentConfig


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
