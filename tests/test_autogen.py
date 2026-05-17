"""Auto-generated test inputs."""
import pytest

from tool_pouch import autogen
from tool_pouch.autogen import autogen_inputs


def search(q: str) -> dict:
    """Search the web for q."""
    return {}


def fetch(url: str) -> dict:
    """Fetch URL content."""
    return {}


def test_uses_llm_when_available(monkeypatch):
    captured = {}

    def fake_llm(prompt: str) -> str:
        captured["prompt"] = prompt
        return '["best pizza in NYC", "weather in SF", "open issues in repo"]'

    monkeypatch.setattr(autogen, "_call_llm", fake_llm)

    inputs = autogen_inputs([search, fetch], n=3)
    assert inputs == ["best pizza in NYC", "weather in SF",
                      "open issues in repo"]
    # The prompt should reference both tools by name + description
    assert "search:" in captured["prompt"]
    assert "fetch:" in captured["prompt"]
    assert "Search the web for q" in captured["prompt"]


def test_falls_back_when_llm_unavailable(monkeypatch):
    def boom(_):
        raise RuntimeError("no API key")

    monkeypatch.setattr(autogen, "_call_llm", boom)

    inputs = autogen_inputs([search, fetch], n=3)
    assert len(inputs) == 3
    assert all(isinstance(i, str) and i for i in inputs)


def test_falls_back_on_unparseable_response(monkeypatch):
    monkeypatch.setattr(autogen, "_call_llm", lambda _: "not json at all")
    inputs = autogen_inputs([search], n=2)
    assert len(inputs) == 2


def test_strips_code_fence(monkeypatch):
    monkeypatch.setattr(
        autogen, "_call_llm",
        lambda _: '```json\n["one", "two"]\n```',
    )
    inputs = autogen_inputs([search], n=2)
    assert inputs == ["one", "two"]
