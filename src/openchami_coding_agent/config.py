"""Configuration parsing and repository/workspace resolution."""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path
from typing import Any

from .constants import (
    AGENT_NAME,
    DEFAULT_SOURCE_CONFIG_YAML,
    DEFAULT_SUMMARY_JSON,
    DEFAULT_WORKSPACE_ROOT,
)
from .models import AgentConfig, RepoSpec
from .reporting import emit_panel, emit_table
from .ursa_compat import (
    generate_workspace_name,
    load_yaml_config,
    sanitize_for_logging,
    setup_workspace,
)
from .utils import run_command, slugify, to_plain_data

_AGENT_APPENDIX_PATH_FIELDS = {
    "prompt_appendix": "prompt_appendix_path",
    "planner_prompt_appendix": "planner_prompt_appendix_path",
    "executor_prompt_appendix": "executor_prompt_appendix_path",
    "repair_prompt_appendix": "repair_prompt_appendix_path",
}


def _compact_text(text: str, max_chars: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def _merge_text_blocks(*parts: object) -> str:
    blocks = [str(part).strip() for part in parts if isinstance(part, str) and part.strip()]
    return "\n\n".join(blocks)


def _resolve_support_file_path(config_dir: Path, raw_path: object, *, label: str) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path.strip())
    attempted_paths: list[Path] = []
    if path.is_absolute():
        resolved = path.resolve()
        attempted_paths.append(resolved)
        if not resolved.exists():
            raise FileNotFoundError(f"{label} file not found: {resolved}")
        if not resolved.is_file():
            raise ValueError(f"{label} path is not a file: {resolved}")
        return resolved

    resolved = (config_dir / path).resolve()
    attempted_paths.append(resolved)
    if resolved.exists():
        if not resolved.is_file():
            raise ValueError(f"{label} path is not a file: {resolved}")
        return resolved

    builtin = _resolve_builtin_support_file(path)
    if builtin is not None:
        return builtin

    attempted = ", ".join(str(candidate) for candidate in attempted_paths)
    raise FileNotFoundError(f"{label} file not found: {attempted}")


def _resolve_builtin_support_file(path: Path) -> Path | None:
    parts = path.parts
    if not parts:
        return None
    if parts[0] == "prompt-library":
        resource_parts = ("prompt_library", *parts[1:])
    elif parts[0] == "prompt_library":
        resource_parts = parts
    else:
        return None

    try:
        candidate = resources.files("openchami_coding_agent").joinpath(*resource_parts)
    except ModuleNotFoundError:
        return None
    if not candidate.is_file():
        return None
    return Path(candidate)


def _load_support_text(config_dir: Path, raw_path: object, *, label: str) -> str:
    path = _resolve_support_file_path(config_dir, raw_path, label=label)
    if path is None:
        return ""
    return path.read_text(encoding="utf-8").strip()


def hydrate_config_support_files(raw: dict[str, Any], *, config_dir: Path) -> dict[str, Any]:
    hydrated = dict(raw)

    agent = dict(hydrated.get("agent", {}) or {})
    for inline_key, path_key in _AGENT_APPENDIX_PATH_FIELDS.items():
        agent[inline_key] = _merge_text_blocks(
            agent.get(inline_key),
            _load_support_text(config_dir, agent.get(path_key), label=f"agent.{path_key}"),
        )
    if agent:
        hydrated["agent"] = agent

    repos: list[dict[str, Any]] = []
    for raw_repo in hydrated.get("repos", []) or []:
        repo = dict(raw_repo)
        brief_label = f"repo {repo.get('name', '<unknown>')} brief_path"
        repo["brief"] = _merge_text_blocks(
            repo.get("brief"),
            _load_support_text(
                config_dir,
                repo.get("brief_path"),
                label=brief_label,
            ),
        )
        repos.append(repo)
    if repos:
        hydrated["repos"] = repos

    return hydrated


def resolve_workspace(
    raw: dict[str, Any],
    cli_workspace: str | None = None,
    resume: bool = False,
) -> tuple[Path, bool]:
    requested = cli_workspace or raw.get("workspace") or raw.get("restart_workspace")
    workspace_root = raw.get("workspace_root") or DEFAULT_WORKSPACE_ROOT
    run_cwd = Path.cwd().resolve()

    def ensure_within_run_cwd(path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(run_cwd)
        except ValueError as exc:
            raise ValueError(
                f"Workspace must be inside launch directory {run_cwd}; got {resolved}"
            ) from exc
        return resolved

    if requested:
        ws = Path(requested)
        if not ws.is_absolute():
            ws = (run_cwd / ws).resolve()
        ws = ensure_within_run_cwd(ws)
        reused = ws.exists()
        if resume and not reused:
            raise FileNotFoundError(f"Requested existing workspace does not exist: {ws}")
        ws.mkdir(parents=True, exist_ok=True)
        return ws, reused

    root = Path(workspace_root)
    if not root.is_absolute():
        root = (run_cwd / root).resolve()
    root = ensure_within_run_cwd(root)
    root.mkdir(parents=True, exist_ok=True)

    while True:
        project_slug = slugify(raw.get("project", "openchami-task"))
        candidate = root / generate_workspace_name(project_slug)
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate, False


def ensure_within_workspace(path: Path, workspace: Path) -> Path:
    resolved = path.resolve()
    workspace_resolved = workspace.resolve()
    try:
        resolved.relative_to(workspace_resolved)
    except ValueError as exc:
        raise ValueError(
            f"Path escapes workspace containment: {resolved} is outside {workspace_resolved}"
        ) from exc
    return resolved


def _repo_read_only_flag(raw_repo: dict[str, Any]) -> bool:
    return bool(raw_repo.get("read_only", raw_repo.get("read-only", False)))


def resolve_repo(workspace: Path, raw_repo: dict[str, Any]) -> RepoSpec:
    if "name" not in raw_repo:
        raise ValueError("Each repo entry must include 'name'.")
    launch_cwd = Path.cwd().resolve()
    source_path: Path | None = None

    if raw_repo.get("path"):
        requested_path = Path(raw_repo["path"])
        resolved_requested = (
            requested_path if requested_path.is_absolute() else (launch_cwd / requested_path)
        )
        resolved_requested = resolved_requested.resolve()

        try:
            path = ensure_within_workspace(resolved_requested, workspace)
        except ValueError:
            source_path = resolved_requested
            path = ensure_within_workspace((workspace / "repos" / raw_repo["name"]), workspace)
    else:
        path = ensure_within_workspace((workspace / "repos" / raw_repo["name"]), workspace)

    return RepoSpec(
        name=raw_repo["name"],
        path=path,
        source_path=source_path,
        url=raw_repo.get("url"),
        branch=raw_repo.get("branch"),
        checkout=bool(raw_repo.get("checkout", False)),
        language=raw_repo.get("language", "generic"),
        description=raw_repo.get("description", ""),
        brief=raw_repo.get("brief", ""),
        checks=list(raw_repo.get("checks") or []),
        read_only=_repo_read_only_flag(raw_repo),
    )


def parse_config(
    config_path: Path, cli_workspace: str | None = None, resume: bool = False
) -> AgentConfig:
    original_raw = to_plain_data(load_yaml_config(str(config_path)))
    raw = hydrate_config_support_files(original_raw, config_dir=config_path.resolve().parent)
    workspace, workspace_reused = resolve_workspace(raw, cli_workspace=cli_workspace, resume=resume)
    setup_workspace(str(workspace), project=str(raw.get("project") or "run"))

    repos = [resolve_repo(workspace, r) for r in raw.get("repos", [])]

    cfg = AgentConfig.from_raw(
        raw,
        workspace=workspace,
        workspace_reused=workspace_reused,
        repos=repos,
    )
    cfg.config_path = config_path.resolve()
    cfg.raw_config = dict(original_raw)
    return cfg


def _load_workspace_source_config(workspace: Path) -> tuple[dict[str, Any], Path | None]:
    artifact_path = (workspace / DEFAULT_SOURCE_CONFIG_YAML).resolve()
    if not artifact_path.exists() or not artifact_path.is_file():
        return {}, None

    payload = to_plain_data(load_yaml_config(str(artifact_path)))
    if not isinstance(payload, dict):
        return {}, None

    raw_config = payload.get("raw_config")
    config_path = payload.get("config_path")
    resolved_path = Path(str(config_path)).resolve() if isinstance(config_path, str) and config_path else None
    return (dict(raw_config) if isinstance(raw_config, dict) else {}), resolved_path


def _synthesized_workspace_analysis_raw(
    workspace: Path,
    *,
    model_name: str,
) -> dict[str, Any]:
    summary_path = (workspace / DEFAULT_SUMMARY_JSON).resolve()
    summary_payload = to_plain_data(load_yaml_config(str(summary_path))) if summary_path.exists() else {}
    if not isinstance(summary_payload, dict):
        summary_payload = {}

    repos_dir = (workspace / "repos").resolve()
    repo_entries: list[dict[str, Any]] = []
    if repos_dir.exists():
        for child in sorted(repos_dir.iterdir()):
            if not child.is_dir():
                continue
            repo_entries.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "checkout": False,
                }
            )

    project_name = str(summary_payload.get("project") or workspace.name)
    return {
        "project": project_name,
        "problem": (
            "Investigate this prior Marvin workspace, identify likely failure causes or "
            "misconfiguration, and recommend YAML updates for the next run."
        ),
        "mode": "analyze_workspace",
        "workspace": str(workspace),
        "planning": {"mode": "hierarchical"},
        "task": {
            "execute_after_plan": False,
            "confirm_before_execute": False,
        },
        "models": {
            "default": model_name,
            "planner": model_name,
        },
        "repos": repo_entries,
    }


def build_workspace_analysis_config(
    workspace_path: Path,
    *,
    config_path: Path | None = None,
    model_name: str = "openai:gpt-5.4",
) -> AgentConfig:
    workspace = workspace_path.resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise FileNotFoundError(f"Workspace does not exist: {workspace}")

    if config_path is not None:
        cfg = parse_config(config_path.resolve(), cli_workspace=str(workspace), resume=True)
    else:
        raw_config, stored_config_path = _load_workspace_source_config(workspace)
        if raw_config:
            hydrated = (
                hydrate_config_support_files(raw_config, config_dir=stored_config_path.parent)
                if stored_config_path is not None and stored_config_path.exists()
                else dict(raw_config)
            )
            setup_workspace(str(workspace), project=str(hydrated.get("project") or "run"))
            repos = [resolve_repo(workspace, repo) for repo in hydrated.get("repos", [])]
            cfg = AgentConfig.from_raw(
                hydrated,
                workspace=workspace,
                workspace_reused=True,
                repos=repos,
            )
            cfg.config_path = stored_config_path
            cfg.raw_config = dict(raw_config)
        else:
            raw = _synthesized_workspace_analysis_raw(workspace, model_name=model_name)
            setup_workspace(str(workspace), project=str(raw.get("project") or "run"))
            repos = [resolve_repo(workspace, repo) for repo in raw.get("repos", [])]
            cfg = AgentConfig.from_raw(
                raw,
                workspace=workspace,
                workspace_reused=True,
                repos=repos,
            )
            cfg.raw_config = dict(raw)
            cfg.config_path = None

    cfg.mode = "analyze_workspace"
    cfg.planning_mode = "hierarchical"
    cfg.confirm_before_execute = False
    cfg.execute_after_plan = False
    cfg.planner_model = cfg.planner_model or model_name
    return cfg


def ensure_repo(repo: RepoSpec) -> None:
    repo.path.parent.mkdir(parents=True, exist_ok=True)

    if not repo.path.exists() and repo.source_path is not None:
        if not repo.source_path.exists():
            raise FileNotFoundError(
                f"Source path for repo '{repo.name}' does not exist: {repo.source_path}"
            )
        if (repo.source_path / ".git").exists():
            code, out, err = run_command(["git", "clone", str(repo.source_path), str(repo.path)])
            if code != 0:
                shutil.copytree(repo.source_path, repo.path)
                clone_error = err.strip() or out.strip()
                emit_panel(
                    (
                        f"{repo.name}: git clone from local source failed; "
                        f"falling back to a direct file copy.\n{clone_error}"
                    ),
                    border_style="yellow",
                )
        else:
            shutil.copytree(repo.source_path, repo.path)

    if not repo.checkout:
        if not repo.path.exists():
            raise RuntimeError(
                f"Repo '{repo.name}' is not available in workspace and "
                "checkout=false with no materialized source. "
                "Provide repos[].path, set checkout=true with repos[].url, "
                "or create the repo in the workspace first."
            )
        return

    if not repo.path.exists():
        if not repo.url:
            raise RuntimeError(
                f"Repo '{repo.name}' does not exist locally and no URL was provided."
            )
        cmd = ["git", "clone"]
        if repo.branch:
            cmd.extend(["--branch", repo.branch])
        cmd.extend([repo.url, str(repo.path)])
        code, out, err = run_command(cmd)
        if code != 0:
            raise RuntimeError(f"git clone failed for {repo.name}\n{out}\n{err}")
        return

    if repo.branch:
        code, current, _ = run_command(
            ["git", "-C", str(repo.path), "rev-parse", "--abbrev-ref", "HEAD"]
        )
        if code == 0 and current.strip() == repo.branch:
            return
        code, _, err = run_command(["git", "-C", str(repo.path), "checkout", repo.branch])
        if code != 0:
            emit_panel(
                (
                    f"{repo.name}: unable to checkout {repo.branch}; "
                    f"continuing on the current branch.\n{err.strip()}"
                ),
                border_style="yellow",
            )


def repo_listing(repos: list[RepoSpec]) -> str:
    lines: list[str] = []
    for repo in repos:
        bits = [f"- {repo.name}: {repo.path}"]
        bits.append(f"role={repo.role_label}")
        if repo.branch:
            bits.append(f"branch={repo.branch}")
        if repo.language:
            bits.append(f"language={repo.language}")
        if repo.description:
            bits.append(_compact_text(repo.description, 180))
        if repo.brief:
            bits.append(f"brief={_compact_text(repo.brief, 420)}")
        lines.append(" | ".join(bits))
    return "\n".join(lines)


def default_working_directory(cfg: AgentConfig) -> Path | None:
    execution_repos = cfg.execution_repos
    if not execution_repos:
        return cfg.workspace
    if len(execution_repos) == 1:
        return execution_repos[0].path
    return cfg.workspace


def render_status(cfg: AgentConfig) -> None:
    from rich.table import Table

    table = Table(title=f"{AGENT_NAME} coding agent", show_header=True)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Project", cfg.project)
    table.add_row("Mode", cfg.mode)
    table.add_row("Planning mode", cfg.planning_mode)
    table.add_row("Workspace", str(cfg.workspace))
    table.add_row("Workspace reused", "yes" if cfg.workspace_reused else "no")
    table.add_row("Proposal", cfg.proposal_markdown)
    table.add_row("Plan JSON", cfg.plan_json)
    table.add_row("Summary JSON", cfg.summary_json)
    table.add_row("Confirm before execute", "yes" if cfg.confirm_before_execute else "no")
    table.add_row("Check parallelism", str(cfg.max_parallel_checks))
    table.add_row("Check retries", str(cfg.max_check_retries))
    table.add_row("Skip failed repos", "yes" if cfg.skip_failed_repos else "no")
    table.add_row("Resume execution state", "yes" if cfg.resume_execution_state else "no")
    table.add_row("Commit each step", "yes" if cfg.commit_each_step else "no")
    table.add_row("Verbose IO", "yes" if cfg.verbose_io else "no")
    table.add_row("Execution repos", str(len(cfg.execution_repos)))
    table.add_row("Reference repos", str(len(cfg.reference_repos)))
    emit_table(table)

    safe_execution = sanitize_for_logging(cfg.execution)
    if safe_execution:
        emit_panel(str(safe_execution), border_style="blue", title="Execution config")
