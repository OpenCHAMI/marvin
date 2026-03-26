from __future__ import annotations

from openchami_coding_agent import ursa_compat


def test_call_with_compatible_kwargs_filters_unknown_fields() -> None:
    def target(*, prompt: str, timeout: int) -> tuple[str, int]:
        return prompt, timeout

    result = ursa_compat._call_with_compatible_kwargs(  # type: ignore[attr-defined]
        target,
        prompt="hello",
        timeout=30,
        ignored=True,
    )

    assert result == ("hello", 30)


def test_instantiate_agent_renames_common_aliases() -> None:
    class FakeAgent:
        def __init__(self, *, model, checkpoint):
            self.model = model
            self.checkpoint = checkpoint

    agent = ursa_compat.instantiate_agent(
        FakeAgent,
        llm="planner-llm",
        checkpointer="checkpoint-db",
    )

    assert agent.model == "planner-llm"
    assert agent.checkpoint == "checkpoint-db"


def test_get_agent_class_uses_resolver(monkeypatch) -> None:
    sentinel = type("ExecutionAgent", (), {})

    def fake_resolve(attribute: str, module_candidates: tuple[str, ...]):
        assert attribute == "ExecutionAgent"
        assert module_candidates == ursa_compat.AGENT_MODULE_CANDIDATES
        return sentinel

    monkeypatch.setattr(ursa_compat, "_resolve_attribute", fake_resolve)

    assert ursa_compat.get_agent_class("ExecutionAgent") is sentinel


def test_setup_llm_passes_supported_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_setup(model_choice: str, models_cfg: dict | None = None) -> tuple[str, dict | None]:
        captured["model_choice"] = model_choice
        captured["models_cfg"] = models_cfg
        return model_choice, models_cfg

    monkeypatch.setattr(
        ursa_compat,
        "_resolve_attribute",
        lambda attribute, module_candidates: fake_setup,
    )

    result = ursa_compat.setup_llm("openai:gpt-5.4", {"temperature": 0})

    assert result == ("openai:gpt-5.4", {"temperature": 0})
    assert captured == {
        "model_choice": "openai:gpt-5.4",
        "models_cfg": {"temperature": 0},
    }


def test_load_json_file_supports_default_argument(monkeypatch) -> None:
    def fake_loader(*, path, default):
        return {"path": str(path), "default": default}

    monkeypatch.setattr(
        ursa_compat,
        "_resolve_attribute",
        lambda attribute, module_candidates: fake_loader,
    )

    payload = ursa_compat.load_json_file("progress.json", {"ok": True})

    assert payload == {"path": "progress.json", "default": {"ok": True}}


def test_snapshot_sqlite_db_supports_new_parameter_names(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_snapshot(*, src_path, dst_path):
        captured["src_path"] = src_path
        captured["dst_path"] = dst_path
        return "ok"

    monkeypatch.setattr(
        ursa_compat,
        "_resolve_attribute",
        lambda attribute, module_candidates: fake_snapshot,
    )

    result = ursa_compat.snapshot_sqlite_db("source.db", "dest.db")

    assert result == "ok"
    assert captured == {
        "src_path": "source.db",
        "dst_path": "dest.db",
    }


def test_sanitize_for_logging_supports_obj_parameter_name(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_sanitizer(*, obj):
        captured["obj"] = obj
        return {"sanitized": True}

    monkeypatch.setattr(
        ursa_compat,
        "_resolve_attribute",
        lambda attribute, module_candidates: fake_sanitizer,
    )

    result = ursa_compat.sanitize_for_logging({"secret": "hidden"})

    assert result == {"sanitized": True}
    assert captured == {"obj": {"secret": "hidden"}}