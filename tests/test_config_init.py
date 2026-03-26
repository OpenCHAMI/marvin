from __future__ import annotations

import argparse
import io
import textwrap
from pathlib import Path

import yaml

from openchami_coding_agent.cli import main
from openchami_coding_agent.config_init import (
    ConfigInitSpec,
    RepoInitSpec,
    auto_config_spec_from_source,
    build_config_payload,
    extract_openchami_links,
    generate_agent_payload_from_source,
    run_init_command,
)


def test_build_config_payload_populates_defaults(tmp_path: Path) -> None:
    spec = ConfigInitSpec(
        project="OpenCHAMI boot-service",
        source_kind="github_issue",
        source_reference="https://github.com/OpenCHAMI/boot-service/issues/6",
        problem="Implement issue #6.",
        repos=[
            RepoInitSpec(
                name="boot-service",
                url="https://github.com/OpenCHAMI/boot-service.git",
                branch="main",
                checkout=True,
                language="go",
                description="Boot service repo",
                checks=["go test ./..."],
            )
        ],
        output_path=tmp_path / "boot-service-issue-6.yaml",
    )

    payload = build_config_payload(spec)

    assert payload["project"] == "OpenCHAMI boot-service"
    assert payload["repos"][0]["name"] == "boot-service"
    assert payload["repos"][0]["checkout"] is True
    assert payload["models"]["default"] == "openai:gpt-5.4"
    assert payload["task"]["confirm_before_execute"] is True
    assert payload["execution"]["commit_each_step"] is True
    assert (
        "Source reference: https://github.com/OpenCHAMI/boot-service/issues/6"
        in payload["task"]["notes"]
    )
    assert payload["outputs"]["plan_json"] == "artifacts/boot-service-issue-6-plan.json"


def test_run_init_command_writes_yaml(tmp_path: Path) -> None:
    responses = iter(
        [
            "OpenCHAMI boot-service",
            "github_issue",
            "https://github.com/OpenCHAMI/boot-service/issues/6",
            "Implement issue #6 for boot-service.",
            ".",
            "boot-service",
            "https://github.com/OpenCHAMI/boot-service.git",
            "",
            "main",
            "y",
            "go",
            "Boot service repo",
            "go test ./...",
            ".",
            "n",
            ".",
            ".",
            "openai:gpt-5.4",
            "y",
            "y",
            "y",
            "wizard.yaml",
        ]
    )

    def fake_input(prompt: str) -> str:
        del prompt
        return next(responses)

    output = io.StringIO()
    args = argparse.Namespace(output=None, source_file=None, force=False)

    code = run_init_command(args, input_func=fake_input, output=output, cwd=tmp_path)

    assert code == 0
    config_path = tmp_path / "wizard.yaml"
    assert config_path.exists()

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["project"] == "OpenCHAMI boot-service"
    assert payload["task"]["execute_after_plan"] is True
    assert payload["execution"]["commit_each_step"] is True
    assert payload["repos"][0]["checks"] == ["go test ./..."]
    assert "Run it with: marvin" in output.getvalue()


def test_cli_main_dispatches_init_command(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_init_command(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 17

    monkeypatch.setattr("openchami_coding_agent.cli.run_init_command", fake_run_init_command)

    assert main(["init", "--output", "task.yaml", "--force"]) == 17
    parsed = captured["args"]
    assert parsed.output == "task.yaml"
    assert parsed.force is True
    assert parsed.model == "openai:gpt-5.4"


def test_auto_config_spec_from_source_infers_project_and_repo(tmp_path: Path) -> None:
    source_file = tmp_path / "tokensmith-amsc.md"
    source_file.write_text(
        "You are working in the OpenCHAMI ecosystem on the `tokensmith` service.\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        output="tokensmith-amsc-task.yaml",
        source_file=str(source_file),
        interactive=False,
        force=False,
    )

    spec = auto_config_spec_from_source(args, cwd=tmp_path)

    assert spec.project == "tokensmith"
    assert spec.source_kind == "feature_description"
    assert spec.repos[0].name == "tokensmith"
    assert spec.repos[0].checkout is True
    assert spec.output_path == tmp_path / "tokensmith-amsc-task.yaml"


def test_extract_openchami_links_filters_external_results() -> None:
    page_html = """
    <a href="/OpenCHAMI/tokensmith">repo</a>
    <a href="/OpenCHAMI/tokensmith/issues/12">issue</a>
    <a href="/someoneelse/project">external</a>
    <a href="/OpenCHAMI/tokensmith/stargazers">stars</a>
    """

    assert extract_openchami_links(page_html) == [
        "https://github.com/OpenCHAMI/tokensmith",
        "https://github.com/OpenCHAMI/tokensmith/issues/12",
    ]


def test_generate_agent_payload_from_source_uses_openchami_context(
    monkeypatch, tmp_path: Path
) -> None:
    source_file = tmp_path / "tokensmith-amsc.md"
    source_file.write_text(
        "You are working in the OpenCHAMI ecosystem on the `tokensmith` service.\n"
        "Implement token exchange support.\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        output="tokensmith-amsc-task.yaml",
        source_file=str(source_file),
        interactive=False,
        force=False,
        model="openai:gpt-5.4",
    )

    captured: dict[str, object] = {}

    def fake_discover(source_text: str, *, project: str, repo_names: list[str], limit: int = 6):
        captured["source_text"] = source_text
        captured["project"] = project
        captured["repo_names"] = repo_names
        captured["limit"] = limit
        return [
            {
                "url": "https://github.com/OpenCHAMI/tokensmith",
                "title": "OpenCHAMI/tokensmith",
                "text": "JWT issuer service with token exchange discussions.",
            }
        ]

    class FakeLLM:
        def invoke(self, messages):
            captured["messages"] = messages
            return type(
                "Response",
                (),
                {
                    "content": textwrap.dedent(
                        """
                        project: tokensmith
                        problem: |
                          Expand tokensmith into a policy-aware STS with RFC 8693 token exchange.
                        repos:
                          - name: tokensmith
                            url: https://github.com/OpenCHAMI/tokensmith
                            branch: main
                            language: go
                            description: OpenCHAMI token service.
                            checks:
                              - go test ./...
                        task:
                          execute_after_plan: true
                          confirm_before_execute: true
                          confirm_timeout_sec: 45
                          deliverables:
                            - Add token exchange support.
                          plan_requirements:
                            - Map the current issuer code before edits.
                          execution_requirements:
                            - Keep changes incremental.
                          notes:
                            - Grounded in OpenCHAMI repo context.
                        execution:
                          commit_each_step: true
                        """
                    )
                },
            )()

    monkeypatch.setattr(
        "openchami_coding_agent.config_init.discover_openchami_context",
        fake_discover,
    )
    monkeypatch.setattr("openchami_coding_agent.config_init.make_init_llm", lambda model: FakeLLM())

    payload = generate_agent_payload_from_source(args, cwd=tmp_path)

    assert payload["project"] == "tokensmith"
    assert payload["problem"].startswith("Expand tokensmith into a policy-aware STS")
    assert payload["repos"][0]["name"] == "tokensmith"
    assert payload["repos"][0]["url"] == "https://github.com/OpenCHAMI/tokensmith"
    assert payload["repos"][0]["checkout"] is True
    assert payload["execution"]["commit_each_step"] is True
    assert payload["task"]["plan_requirements"] == ["Map the current issuer code before edits."]
    assert captured["project"] == "tokensmith"
    assert captured["repo_names"] == ["tokensmith"]


def test_run_init_command_auto_generates_without_prompting(monkeypatch, tmp_path: Path) -> None:
    source_file = tmp_path / "tokensmith-amsc.md"
    source_file.write_text("Example source text\n", encoding="utf-8")
    output = io.StringIO()
    args = argparse.Namespace(
        output="tokensmith-amsc-task.yaml",
        source_file=str(source_file),
        interactive=False,
        force=False,
        model="openai:gpt-5.4",
    )

    monkeypatch.setattr(
        "openchami_coding_agent.config_init.generate_agent_payload_from_source",
        lambda args, cwd=None: {
            "project": "tokensmith",
            "problem": "Useful summary.",
            "mode": "plan_and_execute",
            "repos": [{"name": "tokensmith", "url": "https://github.com/OpenCHAMI/tokensmith"}],
            "models": {"default": "openai:gpt-5.4"},
            "task": {
                "execute_after_plan": True,
                "confirm_before_execute": True,
                "confirm_timeout_sec": 45,
                "deliverables": ["Ship the feature."],
                "plan_requirements": ["Understand the repo first."],
                "execution_requirements": ["Run focused tests."],
                "notes": ["Source reference present."],
            },
            "execution": {"commit_each_step": True},
            "outputs": {
                "proposal_markdown": "docs/tokensmith-amsc-task-proposal.md",
                "plan_json": "artifacts/tokensmith-amsc-task-plan.json",
                "summary_json": "artifacts/tokensmith-amsc-task-summary.json",
                "executor_progress_json": "artifacts/tokensmith-amsc-task-progress.json",
            },
        },
    )

    def fail_input(prompt: str) -> str:
        raise AssertionError(f"Unexpected prompt during auto-generation: {prompt}")

    code = run_init_command(args, input_func=fail_input, output=output, cwd=tmp_path)

    assert code == 0
    config_path = tmp_path / "tokensmith-amsc-task.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["project"] == "tokensmith"
    assert payload["problem"] == "Useful summary."
    assert payload["repos"][0]["name"] == "tokensmith"
    assert "agent assistance" in output.getvalue()


def test_run_init_command_reports_agent_failure_cleanly(monkeypatch, tmp_path: Path) -> None:
    source_file = tmp_path / "tokensmith-amsc.md"
    source_file.write_text("Example source text\n", encoding="utf-8")
    output = io.StringIO()
    args = argparse.Namespace(
        output="tokensmith-amsc-task.yaml",
        source_file=str(source_file),
        interactive=False,
        force=False,
        model="openai:gpt-5.4",
    )

    monkeypatch.setattr(
        "openchami_coding_agent.config_init.generate_agent_payload_from_source",
        lambda args, cwd=None: (_ for _ in ()).throw(RuntimeError("missing credentials")),
    )

    code = run_init_command(args, output=output, cwd=tmp_path)

    assert code == 1
    assert "Agent-assisted config generation failed: missing credentials" in output.getvalue()
    assert "--interactive" in output.getvalue()