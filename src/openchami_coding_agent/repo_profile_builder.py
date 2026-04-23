"""Dedicated OpenCHAMI repository profile builder flow."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .constants import AGENT_NAME
from .reporting import emit_panel, emit_text
from .ursa_compat import get_agent_class, instantiate_agent, setup_llm
from .utils import invoke_agent, render_yaml_text, run_command

_PROFILE_LIST_KEYS = {
    "language_toolchain",
    "validation_commands",
    "smoke_tests",
    "packaging_commands",
    "dangerous_operations",
    "critical_files",
    "service_boundaries",
    "related_repos",
    "protected_paths",
    "operator_sensitive_behavior",
    "resume_idempotence_expectations",
}

_TOP_LEVEL_CRITICAL_FILES = (
    "go.mod",
    "pyproject.toml",
    "package.json",
    "Dockerfile",
    "README.md",
    "Makefile",
    ".goreleaser.yaml",
)


def _profile_contract_text() -> str:
    try:
        path = resources.files("openchami_coding_agent").joinpath(
            "prompt_library", "profile_repo.md"
        )
    except ModuleNotFoundError:
        return ""
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _extract_yaml_block(text: str) -> str:
    match = re.search(r"```yaml\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.strip()


def _read_text(path: Path, max_chars: int = 5000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_items: list[str] = []
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        unique_items.append(value)
    return unique_items


def _sample_repo_files(repo_path: Path, max_files: int = 220) -> list[str]:
    code, out, _ = run_command(["git", "-C", str(repo_path), "ls-files"])
    if code == 0:
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        return lines[:max_files]

    files: list[str] = []
    for path in sorted(repo_path.rglob("*")):
        if len(files) >= max_files:
            break
        if not path.is_file():
            continue
        files.append(str(path.relative_to(repo_path)))
    return files


def _infer_language_toolchain(repo_path: Path, files: list[str]) -> list[str]:
    lowered = {path.lower() for path in files}
    inferred: list[str] = []
    if (repo_path / "go.mod").exists() or any(path.endswith(".go") for path in lowered):
        inferred.append("go")
    if (repo_path / "pyproject.toml").exists() or any(path.endswith(".py") for path in lowered):
        inferred.append("python")
    if (repo_path / "package.json").exists() or any(path.endswith(".ts") or path.endswith(".js") for path in lowered):
        inferred.append("node")
    if not inferred:
        inferred.append("generic")
    return inferred


def _infer_validation_commands(repo_path: Path) -> list[str]:
    commands: list[str] = []
    if (repo_path / "go.mod").exists():
        commands.append("go test ./...")
    if (repo_path / "pyproject.toml").exists() or (repo_path / "pytest.ini").exists():
        commands.append("pytest -q")
    if (repo_path / "package.json").exists():
        commands.append("npm test")
    return commands


def _infer_packaging_commands(repo_path: Path) -> list[str]:
    commands: list[str] = []
    if (repo_path / "pyproject.toml").exists():
        commands.append("python -m build")
    if (repo_path / "go.mod").exists():
        commands.append("go build ./...")
    if (repo_path / "Dockerfile").exists():
        commands.append("docker build .")
    return commands


def _extract_make_targets(repo_path: Path) -> set[str]:
    makefile = repo_path / "Makefile"
    if not makefile.exists() or not makefile.is_file():
        return set()
    targets: set[str] = set()
    pattern = re.compile(r"^([A-Za-z0-9_.-]+):")
    for line in makefile.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        target = match.group(1)
        if target.startswith("."):
            continue
        targets.add(target)
    return targets


def _infer_smoke_tests(repo_name: str, repo_path: Path, make_targets: set[str]) -> list[str]:
    commands: list[str] = []
    if "build" in make_targets:
        commands.append("make build")
    if (repo_path / "cmd").exists():
        commands.append(f"{repo_name} --help")
    if (repo_path / "Dockerfile").exists():
        commands.append("docker build .")
    return _unique(commands)


def _infer_critical_files(repo_path: Path) -> list[str]:
    critical: list[str] = []
    for rel_path in _TOP_LEVEL_CRITICAL_FILES:
        if (repo_path / rel_path).exists():
            critical.append(rel_path)
    for rel_path in (
        "docs/http-endpoints.md",
        "docs/security-notes.md",
        "docs/internal-service-auth.md",
        "docs/cli-reference.md",
        "docs/authz-spec.md",
        "docs/authz_operations.md",
    ):
        if (repo_path / rel_path).exists():
            critical.append(rel_path)
    cmd_dir = repo_path / "cmd"
    if cmd_dir.exists():
        for child in sorted(cmd_dir.iterdir()):
            if child.is_dir():
                critical.append(str(child.relative_to(repo_path)) + "/")
    return _unique(critical)


def _infer_service_boundaries(repo_path: Path) -> list[str]:
    boundaries: list[str] = []
    for rel_dir in ("cmd", "pkg", "internal", "middleware"):
        base = repo_path / rel_dir
        if not base.exists() or not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if child.is_dir():
                boundaries.append(str(child.relative_to(repo_path)))
    return _unique(boundaries)


def _infer_related_repos(repo_path: Path, readme: str) -> list[str]:
    related: list[str] = []
    lowered = readme.lower()
    if (repo_path / "docs" / "fabrica.md").exists() or "fabrica" in lowered:
        related.append("fabrica")
    return _unique(related)


def _infer_protected_paths(repo_path: Path) -> list[str]:
    protected: list[str] = []
    for rel_path in (".github/", "docs/", "cmd/", "internal/", "middleware/"):
        if (repo_path / rel_path.rstrip("/")).exists():
            protected.append(rel_path)
    return _unique(protected)


def _infer_operator_sensitive_behavior(repo_path: Path, readme: str) -> list[str]:
    notes: list[str] = []
    lowered = readme.lower()
    if "oauth/token" in lowered or "token exchange" in lowered or (repo_path / "docs" / "http-endpoints.md").exists():
        notes.append(
            "Token exchange, HTTP endpoint, or JWKS surface changes require operator-facing notes and rollout review."
        )
    if (repo_path / "docs" / "authz_operations.md").exists() or "policy_version" in lowered:
        notes.append(
            "Authorization policy loading, enforcement mode, or policy version behavior changes require operator review."
        )
    if (repo_path / "docs" / "env-reference.md").exists() or (repo_path / "docs" / "cli-reference.md").exists():
        notes.append(
            "CLI, config, or environment variable surface changes require documentation and operator update notes."
        )
    return _unique(notes)


def _infer_resume_expectations(repo_path: Path) -> list[str]:
    expectations: list[str] = [
        "Profile-guided validation and smoke checks should be repeatable without mutating committed source files."
    ]
    if (repo_path / "cmd").exists():
        expectations.append(
            "CLI help, health, and build-oriented smoke checks should remain safe to rerun during resume and verification cycles."
        )
    return _unique(expectations)


def _heuristic_profile(repo_name: str, repo_path: Path) -> dict[str, Any]:
    files = _sample_repo_files(repo_path)
    readme = _read_text(repo_path / "README.md")
    make_targets = _extract_make_targets(repo_path)

    validation_commands = _infer_validation_commands(repo_path)
    if "test" in make_targets:
        validation_commands.insert(0, "make test")
    if "lint" in make_targets:
        validation_commands.append("make lint")
    if "vet" in make_targets:
        validation_commands.append("make vet")

    packaging_commands = _infer_packaging_commands(repo_path)
    if "build" in make_targets:
        packaging_commands.insert(0, "make build")
    if "docker-build" in make_targets:
        packaging_commands.append("make docker-build")

    dangerous_operations: list[str] = []
    if "clean" in make_targets:
        dangerous_operations.append("make clean")
    if (repo_path / "Dockerfile").exists():
        dangerous_operations.append("docker build .")

    return {
        "repo": repo_name,
        "language_toolchain": _infer_language_toolchain(repo_path, files),
        "validation_commands": _unique(validation_commands),
        "smoke_tests": _infer_smoke_tests(repo_name, repo_path, make_targets),
        "packaging_commands": _unique(packaging_commands),
        "dangerous_operations": _unique(dangerous_operations),
        "critical_files": _infer_critical_files(repo_path),
        "service_boundaries": _infer_service_boundaries(repo_path),
        "related_repos": _infer_related_repos(repo_path, readme),
        "protected_paths": _infer_protected_paths(repo_path),
        "operator_sensitive_behavior": _infer_operator_sensitive_behavior(repo_path, readme),
        "resume_idempotence_expectations": _infer_resume_expectations(repo_path),
    }


def _merge_profile_payloads(
    generated: dict[str, Any],
    heuristic: dict[str, Any],
    repo_name: str,
) -> dict[str, Any]:
    merged: dict[str, Any] = {"repo": str(generated.get("repo") or heuristic.get("repo") or repo_name)}
    for key in _PROFILE_LIST_KEYS:
        generated_items = generated.get(key)
        heuristic_items = heuristic.get(key)
        merged[key] = _unique(
            [
                *(
                    [str(item) for item in generated_items]
                    if isinstance(generated_items, list)
                    else []
                ),
                *(
                    [str(item) for item in heuristic_items]
                    if isinstance(heuristic_items, list)
                    else []
                ),
            ]
        )
    return merged


def build_repo_profile_prompt(repo_name: str, repo_path: Path) -> str:
    files = _sample_repo_files(repo_path)
    readme = _read_text(repo_path / "README.md")
    docs_index = _read_text(repo_path / "docs" / "README.md")
    inferred_languages = _infer_language_toolchain(repo_path, files)
    inferred_validation = _infer_validation_commands(repo_path)
    inferred_packaging = _infer_packaging_commands(repo_path)
    contract = _profile_contract_text()

    return f"""
You are {AGENT_NAME}, building a structured OpenCHAMI repository profile from repository evidence.

Repository:
- name: {repo_name}
- path: {repo_path}

Evidence:
- sampled files:\n""" + "\n".join(f"  - {path}" for path in files) + f"""

- inferred language/toolchain seeds: {inferred_languages}
- inferred validation command seeds: {inferred_validation}
- inferred packaging command seeds: {inferred_packaging}

README excerpt:
{readme or '<missing>'}

docs/README excerpt:
{docs_index or '<missing>'}

Return ONLY YAML in a single ```yaml fenced block with this shape:
repo: <repo-name>
language_toolchain: []
validation_commands: []
smoke_tests: []
packaging_commands: []
dangerous_operations: []
critical_files: []
service_boundaries: []
related_repos: []
protected_paths: []
operator_sensitive_behavior: []
resume_idempotence_expectations: []

Requirements:
- Keep only concrete, repository-evidence-backed entries.
- Prefer concise command suggestions likely to run in CI/local dev.
- Keep lists compact; avoid speculative items.
- Paths should be workspace-relative repo paths.
- If unknown, use an empty list for that field.
""".strip() + (f"\n\nProfile contract:\n{contract}" if contract else "")


def parse_repo_profile_response(content: str, repo_name: str) -> dict[str, Any]:
    snippet = _extract_yaml_block(content)
    payload = yaml.safe_load(snippet)
    if not isinstance(payload, dict):
        raise ValueError("Profile response did not parse into a YAML mapping.")

    normalized: dict[str, Any] = {"repo": str(payload.get("repo") or repo_name)}
    for key in _PROFILE_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            normalized[key] = [str(item).strip() for item in value if str(item).strip()]
        else:
            normalized[key] = []
    return normalized


def generate_repo_profile(
    *,
    workspace: Path,
    repo_name: str,
    model_name: str = "openai:gpt-5.4",
    repo_path: Path | None = None,
    output_path: Path | None = None,
    verbose_io: bool = False,
    overwrite: bool = False,
) -> Path:
    resolved_workspace = workspace.resolve()
    resolved_repo_path = (
        repo_path.resolve()
        if repo_path is not None
        else (resolved_workspace / "repos" / repo_name).resolve()
    )
    if not resolved_repo_path.exists() or not resolved_repo_path.is_dir():
        raise FileNotFoundError(f"Repository path does not exist: {resolved_repo_path}")

    target_path = (
        output_path.resolve()
        if output_path is not None
        else (resolved_workspace / "repo_profiles" / f"{repo_name}.yaml").resolve()
    )
    if target_path.exists() and not overwrite:
        raise FileExistsError(
            f"Profile already exists at {target_path}; use --overwrite to replace it."
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)

    prompt = build_repo_profile_prompt(repo_name, resolved_repo_path)
    heuristic_profile = _heuristic_profile(repo_name, resolved_repo_path)
    llm = setup_llm(model_name, {}, agent_name=AGENT_NAME)
    planning_agent_class = get_agent_class("PlanningAgent")
    planner = instantiate_agent(
        planning_agent_class,
        llm=llm,
        enable_metrics=True,
        metrics_dir="ursa_metrics",
        thread_id=f"{resolved_workspace.name}::repo-profile::{repo_name}",
        workspace=str(resolved_workspace),
    )
    if hasattr(planner, "thread_id"):
        planner.thread_id = f"{resolved_workspace.name}::repo-profile::{repo_name}"
    invocation = invoke_agent(planner, prompt, verbose_io)
    generated_profile = parse_repo_profile_response(invocation.content, repo_name)
    profile_payload = _merge_profile_payloads(generated_profile, heuristic_profile, repo_name)

    target_path.write_text(render_yaml_text(profile_payload), encoding="utf-8")
    emit_panel(
        (
            "Repository profile generated.\n"
            f"repo: {repo_name}\n"
            f"source: {resolved_repo_path}\n"
            f"output: {target_path}"
        ),
        border_style="green",
    )
    emit_text(f"Wrote repository profile: {target_path}")
    return target_path