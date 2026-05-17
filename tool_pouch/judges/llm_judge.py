"""LLM judge that classifies failures, attributes their source, and suggests fixes.

Internet-dependent. Configurable via env vars so users can run against:
- Anthropic (default)
- OpenAI
- Local Ollama or any OpenAI-compatible endpoint

If the LLM call fails (no network, no API key, model down), the judge returns
a graceful fallback verdict instead of raising. Crashes, timeouts, and loops
are detected upstream without the judge, so partial functionality survives.

Env vars:
  AGENT_SIM_JUDGE_PROVIDER   "anthropic" | "openai" | "ollama"  (default: anthropic)
  AGENT_SIM_JUDGE_MODEL      model name override
  AGENT_SIM_JUDGE_BASE_URL   for ollama / openai-compatible endpoints
"""
import json
import os


JUDGE_PROMPT = """An AI agent was tested with a fault injection.

Original user request: {user_input}
Injected failure: {scenario} on tool "{tool}"
Agent's final output: {output}
Tool calls made: {tool_calls}

Return three things as JSON:

1. "verdict" - what happened. One of:
   - "handled": correctly recognized the failure and reported it
   - "crashed": threw an unhandled exception
   - "looped": got stuck calling the same tool repeatedly
   - "gave_up": stopped without completing or explaining
   - "hallucinated": claimed success or fabricated data despite the failure
   - "silent_wrong": completed but with incorrect output the user wouldn't catch

2. "source" - where the bug most likely lives. One of:
   - "prompt": the agent's instructions don't tell it how to handle this case
   - "control_flow": the agent's code didn't validate the tool response before using it
   - "integration": the tool wrapper or schema is mishandling the failure
   - "model_behavior": the LLM ignored explicit instructions or reasoned poorly
   - "unclear": not enough signal to attribute the source confidently

3. "hypothesis" - a specific, hedged suggestion for what to look at.
   Frame as "this often happens when..." or "consider checking...".
   Be specific (mention the actual tool, scenario, or output where useful).
   Keep it to 1-2 sentences. If "source" is "unclear", say so honestly.

Return only the JSON, no other text:
{{"verdict": "...", "source": "...", "hypothesis": "..."}}"""


# Returned when the judge can't run (no internet, no key, etc.)
FALLBACK_VERDICT = {
    "verdict": "completed",
    "source": "unclear",
    "hypothesis": "Judge unavailable - could not classify this run. "
                  "Check network connection, API key, or AGENT_SIM_JUDGE_PROVIDER.",
}


def judge(user_input, scenario, tool, output, tool_calls):
    """Classify a completed run. Returns dict with verdict/source/hypothesis.

    Never raises - returns FALLBACK_VERDICT on any error.
    """
    provider = os.environ.get("AGENT_SIM_JUDGE_PROVIDER", "anthropic").lower()
    prompt = JUDGE_PROMPT.format(
        user_input=user_input,
        scenario=scenario,
        tool=tool,
        output=output,
        tool_calls=json.dumps(tool_calls),
    )

    try:
        if provider == "anthropic":
            text = _call_anthropic(prompt)
        elif provider in ("openai", "ollama"):
            text = _call_openai_compatible(prompt, provider)
        else:
            return _fallback(f"Unknown provider: {provider}")

        return _parse_response(text)

    except Exception as e:
        return _fallback(f"{type(e).__name__}: {e}")


def _call_anthropic(prompt):
    import anthropic
    model = os.environ.get("AGENT_SIM_JUDGE_MODEL", "claude-opus-4-7")
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _call_openai_compatible(prompt, provider):
    """Works for OpenAI proper and any OpenAI-compatible endpoint (Ollama, vLLM, etc)."""
    from openai import OpenAI

    if provider == "ollama":
        base_url = os.environ.get("AGENT_SIM_JUDGE_BASE_URL", "http://localhost:11434/v1")
        api_key = "ollama"  # Ollama doesn't validate, but the client requires something
        default_model = "llama3.1"
    else:  # openai
        base_url = os.environ.get("AGENT_SIM_JUDGE_BASE_URL")  # None = use default
        api_key = os.environ.get("OPENAI_API_KEY")
        default_model = "gpt-4o-mini"

    model = os.environ.get("AGENT_SIM_JUDGE_MODEL", default_model)
    client = OpenAI(api_key=api_key, base_url=base_url)
    msg = client.chat.completions.create(
        model=model,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.choices[0].message.content.strip()


def _parse_response(text):
    if text.startswith("```"):
        text = text.split("```")[1].lstrip("json\n")
    data = json.loads(text)
    # Make sure all three fields exist even if the model omitted one
    return {
        "verdict": data.get("verdict", "completed"),
        "source": data.get("source", "unclear"),
        "hypothesis": data.get("hypothesis", ""),
    }


def _fallback(reason):
    """Return the fallback verdict with the reason embedded for debugging."""
    return {
        **FALLBACK_VERDICT,
        "hypothesis": f"{FALLBACK_VERDICT['hypothesis']} (reason: {reason})",
    }
