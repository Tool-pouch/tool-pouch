"""Make the judge default to the same provider as the agent.

Removes a major DX papercut: if the user is testing an OpenAI agent and
only has $OPENAI_API_KEY set, we shouldn't force them to also get an
Anthropic key just to classify their failures.

Resolution order (unchanged):
  CLI --judge > $AGENT_SIM_JUDGE_PROVIDER > saved config > [this default] > anthropic
"""
from __future__ import annotations

import os


def default_judge_to(provider: str) -> None:
    """Set the judge provider env var to `provider` if nothing else has."""
    if not os.environ.get("AGENT_SIM_JUDGE_PROVIDER"):
        os.environ["AGENT_SIM_JUDGE_PROVIDER"] = provider
