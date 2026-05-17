"""Path 2 of 3: Production capture.

One line wraps your Anthropic client. Every subsequent `messages.create`
call is captured to your destinations of choice with sub-millisecond p99
overhead and built-in PII redaction.

Set ANTHROPIC_API_KEY and run with:
    python examples/02_production_capture.py

Then query what was captured:
    pouch traces --since 1h
    pouch trace <trace_id>

On OpenAI? Swap two lines:
    from openai import OpenAI
    client = pouch.wrap_openai(OpenAI(), ...)
Everything else (request_id, redact, destinations) is identical.
"""

from anthropic import Anthropic

import tool_pouch as pouch


def main() -> None:
    client = pouch.wrap_anthropic(
        Anthropic(),
        agent_name="support_bot",
        request_id=lambda **kw: kw.get("metadata", {}).get("user_id", "anon"),
        redact=pouch.redact.builtin(extra_patterns=[r"acct_\d{6}"]),
        destinations=[pouch.LocalStore(), pouch.JSONLogger()],
    )

    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        messages=[{"role": "user", "content": "Summarize the last 24h of outages."}],
        metadata={"user_id": "cust_42"},
    )
    print(response.content[0].text)


if __name__ == "__main__":
    main()
