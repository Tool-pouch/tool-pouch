"""Adapters set the judge provider to match the agent when not configured."""
import os

from tool_pouch.adapters._judge_default import default_judge_to


def test_default_judge_to_sets_when_unset(monkeypatch):
    monkeypatch.delenv("AGENT_SIM_JUDGE_PROVIDER", raising=False)
    default_judge_to("openai")
    assert os.environ["AGENT_SIM_JUDGE_PROVIDER"] == "openai"


def test_default_judge_to_respects_existing(monkeypatch):
    monkeypatch.setenv("AGENT_SIM_JUDGE_PROVIDER", "anthropic")
    default_judge_to("openai")
    assert os.environ["AGENT_SIM_JUDGE_PROVIDER"] == "anthropic"
