"""Path 2 of 3: Production capture.

One line wraps your Anthropic client. Every subsequent `messages.create`
call is captured to your destinations of choice with sub-millisecond p99
overhead and built-in PII redaction.

Set ANTHROPIC_API_KEY and run with:
    python examples/02_production_capture.py

Then query what was captured:
    tool-pouch traces --since 1h
    tool-pouch trace <trace_id>

On OpenAI? Swap two lines:
    from openai import OpenAI
    client = tool_pouch.wrap_openai(OpenAI(), ...)
Everything else (request_id, redact, destinations) is identical.
"""

from anthropic import Anthropic

import tool_pouch


def main() -> None:
    client = tool_pouch.wrap_anthropic(
        Anthropic(),
        agent_name="support_bot",
        # Tie each capture to your application's request id so you can
        # cross-reference traces with logs and customer support tickets.
        request_id=lambda **kw: kw.get("metadata", {}).get("user_id", "anon"),
        # Default redactor scrubs emails, phones, SSNs, credit cards,
        # IPs, and common API keys at capture time. Extend with project-
        # specific patterns.
        redact=tool_pouch.redact.builtin(extra_patterns=[r"acct_\d{6}"]),
        # LocalStore is the default. JSONLogger ships captures as NDJSON
        # to stderr so your existing log agent (Datadog, Honeycomb, Loki,
        # CloudWatch) picks them up with no extra integration.
        destinations=[tool_pouch.LocalStore(), tool_pouch.JSONLogger()],
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
