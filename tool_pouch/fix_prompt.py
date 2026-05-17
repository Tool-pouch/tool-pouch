"""Output format designed for AI coding tools (Cursor, Claude Code, Cline, etc).

The same run data, restructured as a fix-instruction prompt the developer can
paste into their AI editor. The AI gets enough context to make the fix without
re-reading the codebase, and groups similar failures so it doesn't propose
14 separate one-line patches.
"""
import json
from collections import defaultdict
from tool_pouch.store import Store


BAD_TYPES = {"crashed", "looped", "gave_up", "hallucinated", "silent_wrong", "timeout"}


def render(run_id, agent_path=None):
    """Return a markdown string optimized for pasting into an AI coding tool."""
    store = Store()
    results = store.results_for(run_id)
    failures = [r for r in results if r["failure_type"] in BAD_TYPES]

    if not failures:
        return f"# pouch run {run_id[:8]}\n\nAll {len(results)} scenarios passed. No fixes needed."

    # Group by source so the AI sees clusters, not a flat list
    by_source = defaultdict(list)
    for r in failures:
        source = r["trace"].get("source", "unclear")
        by_source[source].append(r)

    # Suggested fix order: crashes first (block everything else), then control_flow,
    # then prompt issues, then integration. Unclear last.
    source_order = ["control_flow", "prompt", "integration", "model_behavior", "unclear"]
    ordered_sources = sorted(
        by_source.keys(),
        key=lambda s: source_order.index(s) if s in source_order else 99,
    )

    lines = []
    lines.append("# Agent reliability fixes needed")
    lines.append("")
    lines.append(f"`tool-pouch` ran {len(results)} stress-test scenarios against the agent")
    if agent_path:
        lines.append(f"at `{agent_path}` and found **{len(failures)} failures** that need fixing.")
    else:
        lines.append(f"and found **{len(failures)} failures** that need fixing.")
    lines.append("")
    lines.append("Failures are grouped by likely source. Address them in the order below — "
                 "earlier groups often unblock later ones (e.g., a crash fix may reveal a "
                 "downstream hallucination that wasn't reachable before).")
    lines.append("")

    for source in ordered_sources:
        items = by_source[source]
        lines.append(f"## {_source_heading(source)} ({len(items)})")
        lines.append("")
        lines.append(_source_guidance(source))
        lines.append("")

        for i, r in enumerate(items, 1):
            lines.extend(_render_failure(i, r))
            lines.append("")

    lines.append("## After fixing")
    lines.append("")
    lines.append("Re-run `pouch run` to verify the failures are resolved. New failures "
                 "may surface that were previously hidden by earlier crashes.")
    lines.append("")
    return "\n".join(lines)


def _source_heading(source):
    return {
        "control_flow": "Control flow — validate tool responses",
        "prompt": "Prompt — instruct the agent how to handle failures",
        "integration": "Integration — fix tool wrappers and schemas",
        "model_behavior": "Model behavior — the LLM is reasoning poorly",
        "unclear": "Unclear source — investigate manually",
    }.get(source, source)


def _source_guidance(source):
    return {
        "control_flow": (
            "These failures happened because the agent's code didn't validate "
            "tool responses before using them. Add null/empty checks, schema "
            "validation, or use `.get()` with safe defaults."
        ),
        "prompt": (
            "These failures happened because the agent's prompt didn't instruct "
            "it how to handle the failure case. Update the system prompt with "
            "explicit guidance for empty results, errors, and missing data."
        ),
        "integration": (
            "These failures happened in the tool wrapper or schema layer. "
            "Check how exceptions are propagated and how response shapes are "
            "validated at the integration boundary."
        ),
        "model_behavior": (
            "These failures happened because the model reasoned poorly even "
            "though instructions were present. Consider stronger prompt "
            "constraints, structured output enforcement, or a different model."
        ),
        "unclear": (
            "Source could not be confidently attributed. Inspect the trace "
            "and decide which layer to address."
        ),
    }.get(source, "")


def _render_failure(idx, r):
    trace = r["trace"]
    out = []
    out.append(f"### {idx}. `[{r['failure_type'].upper()}]` "
               f"tool={r['target_tool']} scenario={r['scenario']}")
    out.append("")

    hypothesis = trace.get("hypothesis") or trace.get("error", "")
    if hypothesis:
        out.append(f"**What happened:** {hypothesis}")
        out.append("")

    if trace.get("error") and trace.get("traceback"):
        out.append("**Error:**")
        out.append("```")
        out.append(trace["error"])
        out.append("```")
        out.append("")

    user_input = trace.get("user_input")
    if user_input:
        out.append(f"**User request:** `{user_input}`")
        out.append("")

    tool_calls = trace.get("tool_calls", [])
    if tool_calls:
        out.append("**Tool calls during this scenario:**")
        out.append("```")
        for c in tool_calls[:5]:
            args = json.dumps(c.get("args", {}))
            result = json.dumps(c.get("result"))[:80]
            out.append(f"  {c.get('tool')}({args}) → {result}")
        if len(tool_calls) > 5:
            out.append(f"  ... and {len(tool_calls) - 5} more")
        out.append("```")
        out.append("")

    output = trace.get("output")
    if output:
        out.append(f"**Agent's final output:** {output[:200]}")
        out.append("")

    return out
