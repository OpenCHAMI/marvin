"""Interactive CLI helpers for generating Marvin YAML configs."""

from __future__ import annotations

import argparse
import html
import re
import sys
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO
from urllib import error, parse, request

from .ursa_compat import setup_llm
from .utils import invoke_agent, render_yaml_text, slugify

DEFAULT_MODEL = "openai:gpt-5.4"
GITHUB_SEARCH_LIMIT = 6
OPENCHAMI_GITHUB_PREFIX = "https://github.com/OpenCHAMI/"
SOURCE_KIND_CHOICES = (
    "github_issue",
    "feature_description",
    "architectural_proposal",
    "other",
)
SOURCE_KIND_LABELS = {
    "github_issue": "GitHub issue",
    "feature_description": "Feature description",
    "architectural_proposal": "Architectural proposal",
    "other": "Other",
}


@dataclass
class RepoInitSpec:
    name: str
    url: str = ""
    path: str = ""
    branch: str = ""
    checkout: bool = True
    language: str = "generic"
    description: str = ""
    checks: list[str] = field(default_factory=list)


@dataclass
class ConfigInitSpec:
    project: str
    source_kind: str
    problem: str
    repos: list[RepoInitSpec]
    output_path: Path
    source_reference: str = ""
    deliverables: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    model: str = DEFAULT_MODEL
    planning_mode: str = "single"
    execute_after_plan: bool = True
    confirm_before_execute: bool = True
    confirm_timeout_sec: int = 45
    commit_each_step: bool = True


class InitGenerationAgent:
    def __init__(self, llm: Any):
        self.llm = llm

    def invoke(self, payload: dict[str, Any]) -> Any:
        return self.llm.invoke(payload.get("messages") or [])


def add_init_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Force the interactive wizard even when enough inputs exist for auto-generation.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model to use for agent-assisted config generation.",
    )
    parser.add_argument(
        "--output",
        help="Where to write the generated YAML config. Defaults to <project-slug>.yaml.",
    )
    parser.add_argument(
        "--source-file",
        help="Optional file containing a starting issue, proposal, or feature description.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )


def _println(output: TextIO, text: str = "") -> None:
    output.write(text + "\n")


def _load_source_text(source_path: Path) -> str:
    return textwrap.dedent(source_path.read_text(encoding="utf-8")).strip()


def _resolve_output_path(output_value: str | None, cwd: Path | None) -> Path:
    base = (cwd or Path.cwd()).resolve()
    value = output_value or "marvin-task.yaml"
    path = Path(value)
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def _resolve_source_path(source_file: str) -> Path:
    return Path(source_file).expanduser().resolve()


def _prompt_text(
    prompt: str,
    *,
    input_func: Callable[[str], str],
    output: TextIO,
    default: str = "",
    required: bool = False,
) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        raw = input_func(f"{prompt}{suffix}: ").strip()
        if raw:
            return raw
        if default:
            return default
        if not required:
            return ""
        _println(output, "A value is required.")


def _prompt_bool(
    prompt: str,
    *,
    input_func: Callable[[str], str],
    output: TextIO,
    default: bool,
) -> bool:
    default_hint = "Y/n" if default else "y/N"
    while True:
        raw = input_func(f"{prompt} [{default_hint}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        _println(output, "Please answer yes or no.")


def _prompt_choice(
    prompt: str,
    *,
    input_func: Callable[[str], str],
    output: TextIO,
    choices: tuple[str, ...],
    default: str,
) -> str:
    _println(output, f"Options: {', '.join(choices)}")
    while True:
        raw = input_func(f"{prompt} [{default}]: ").strip().lower()
        value = raw or default
        if value in choices:
            return value
        _println(output, f"Please choose one of: {', '.join(choices)}")


def _prompt_multiline(
    prompt: str,
    *,
    input_func: Callable[[str], str],
    output: TextIO,
    default: str = "",
    required: bool = False,
) -> str:
    while True:
        _println(output, prompt)
        _println(output, "Finish with a single '.' on its own line.")
        if default:
            _println(output, "Press Enter on the first line to keep the provided text.")

        lines: list[str] = []
        while True:
            line = input_func("> ")
            if default and not lines and line == "":
                return default.strip()
            if line.strip() == ".":
                break
            lines.append(line.rstrip())

        text = "\n".join(lines).strip()
        if text:
            return text
        if not required:
            return default.strip() if default else ""
        _println(output, "A value is required.")


def _prompt_list(
    prompt: str,
    *,
    input_func: Callable[[str], str],
    output: TextIO,
) -> list[str]:
    text = _prompt_multiline(prompt, input_func=input_func, output=output)
    items: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        items.append(line)
    return items


def _build_repo_dict(repo: RepoInitSpec) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": repo.name}
    if repo.url:
        payload["url"] = repo.url
    if repo.path:
        payload["path"] = repo.path
    if repo.branch:
        payload["branch"] = repo.branch
    payload["checkout"] = bool(repo.checkout)
    if repo.language:
        payload["language"] = repo.language
    if repo.description:
        payload["description"] = repo.description
    if repo.checks:
        payload["checks"] = repo.checks
    return payload


def _default_deliverables(source_kind: str) -> list[str]:
    deliverables = [
        "Implement the requested behavior with minimal, focused changes.",
        "Add or update focused tests that cover the change.",
        "Update documentation when user-visible behavior changes.",
    ]
    if source_kind == "architectural_proposal":
        deliverables.append("Align the implementation with the architectural proposal intent.")
    return deliverables


def _default_notes(source_kind: str, source_reference: str) -> list[str]:
    notes = [
        f"Starting point: {SOURCE_KIND_LABELS.get(source_kind, source_kind)}.",
        "Prefer incremental changes that are easy to validate and review.",
    ]
    if source_reference:
        notes.append(f"Source reference: {source_reference}")
    if source_kind == "github_issue":
        notes.append(
            "Use the issue details as the baseline source of truth unless code proves otherwise."
        )
    elif source_kind == "architectural_proposal":
        notes.append(
            "Preserve the intent of the proposal while keeping implementation steps concrete."
        )
    return notes


def _fetch_url(url: str, *, timeout: int = 15) -> str:
    req = request.Request(
        url,
        headers={
            "User-Agent": "marvin-config-init/0.1",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _html_to_text(page_html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", page_html)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _page_title(page_html: str, fallback: str) -> str:
    match = re.search(r"<title>(.*?)</title>", page_html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return fallback
    title = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()
    return title or fallback


def extract_openchami_links(page_html: str, *, limit: int = GITHUB_SEARCH_LIMIT) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r'href="([^"]+)"', page_html):
        href = html.unescape(match.group(1))
        if not href.startswith("/OpenCHAMI/"):
            continue
        url = f"https://github.com{href.split('#', 1)[0].split('?', 1)[0]}"
        lower = url.lower()
        if not lower.startswith(OPENCHAMI_GITHUB_PREFIX.lower()):
            continue
        if any(part in lower for part in ("/stargazers", "/watchers", "/network", "/search")):
            continue
        if url in seen:
            continue
        seen.add(url)
        links.append(url)
        if len(links) >= limit:
            break
    return links


def search_openchami_links(
    query: str,
    *,
    search_type: str = "repositories",
    limit: int = GITHUB_SEARCH_LIMIT,
) -> list[str]:
    encoded_query = parse.quote(f"org:OpenCHAMI {query}")
    url = f"https://github.com/search?q={encoded_query}&type={search_type}"
    try:
        page_html = _fetch_url(url)
    except (error.URLError, TimeoutError, OSError):
        return []
    return extract_openchami_links(page_html, limit=limit)


def fetch_openchami_context_pages(
    urls: list[str],
    *,
    limit: int = GITHUB_SEARCH_LIMIT,
) -> list[dict[str, str]]:
    context_pages: list[dict[str, str]] = []
    for url in urls[:limit]:
        if not url.lower().startswith(OPENCHAMI_GITHUB_PREFIX.lower()):
            continue
        try:
            page_html = _fetch_url(url)
        except (error.URLError, TimeoutError, OSError):
            continue
        text = _html_to_text(page_html)
        if not text:
            continue
        context_pages.append(
            {
                "url": url,
                "title": _page_title(page_html, fallback=url.rsplit("/", 1)[-1]),
                "text": text[:4000],
            }
        )
    return context_pages


def discover_openchami_context(
    source_text: str,
    *,
    project: str,
    repo_names: list[str],
    limit: int = GITHUB_SEARCH_LIMIT,
) -> list[dict[str, str]]:
    urls: list[str] = []
    seen: set[str] = set()

    def add_url(url: str) -> None:
        if not url.lower().startswith(OPENCHAMI_GITHUB_PREFIX.lower()):
            return
        if url in seen:
            return
        seen.add(url)
        urls.append(url)

    for repo_name in repo_names[:3]:
        add_url(f"{OPENCHAMI_GITHUB_PREFIX}{repo_name}")
        for search_type in ("repositories", "issues", "pullrequests"):
            for url in search_openchami_links(repo_name, search_type=search_type, limit=2):
                add_url(url)

    for term in (project, " ".join(re.findall(r"RFC\s+\d+", source_text)[:1])):
        query = term.strip()
        if not query:
            continue
        for url in search_openchami_links(query, search_type="issues", limit=2):
            add_url(url)

    return fetch_openchami_context_pages(urls, limit=limit)


def _stem_to_title(value: str) -> str:
    words = [part for part in re.split(r"[-_]+", value.strip()) if part]
    return " ".join(word.upper() if word.isupper() else word.capitalize() for word in words)


def infer_source_kind(source_path: Path, source_text: str) -> str:
    haystack = f"{source_path.name}\n{source_text}".lower()
    if "github.com" in haystack and ("/issues/" in haystack or " issue #" in haystack):
        return "github_issue"
    if any(
        token in haystack
        for token in ("architecture", "architectural", "proposal", "design doc")
    ):
        return "architectural_proposal"
    if "issue #" in haystack:
        return "github_issue"
    return "feature_description"


def infer_project_name(source_text: str, output_path: Path, source_path: Path) -> str:
    patterns = [
        r"working in the .*? on the `([^`]+)` service",
        r"on the `([^`]+)` service",
        r"\bservice\s+`([^`]+)`",
        r"`([^`]+)` service",
        r"`([^`]+)` repository",
        r"`([^`]+)` repo",
        r"\b([A-Za-z0-9_-]+) service\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, source_text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip("` ")
            if candidate:
                return candidate

    for line in source_text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            if len(stripped) <= 80:
                return stripped.rstrip(".:")
            break

    output_name = re.sub(
        r"(?:-task|-issue-\d+|-config)$",
        "",
        output_path.stem,
        flags=re.IGNORECASE,
    )
    if output_name:
        return _stem_to_title(output_name)
    return _stem_to_title(source_path.stem)


def infer_repo_specs(source_text: str, project: str) -> list[RepoInitSpec]:
    repo_names: list[str] = []
    for pattern in (
        r"`([^`]+)` service",
        r"`([^`]+)` repository",
        r"`([^`]+)` repo",
    ):
        for match in re.finditer(pattern, source_text, flags=re.IGNORECASE):
            name = match.group(1).strip()
            if name and name not in repo_names:
                repo_names.append(name)

    if not repo_names:
        slug = slugify(project).replace("-task", "")
        if slug:
            repo_names.append(slug)

    return [
        RepoInitSpec(
            name=name,
            description="Inferred from source material; review before running.",
        )
        for name in repo_names[:3]
    ]


def make_init_llm(model_name: str):
    return setup_llm(model_name, {})


def build_config_generation_prompt(
    *,
    source_path: Path,
    output_path: Path,
    source_text: str,
    seed_spec: ConfigInitSpec,
    web_context: list[dict[str, str]],
) -> str:
    web_context_text = "\n\n".join(
        (
            f"URL: {item['url']}\n"
            f"Title: {item['title']}\n"
            f"Excerpt: {item['text']}"
        )
        for item in web_context
    ) or "No additional OpenCHAMI GitHub pages were retrieved."

    seed_repo_lines = "\n".join(
        f"- {repo.name}: {repo.description or 'no description'}"
        for repo in seed_spec.repos
    ) or "- none inferred"

    return f"""
You are generating a Marvin YAML config for a later coding run.

Requirements:
1. Return only YAML. No markdown fences, no commentary.
2. The YAML must match this shape:
   project: string
   problem: multiline string
   mode: plan_and_execute
   repos:
     - name: string
       url: optional string
       path: optional string
       branch: optional string
       checkout: optional boolean
       language: optional string
       description: optional string
       checks: optional list of shell commands
   models:
     default: string
   task:
     execute_after_plan: boolean
     confirm_before_execute: boolean
     confirm_timeout_sec: integer
     deliverables: list of strings
     plan_requirements: list of strings
     execution_requirements: list of strings
     notes: list of strings
   execution:
     commit_each_step: boolean
3. Make the config specific and useful, not generic boilerplate.
4. Summarize the source material into a concise but detailed problem statement
     instead of copying it verbatim.
5. Use web evidence only from github.com/OpenCHAMI links supplied below.
6. If repo checks are unknown, omit them rather than inventing implausible commands.
7. Prefer concrete repo URLs under OpenCHAMI when the evidence supports them.
8. Keep backward-compatible Marvin defaults unless the source strongly suggests otherwise.

Source file: {source_path}
Output file target: {output_path.name}
Seed project guess: {seed_spec.project}
Seed source kind: {seed_spec.source_kind}
Seed repo guesses:
{seed_repo_lines}

Source material:
{source_text}

OpenCHAMI GitHub context:
{web_context_text}
""".strip()


def _extract_yaml_text(response_text: str) -> str:
    match = re.search(
        r"```(?:yaml|yml)?\s*(.*?)```",
        response_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return response_text.strip()


def _coerce_repo_specs(raw_repos: Any, fallback_repos: list[RepoInitSpec]) -> list[RepoInitSpec]:
    repos: list[RepoInitSpec] = []
    if isinstance(raw_repos, list):
        for item in raw_repos:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            checks = item.get("checks") if isinstance(item.get("checks"), list) else []
            repos.append(
                RepoInitSpec(
                    name=name,
                    url=str(item.get("url") or "").strip(),
                    path=str(item.get("path") or "").strip(),
                    branch=str(item.get("branch") or "").strip(),
                    checkout=bool(item.get("checkout", True)),
                    language=str(item.get("language") or "generic").strip() or "generic",
                    description=str(item.get("description") or "").strip(),
                    checks=[str(check).strip() for check in checks if str(check).strip()],
                )
            )
    return repos or fallback_repos


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_generated_payload(
    raw_payload: dict[str, Any],
    *,
    source_path: Path,
    output_path: Path,
    seed_spec: ConfigInitSpec,
    model_name: str,
    web_context: list[dict[str, str]],
) -> dict[str, Any]:
    task = raw_payload.get("task") if isinstance(raw_payload.get("task"), dict) else {}
    execution = (
        raw_payload.get("execution") if isinstance(raw_payload.get("execution"), dict) else {}
    )
    repos = _coerce_repo_specs(raw_payload.get("repos"), seed_spec.repos)
    source_kind = (
        str(raw_payload.get("source_kind") or seed_spec.source_kind).strip()
        or seed_spec.source_kind
    )
    project = str(raw_payload.get("project") or seed_spec.project).strip() or seed_spec.project
    problem = str(raw_payload.get("problem") or seed_spec.problem).strip() or seed_spec.problem
    notes = _coerce_str_list(task.get("notes")) or seed_spec.notes or _default_notes(
        source_kind,
        str(source_path),
    )

    github_urls = [item["url"] for item in web_context[:3] if item.get("url")]
    if github_urls:
        notes.append("OpenCHAMI references reviewed: " + ", ".join(github_urls))
    if not any(str(source_path) in note for note in notes):
        notes.append(f"Source reference: {source_path}")

    payload = build_config_payload(
        ConfigInitSpec(
            project=project,
            source_kind=source_kind,
            problem=problem,
            repos=repos,
            output_path=output_path,
            source_reference=str(source_path),
            deliverables=_coerce_str_list(task.get("deliverables")),
            notes=notes,
            model=model_name,
            execute_after_plan=bool(task.get("execute_after_plan", True)),
            confirm_before_execute=bool(task.get("confirm_before_execute", True)),
            confirm_timeout_sec=max(1, int(task.get("confirm_timeout_sec", 45) or 45)),
            commit_each_step=bool(execution.get("commit_each_step", True)),
        )
    )

    plan_requirements = _coerce_str_list(task.get("plan_requirements"))
    if plan_requirements:
        payload["task"]["plan_requirements"] = plan_requirements
    execution_requirements = _coerce_str_list(task.get("execution_requirements"))
    if execution_requirements:
        payload["task"]["execution_requirements"] = execution_requirements
    generated_mode = str(raw_payload.get("mode") or "plan_and_execute").strip()
    if generated_mode in {"plan", "execute", "plan_and_execute"}:
        payload["mode"] = generated_mode

    return payload


def generate_agent_payload_from_source(
    args: argparse.Namespace,
    *,
    cwd: Path | None = None,
) -> dict[str, Any]:
    source_path = _resolve_source_path(args.source_file)
    output_path = _resolve_output_path(args.output, cwd)
    source_text = _load_source_text(source_path)
    seed_spec = auto_config_spec_from_source(args, cwd=cwd)
    repo_names = [repo.name for repo in seed_spec.repos]
    web_context = discover_openchami_context(
        source_text,
        project=seed_spec.project,
        repo_names=repo_names,
    )
    prompt = build_config_generation_prompt(
        source_path=source_path,
        output_path=output_path,
        source_text=source_text,
        seed_spec=seed_spec,
        web_context=web_context,
    )
    llm = make_init_llm(str(args.model or DEFAULT_MODEL))
    agent = InitGenerationAgent(llm)
    response = invoke_agent(agent, prompt, verbose_io=False)
    raw_text = _extract_yaml_text(response.content)
    import yaml

    raw_payload = yaml.safe_load(raw_text)
    if not isinstance(raw_payload, dict):
        raise ValueError("Config-generation agent did not return a YAML mapping.")
    return normalize_generated_payload(
        raw_payload,
        source_path=source_path,
        output_path=output_path,
        seed_spec=seed_spec,
        model_name=str(args.model or DEFAULT_MODEL),
        web_context=web_context,
    )


def auto_config_spec_from_source(
    args: argparse.Namespace,
    *,
    cwd: Path | None = None,
) -> ConfigInitSpec:
    cwd = (cwd or Path.cwd()).resolve()
    if not args.source_file:
        raise ValueError("Auto-generation requires --source-file.")
    if not args.output:
        raise ValueError("Auto-generation requires --output.")

    source_path = _resolve_source_path(args.source_file)
    source_text = _load_source_text(source_path)
    output_path = _resolve_output_path(args.output, cwd)

    source_kind = infer_source_kind(source_path, source_text)
    project = infer_project_name(source_text, output_path, source_path)
    notes = _default_notes(source_kind, str(source_path))
    notes.insert(
        1,
        "Generated non-interactively from --source-file; review inferred repos and checks.",
    )

    return ConfigInitSpec(
        project=project,
        source_kind=source_kind,
        source_reference=str(source_path),
        problem=source_text,
        repos=infer_repo_specs(source_text, project),
        output_path=output_path,
        notes=notes,
    )


def build_config_payload(spec: ConfigInitSpec) -> dict[str, Any]:
    output_stem = spec.output_path.stem
    return {
        "project": spec.project,
        "problem": spec.problem,
        "mode": "plan_and_execute",
        "planning": {"mode": spec.planning_mode},
        "repos": [_build_repo_dict(repo) for repo in spec.repos],
        "models": {"default": spec.model},
        "task": {
            "execute_after_plan": spec.execute_after_plan,
            "confirm_before_execute": spec.confirm_before_execute,
            "confirm_timeout_sec": spec.confirm_timeout_sec,
            "deliverables": spec.deliverables or _default_deliverables(spec.source_kind),
            "plan_requirements": [
                "Identify the exact code paths to modify before editing.",
                "Keep the implementation incremental and safe.",
                "Include explicit validation commands for each meaningful step.",
                "Call out assumptions taken from the provided source material.",
            ],
            "execution_requirements": [
                "Keep changes minimal and focused on the requested outcome.",
                "Preserve existing behavior unless the request explicitly changes it.",
                "Run focused tests after each meaningful change.",
                "Report unresolved risks or follow-up work clearly.",
            ],
            "notes": spec.notes or _default_notes(spec.source_kind, spec.source_reference),
        },
        "execution": {
            "max_parallel_checks": 2,
            "max_check_retries": 1,
            "skip_failed_repos": False,
            "check_command_timeout_sec": 1800,
            "check_output_tail_chars": 16000,
            "resume_execution_state": True,
            "verbose_io": False,
            "commit_each_step": spec.commit_each_step,
        },
        "outputs": {
            "proposal_markdown": f"docs/{output_stem}-proposal.md",
            "plan_json": f"artifacts/{output_stem}-plan.json",
            "summary_json": f"artifacts/{output_stem}-summary.json",
            "executor_progress_json": f"artifacts/{output_stem}-progress.json",
        },
    }


def _collect_repo_specs(
    *,
    input_func: Callable[[str], str],
    output: TextIO,
) -> list[RepoInitSpec]:
    repos: list[RepoInitSpec] = []
    _println(output)
    _println(output, "Repository setup")
    while True:
        name = _prompt_text(
            "Repository name",
            input_func=input_func,
            output=output,
            required=not repos,
        )
        if not name:
            break
        url = _prompt_text("Repository URL", input_func=input_func, output=output)
        path = _prompt_text(
            "Local path override",
            input_func=input_func,
            output=output,
        )
        branch_default = "main" if url else ""
        branch = _prompt_text(
            "Branch",
            input_func=input_func,
            output=output,
            default=branch_default,
        )
        checkout_default = not bool(path)
        checkout = _prompt_bool(
            "Clone or materialize this repo into the workspace",
            input_func=input_func,
            output=output,
            default=checkout_default,
        )
        language = _prompt_text(
            "Primary language",
            input_func=input_func,
            output=output,
            default="generic",
        )
        description = _prompt_text(
            "Short repository description",
            input_func=input_func,
            output=output,
        )
        checks = _prompt_list(
            "Enter repository validation commands, one per line.",
            input_func=input_func,
            output=output,
        )
        repos.append(
            RepoInitSpec(
                name=name,
                url=url,
                path=path,
                branch=branch,
                checkout=checkout,
                language=language,
                description=description,
                checks=checks,
            )
        )
        if not _prompt_bool(
            "Add another repository",
            input_func=input_func,
            output=output,
            default=False,
        ):
            break
    return repos


def collect_config_spec(
    args: argparse.Namespace,
    *,
    input_func: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
    cwd: Path | None = None,
) -> ConfigInitSpec:
    cwd = (cwd or Path.cwd()).resolve()
    source_text = ""
    if args.source_file:
        source_text = _load_source_text(_resolve_source_path(args.source_file))

    _println(output, "Marvin config wizard")
    _println(output, "This writes a YAML config for later runs. Miserable, but useful.")
    _println(output)

    project = _prompt_text("Project name", input_func=input_func, output=output, required=True)
    source_kind = _prompt_choice(
        "Starting point type",
        input_func=input_func,
        output=output,
        choices=SOURCE_KIND_CHOICES,
        default="feature_description",
    )
    source_reference = _prompt_text(
        "Source reference (URL, issue number, doc path, or similar)",
        input_func=input_func,
        output=output,
    )
    problem = _prompt_multiline(
        "Describe the requested work.",
        input_func=input_func,
        output=output,
        default=source_text,
        required=True,
    )
    repos = _collect_repo_specs(input_func=input_func, output=output)
    deliverables = _prompt_list(
        "Optional deliverables override. Leave empty to use sensible defaults.",
        input_func=input_func,
        output=output,
    )
    notes = _prompt_list(
        "Optional notes to preserve context or constraints. Leave empty for defaults.",
        input_func=input_func,
        output=output,
    )
    model = _prompt_text(
        "Default model",
        input_func=input_func,
        output=output,
        default=DEFAULT_MODEL,
    )
    planning_mode = _prompt_choice(
        "Planning mode",
        choices=("single", "hierarchical"),
        input_func=input_func,
        output=output,
        default="single",
    )
    execute_after_plan = _prompt_bool(
        "Execute automatically after planning",
        input_func=input_func,
        output=output,
        default=True,
    )
    confirm_before_execute = _prompt_bool(
        "Require confirmation before execution",
        input_func=input_func,
        output=output,
        default=True,
    )
    commit_each_step = _prompt_bool(
        "Commit each completed step when repos are git worktrees",
        input_func=input_func,
        output=output,
        default=True,
    )
    suggested_output = args.output or f"{slugify(project)}.yaml"
    output_path = Path(
        _prompt_text(
            "Output config path",
            input_func=input_func,
            output=output,
            default=suggested_output,
        )
    )
    if not output_path.is_absolute():
        output_path = (cwd / output_path).resolve()

    return ConfigInitSpec(
        project=project,
        source_kind=source_kind,
        source_reference=source_reference,
        problem=problem,
        repos=repos,
        output_path=output_path,
        deliverables=deliverables,
        notes=notes,
        model=model,
        planning_mode=planning_mode,
        execute_after_plan=execute_after_plan,
        confirm_before_execute=confirm_before_execute,
        commit_each_step=commit_each_step,
    )


def write_config_file(payload: dict[str, Any], output_path: Path, *, force: bool) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {output_path}")
    output_path.write_text(render_yaml_text(payload), encoding="utf-8")
    return output_path


def run_init_command(
    args: argparse.Namespace,
    *,
    input_func: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
    cwd: Path | None = None,
) -> int:
    auto_mode = bool(args.source_file and args.output and not getattr(args, "interactive", False))
    output_path = _resolve_output_path(args.output, cwd) if args.output else None
    if auto_mode:
        if output_path is not None and output_path.exists() and not args.force:
            _println(output, f"Refusing to overwrite existing file: {output_path}")
            return 1
        try:
            payload = generate_agent_payload_from_source(args, cwd=cwd)
        except Exception as exc:
            _println(output, f"Agent-assisted config generation failed: {exc}")
            _println(
                output,
                "Set the credentials required by the selected model, or rerun with "
                "--interactive to complete the wizard manually.",
            )
            return 1
    else:
        spec = collect_config_spec(args, input_func=input_func, output=output, cwd=cwd)
        payload = None

    if not auto_mode and spec.output_path.exists() and not args.force:
        if auto_mode:
            _println(output, f"Refusing to overwrite existing file: {spec.output_path}")
            return 1
        should_overwrite = _prompt_bool(
            f"{spec.output_path} already exists. Overwrite",
            input_func=input_func,
            output=output,
            default=False,
        )
        if not should_overwrite:
            _println(output, "Config creation cancelled.")
            return 1

    if auto_mode:
        if output_path is None:
            raise ValueError("Output path must be resolved in auto mode.")
        path = write_config_file(payload, output_path, force=True)
    else:
        payload = build_config_payload(spec)
        path = write_config_file(payload, spec.output_path, force=True)
    _println(output)
    _println(output, f"Wrote config: {path}")
    if auto_mode:
        _println(
            output,
            "Generated from source file with agent assistance and OpenCHAMI GitHub context; "
            "review the draft before running.",
        )
    _println(output, f"Run it with: marvin {path}")
    return 0