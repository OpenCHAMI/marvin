from __future__ import annotations

from pathlib import Path

from openchami_coding_agent.models import InvocationCapture
from openchami_coding_agent.repo_profile_builder import (
    generate_repo_profile,
    parse_repo_profile_response,
)


def test_parse_repo_profile_response_normalizes_shape() -> None:
    payload = parse_repo_profile_response(
        """
```yaml
repo: tokensmith
language_toolchain:
  - go
validation_commands:
  - go test ./...
critical_files:
  - cmd/tokenservice/main.go
```
""",
        repo_name="tokensmith",
    )

    assert payload["repo"] == "tokensmith"
    assert payload["language_toolchain"] == ["go"]
    assert payload["validation_commands"] == ["go test ./..."]
    assert payload["critical_files"] == ["cmd/tokenservice/main.go"]
    assert payload["packaging_commands"] == []


def test_generate_repo_profile_writes_yaml_artifact(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    repo = workspace / "repos" / "tokensmith"
    repo.mkdir(parents=True)
    (repo / "go.mod").write_text("module example.com/tokensmith\n", encoding="utf-8")
    (repo / "README.md").write_text("# TokenSmith\n", encoding="utf-8")

    monkeypatch.setattr("openchami_coding_agent.repo_profile_builder.setup_llm", lambda *args, **kwargs: object())
    monkeypatch.setattr("openchami_coding_agent.repo_profile_builder.get_agent_class", lambda *args: object)
    monkeypatch.setattr("openchami_coding_agent.repo_profile_builder.instantiate_agent", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        "openchami_coding_agent.repo_profile_builder.invoke_agent",
        lambda *args, **kwargs: InvocationCapture(
            content=(
                "```yaml\n"
                "repo: tokensmith\n"
                "language_toolchain:\n"
                "  - go\n"
                "validation_commands:\n"
                "  - go test ./...\n"
                "smoke_tests:\n"
                "  - tokensmith --help\n"
                "packaging_commands:\n"
                "  - go build ./...\n"
                "dangerous_operations: []\n"
                "critical_files:\n"
                "  - go.mod\n"
                "service_boundaries: []\n"
                "related_repos: []\n"
                "protected_paths: []\n"
                "operator_sensitive_behavior: []\n"
                "resume_idempotence_expectations: []\n"
                "```"
            )
        ),
    )

    output_path = workspace / "repo_profiles" / "tokensmith.yaml"
    written = generate_repo_profile(
        workspace=workspace,
        repo_name="tokensmith",
        output_path=output_path,
        overwrite=True,
    )

    text = written.read_text(encoding="utf-8")
    assert written == output_path
    assert "repo: tokensmith" in text
    assert "- go test ./..." in text


def test_generate_repo_profile_requires_overwrite_flag(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = workspace / "repos" / "tokensmith"
    repo.mkdir(parents=True)
    (workspace / "repo_profiles").mkdir(parents=True)
    existing = workspace / "repo_profiles" / "tokensmith.yaml"
    existing.write_text("repo: tokensmith\n", encoding="utf-8")

    try:
        generate_repo_profile(
            workspace=workspace,
            repo_name="tokensmith",
            output_path=existing,
            overwrite=False,
        )
    except FileExistsError as exc:
        assert "--overwrite" in str(exc)
    else:
        raise AssertionError("Expected FileExistsError when overwrite is disabled.")


def test_generate_repo_profile_merges_heuristics_when_model_returns_empty_lists(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    repo = workspace / "repos" / "tokensmith"
    (repo / "cmd" / "tokenservice").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)
    (repo / "go.mod").write_text("module example.com/tokensmith\n", encoding="utf-8")
    (repo / "README.md").write_text(
        "TokenSmith provides token exchange and policy version support.\n",
        encoding="utf-8",
    )
    (repo / "Makefile").write_text(
        "build:\n\tgo build ./...\n\n"
        "test:\n\tgo test ./...\n\n"
        "lint:\n\tgolangci-lint run\n",
        encoding="utf-8",
    )
    (repo / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (repo / "docs" / "http-endpoints.md").write_text("# HTTP endpoints\n", encoding="utf-8")
    (repo / "docs" / "authz_operations.md").write_text("# Authz ops\n", encoding="utf-8")
    (repo / "docs" / "cli-reference.md").write_text("# CLI\n", encoding="utf-8")

    monkeypatch.setattr("openchami_coding_agent.repo_profile_builder.setup_llm", lambda *args, **kwargs: object())
    monkeypatch.setattr("openchami_coding_agent.repo_profile_builder.get_agent_class", lambda *args: object)
    monkeypatch.setattr("openchami_coding_agent.repo_profile_builder.instantiate_agent", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        "openchami_coding_agent.repo_profile_builder.invoke_agent",
        lambda *args, **kwargs: InvocationCapture(
            content=(
                "```yaml\n"
                "repo: tokensmith\n"
                "language_toolchain: []\n"
                "validation_commands: []\n"
                "smoke_tests: []\n"
                "packaging_commands: []\n"
                "dangerous_operations: []\n"
                "critical_files: []\n"
                "service_boundaries: []\n"
                "related_repos: []\n"
                "protected_paths: []\n"
                "operator_sensitive_behavior: []\n"
                "resume_idempotence_expectations: []\n"
                "```"
            )
        ),
    )

    written = generate_repo_profile(
        workspace=workspace,
        repo_name="tokensmith",
        output_path=workspace / "repo_profiles" / "tokensmith.yaml",
        overwrite=True,
    )

    text = written.read_text(encoding="utf-8")
    assert "- go" in text
    assert "- make test" in text
    assert "- make build" in text
    assert "- tokensmith --help" in text
    assert "- Dockerfile" in text
    assert "- cmd/tokenservice/" in text