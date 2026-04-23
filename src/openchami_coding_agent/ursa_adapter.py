"""Stable adapter boundary between Marvin and URSA."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable
from functools import cache
from typing import Any, Protocol


class PlanningAgentProtocol(Protocol):
    thread_id: str | None


class ExecutionAgentProtocol(Protocol):
    thread_id: str | None


class VerifierAgentProtocol(Protocol):
    thread_id: str | None


class ModelFactoryProtocol(Protocol):
    def setup_llm(
        self,
        model_choice: str,
        models_cfg: dict[str, Any] | None = None,
        agent_name: str | None = None,
    ) -> Any: ...


class CheckpointStoreProtocol(Protocol):
    def snapshot_sqlite_db(self, source: Any, destination: Any) -> Any: ...


class UrsaAdapter:
    """Single compatibility layer for dynamic URSA entrypoints."""

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
    def resolve_attribute(self, attribute: str, module_candidates: tuple[str, ...]) -> Any:
        errors: list[str] = []
        for module_name in module_candidates:
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:
                errors.append(f"{module_name}: {type(exc).__name__}: {exc}")
                continue
            if hasattr(module, attribute):
                return getattr(module, attribute)

        searched = ", ".join(module_candidates)
        details = "; ".join(errors)
        raise ImportError(
            f"Unable to resolve URSA attribute '{attribute}' in: {searched}. {details}"
        )

    def _rename_kwargs(self, kwargs: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
        renamed = dict(kwargs)
        for old_name, new_name in aliases.items():
            if old_name not in renamed or new_name in renamed:
                continue
            renamed[new_name] = renamed.pop(old_name)
        return renamed

    def call_with_compatible_kwargs(self, target: Callable[..., Any], **kwargs: Any) -> Any:
        signature = inspect.signature(target)
        parameters = [
            parameter for name, parameter in signature.parameters.items() if name != "self"
        ]
        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
            return target(**kwargs)

        accepted_names = {
            parameter.name
            for parameter in parameters
            if parameter.kind
            in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        }
        filtered = {name: value for name, value in kwargs.items() if name in accepted_names}

        missing_required = [
            parameter.name
            for parameter in parameters
            if parameter.kind
            in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
            and parameter.default is inspect.Parameter.empty
            and parameter.name not in filtered
        ]
        if missing_required:
            missing = ", ".join(missing_required)
            raise TypeError(f"Missing required arguments: {missing}")
        return target(**filtered)

    def instantiate_agent(self, agent_class: type[Any], **kwargs: Any) -> Any:
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
                return self.call_with_compatible_kwargs(
                    agent_class,
                    **self._rename_kwargs(kwargs, aliases),
                )
            except TypeError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Unable to instantiate URSA agent {agent_class.__name__}")

    def get_agent_class(self, *names: str) -> type[Any]:
        for name in names:
            try:
                return self.resolve_attribute(name, self.AGENT_MODULE_CANDIDATES)
            except ImportError:
                continue
        joined = ", ".join(names)
        raise ImportError(f"Unable to resolve any URSA agent class from: {joined}")

    def setup_llm(
        self,
        model_choice: str,
        models_cfg: dict[str, Any] | None = None,
        agent_name: str | None = None,
    ) -> Any:
        fn = self.resolve_attribute("setup_llm", self.UTILITY_MODULE_CANDIDATES)
        return self.call_with_compatible_kwargs(
            fn,
            model_choice=model_choice,
            models_cfg=models_cfg,
            agent_name=agent_name,
        )

    def _call_utility(self, name: str, **kwargs: Any) -> Any:
        fn = self.resolve_attribute(name, self.UTILITY_MODULE_CANDIDATES)
        return self.call_with_compatible_kwargs(fn, **kwargs)

    def generate_workspace_name(self, project: str = "run") -> str:
        return self._call_utility("generate_workspace_name", project=project)

    def load_yaml_config(self, path: str) -> Any:
        return self._call_utility("load_yaml_config", path=path)

    def setup_workspace(
        self,
        user_specified_workspace: str | None,
        project: str = "run",
        model_name: str = "openai:gpt-5-mini",
    ) -> Any:
        return self._call_utility(
            "setup_workspace",
            user_specified_workspace=user_specified_workspace,
            project=project,
            model_name=model_name,
        )

    def timed_input_with_countdown(self, prompt: str, timeout: int) -> str | None:
        return self._call_utility("timed_input_with_countdown", prompt=prompt, timeout=timeout)

    def hash_plan(self, plan_steps: list[Any] | tuple[Any, ...]) -> str:
        return self._call_utility("hash_plan", plan_steps=plan_steps)

    def snapshot_sqlite_db(self, source: Any, destination: Any) -> Any:
        fn = self.resolve_attribute("snapshot_sqlite_db", self.UTILITY_MODULE_CANDIDATES)
        alias_sets = (
            {"source": source, "destination": destination},
            {"src_path": source, "dst_path": destination},
        )
        last_error: Exception | None = None
        for kwargs in alias_sets:
            try:
                return self.call_with_compatible_kwargs(fn, **kwargs)
            except TypeError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("Unable to call URSA snapshot_sqlite_db")

    def load_json_file(self, path: Any, default: Any) -> Any:
        return self._call_utility("load_json_file", path=path, default=default)

    def save_json_file(self, path: Any, payload: Any) -> Any:
        return self._call_utility("save_json_file", path=path, payload=payload)

    def sanitize_for_logging(self, value: Any) -> Any:
        fn = self.resolve_attribute("sanitize_for_logging", self.UTILITY_MODULE_CANDIDATES)
        alias_sets = (
            {"value": value},
            {"obj": value},
        )
        last_error: Exception | None = None
        for kwargs in alias_sets:
            try:
                return self.call_with_compatible_kwargs(fn, **kwargs)
            except TypeError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("Unable to call URSA sanitize_for_logging")


ADAPTER = UrsaAdapter()
