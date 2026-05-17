"""Path 2 of 3: Production capture.

One line wraps your OpenAI or Anthropic client. Every subsequent
`chat.completions.create` (or `messages.create`) is captured to your
destinations of choice with sub-millisecond p99 overhead and built-in
PII redaction.

Set OPENAI_API_KEY (or use a local stub) and run with:
    python examples/02_production_capture.py

Then query what was captured:
    tool-pouch traces --since 1h
    tool-pouch trace <trace_id>
"""

from openai import OpenAI

import tool_pouch


def main() -> None:
    client = tool_pouch.wrap_openai(
        OpenAI(),
        agent_name="support_bot",
        # Tie each capture to your application's request id so you can
        # cross-reference traces with logs and customer support tickets.
        request_id=lambda **kw: kw.get("user", "anon"),
        # Default redactor scrubs emails, phones, SSNs, credit cards,
        # IPs, and common API keys at capture time. Extend with project-
        # specific patterns.
        redact=tool_pouch.redact.builtin(extra_patterns=[r"acct_\d{6}"]),
        # LocalStore is the default. JSONLogger ships captures as NDJSON
        # to stderr so your existing log agent (Datadog, Honeycomb, Loki,
        # CloudWatch) picks them up with no extra integration.
        destinations=[tool_pouch.LocalStore(), tool_pouch.JSONLogger()],
    )

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Summarize the last 24h of outages."}],
        user="cust_42",
    )
    print(response.choices[0].message.content)


if __name__ == "__main__":
    main()
