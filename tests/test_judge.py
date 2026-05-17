"""LLM judge contract: parse, fall back, never raise."""
from tool_pouch.judges import llm_judge


def test_unknown_provider_returns_fallback(monkeypatch):
    monkeypatch.setenv("AGENT_SIM_JUDGE_PROVIDER", "nope")
    verdict = llm_judge.judge(
        user_input="x", scenario="timeout", tool="search",
        output=None, tool_calls=[],
    )
    assert verdict["verdict"] == "completed"
    assert verdict["source"] == "unclear"
    assert "nope" in verdict["hypothesis"]


def test_anthropic_failure_returns_fallback(monkeypatch):
    monkeypatch.setenv("AGENT_SIM_JUDGE_PROVIDER", "anthropic")

    def boom(_prompt):
        raise RuntimeError("network down")

    monkeypatch.setattr(llm_judge, "_call_anthropic", boom)

    verdict = llm_judge.judge(
        user_input="x", scenario="timeout", tool="search",
        output=None, tool_calls=[],
    )
    assert verdict["verdict"] == "completed"
    assert "network down" in verdict["hypothesis"]


def test_parse_strips_code_fence():
    raw = '```json\n{"verdict": "handled", "source": "prompt", "hypothesis": "ok"}\n```'
    parsed = llm_judge._parse_response(raw)
    assert parsed == {"verdict": "handled", "source": "prompt", "hypothesis": "ok"}


def test_parse_fills_missing_keys():
    parsed = llm_judge._parse_response('{"verdict": "hallucinated"}')
    assert parsed["verdict"] == "hallucinated"
    assert parsed["source"] == "unclear"
    assert parsed["hypothesis"] == ""
