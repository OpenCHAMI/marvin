from __future__ import annotations

from openchami_coding_agent.ursa_adapter import UrsaAdapter


def test_call_with_compatible_kwargs_filters_unknown_fields() -> None:
    adapter = UrsaAdapter()

    def target(*, prompt: str, timeout: int) -> tuple[str, int]:
        return prompt, timeout

    result = adapter.call_with_compatible_kwargs(
        target,
        prompt="hello",
        timeout=30,
        ignored=True,
    )

    assert result == ("hello", 30)


def test_instantiate_agent_renames_common_aliases() -> None:
    adapter = UrsaAdapter()

    class FakeAgent:
        def __init__(self, *, model, checkpoint):
            self.model = model
            self.checkpoint = checkpoint

    agent = adapter.instantiate_agent(
        FakeAgent,
        llm="planner-llm",
        checkpointer="checkpoint-db",
    )

    assert agent.model == "planner-llm"
    assert agent.checkpoint == "checkpoint-db"
