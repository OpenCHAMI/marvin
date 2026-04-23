"""Compatibility helpers for URSA imports and evolving call signatures."""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from typing import Any

from .ursa_adapter import ADAPTER

AGENT_MODULE_CANDIDATES = (
    "ursa.agents",
    "ursa",
)

UTILITY_MODULE_CANDIDATES = (
    "ursa.util.plan_execute_utils",
    "ursa.util.helperFunctions",
    "ursa.util",
    "ursa.workflows.planning_execution_workflow",
)


@cache
def _resolve_attribute(attribute: str, module_candidates: tuple[str, ...]) -> Any:
    return ADAPTER.resolve_attribute(attribute, module_candidates)


def _rename_kwargs(kwargs: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    renamed = dict(kwargs)
    for old_name, new_name in aliases.items():
        if old_name not in renamed or new_name in renamed:
            continue
        renamed[new_name] = renamed.pop(old_name)
    return renamed


def _call_with_compatible_kwargs(target: Callable[..., Any], **kwargs: Any) -> Any:
    return ADAPTER.call_with_compatible_kwargs(target, **kwargs)


def instantiate_agent(agent_class: type[Any], **kwargs: Any) -> Any:
    alias_sets = (
        {},
        {"llm": "model"},
        {"checkpointer": "checkpoint"},
        {"checkpointer": "checkpoint_saver"},
        {"llm": "model", "checkpointer": "checkpoint"},
        {"llm": "model", "checkpointer": "checkpoint_saver"},
    )
    last_error: Exception | None = None
    for aliases in alias_sets:
        try:
            return _call_with_compatible_kwargs(
                agent_class,
                **_rename_kwargs(kwargs, aliases),
            )
        except TypeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Unable to instantiate URSA agent {agent_class.__name__}")


def get_agent_class(*names: str) -> type[Any]:
    for name in names:
        try:
            return _resolve_attribute(name, AGENT_MODULE_CANDIDATES)
        except ImportError:
            continue
    joined = ", ".join(names)
    raise ImportError(f"Unable to resolve any URSA agent class from: {joined}")


def setup_llm(
    model_choice: str,
    models_cfg: dict[str, Any] | None = None,
    agent_name: str | None = None,
):
    fn = _resolve_attribute("setup_llm", UTILITY_MODULE_CANDIDATES)
    return _call_with_compatible_kwargs(
        fn,
        model_choice=model_choice,
        models_cfg=models_cfg,
        agent_name=agent_name,
    )


def generate_workspace_name(project: str = "run") -> str:
    fn = _resolve_attribute("generate_workspace_name", UTILITY_MODULE_CANDIDATES)
    return _call_with_compatible_kwargs(fn, project=project)


def load_yaml_config(path: str) -> Any:
    fn = _resolve_attribute("load_yaml_config", UTILITY_MODULE_CANDIDATES)
    return _call_with_compatible_kwargs(fn, path=path)


def setup_workspace(
    user_specified_workspace: str | None,
    project: str = "run",
    model_name: str = "openai:gpt-5-mini",
) -> Any:
    fn = _resolve_attribute("setup_workspace", UTILITY_MODULE_CANDIDATES)
    return _call_with_compatible_kwargs(
        fn,
        user_specified_workspace=user_specified_workspace,
        project=project,
        model_name=model_name,
    )


def timed_input_with_countdown(prompt: str, timeout: int) -> str | None:
    fn = _resolve_attribute("timed_input_with_countdown", UTILITY_MODULE_CANDIDATES)
    return _call_with_compatible_kwargs(fn, prompt=prompt, timeout=timeout)


def hash_plan(plan_steps: list[Any] | tuple[Any, ...]) -> str:
    fn = _resolve_attribute("hash_plan", UTILITY_MODULE_CANDIDATES)
    return _call_with_compatible_kwargs(fn, plan_steps=plan_steps)


def snapshot_sqlite_db(source: Any, destination: Any) -> Any:
    fn = _resolve_attribute("snapshot_sqlite_db", UTILITY_MODULE_CANDIDATES)
    alias_sets = (
        {"source": source, "destination": destination},
        {"src_path": source, "dst_path": destination},
    )
    last_error: Exception | None = None
    for kwargs in alias_sets:
        try:
            return _call_with_compatible_kwargs(fn, **kwargs)
        except TypeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to call URSA snapshot_sqlite_db")


def load_json_file(path: Any, default: Any) -> Any:
    fn = _resolve_attribute("load_json_file", UTILITY_MODULE_CANDIDATES)
    return _call_with_compatible_kwargs(fn, path=path, default=default)


def save_json_file(path: Any, payload: Any) -> Any:
    fn = _resolve_attribute("save_json_file", UTILITY_MODULE_CANDIDATES)
    return _call_with_compatible_kwargs(fn, path=path, payload=payload)


def sanitize_for_logging(value: Any) -> Any:
    fn = _resolve_attribute("sanitize_for_logging", UTILITY_MODULE_CANDIDATES)
    alias_sets = (
        {"value": value},
        {"obj": value},
    )
    last_error: Exception | None = None
    for kwargs in alias_sets:
        try:
            return _call_with_compatible_kwargs(fn, **kwargs)
        except TypeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to call URSA sanitize_for_logging")