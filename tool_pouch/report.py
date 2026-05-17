"""Summary report and detailed trace view."""
import json
from collections import Counter
from tool_pouch.store import Store
from tool_pouch import colors as c


BAD_TYPES = {"crashed", "looped", "gave_up", "hallucinated", "silent_wrong", "timeout"}

# When picking a single failure to feature in the summary, prioritize the
# kinds of bugs that are hardest to find via normal testing.
HEADLINE_PRIORITY = ["silent_wrong", "hallucinated", "looped", "gave_up",
                     "crashed", "timeout"]


def has_failures(run_id):
    """Return True if any result in this run was a real failure."""
    store = Store()
    results = store.results_for(run_id)
    return any(r["failure_type"] in BAD_TYPES for r in results)


def list_runs(limit=20, failed_only=False):
    """Print a list of past runs, most recent first."""
    import time as time_mod
    store = Store()
    runs = store.list_runs(limit=limit, failed_only=failed_only)

    if not runs:
        if failed_only:
            print("No failed runs found.")
        else:
            print("No runs yet. Run `tool-pouch run` to get started.")
        return

    print()
    print(c.dim("=" * 76))
    header = f"  {'RUN':<10}  {'WHEN':<14}  {'AGENT':<22}  {'RESULT':<22}"
    print(c.bold(header))
    print(c.dim("=" * 76))

    now = time_mod.time()
    for r in runs:
        rid = r["id"][:8]
        when = _relative_time(now - r["started_at"])
        agent = (r["agent_name"] or "?")[:22]

        if r["failures"] == 0 and r["total"] > 0:
            result = c.green(f"✓ {r['total']} passed")
        elif r["total"] == 0:
            result = c.dim("(no results)")
        else:
            pct = 100 * r["failures"] // r["total"]
            result = c.red(f"❌ {r['failures']}/{r['total']} failed ({pct}%)")

        print(f"  {rid:<10}  {when:<14}  {agent:<22}  {result}")

    print()
    print(c.dim(f"Drill into a run:  tool-pouch show <run_id>"))
    print(c.dim(f"Get an AI fix:     tool-pouch fix-prompt <run_id>"))


def _last_assistant_text(trace: dict) -> str | None:
    """Extract the latest assistant message text from a captured trace."""
    for msg in reversed(trace.get("messages") or []):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, str) and content:
                return content
    return None


def _relative_time(seconds_ago):
    """Format a duration as 'just now', '5m ago', '2h ago', etc."""
    if seconds_ago < 60:
        return "just now"
    if seconds_ago < 3600:
        return f"{int(seconds_ago // 60)}m ago"
    if seconds_ago < 86400:
        return f"{int(seconds_ago // 3600)}h ago"
    if seconds_ago < 86400 * 7:
        return f"{int(seconds_ago // 86400)}d ago"
    return f"{int(seconds_ago // 86400)}d ago"


def summary(run_id):
    """Prints the high-level summary - what most users see first."""
    store = Store()
    results = store.results_for(run_id)
    total = len(results)

    by_type = Counter(r["failure_type"] for r in results)
    failures = [r for r in results if r["failure_type"] in BAD_TYPES]

    sources = Counter(
        r["trace"].get("source", "unclear")
        for r in failures
        if r["trace"].get("source")
    )

    print()
    print(c.dim("=" * 60))
    print(c.bold(f"Agent Test Report (run {run_id[:8]})"))
    print(c.dim("=" * 60))
    print(f"Total scenarios: {total}")

    fail_pct = 100 * len(failures) // total if total else 0
    fail_str = f"{len(failures)} ({fail_pct}%)"
    fail_str = c.red(fail_str) if failures else c.green(fail_str)
    print(f"Failures: {fail_str}\n")

    print("Breakdown:")
    for failure_type, count in by_type.most_common():
        marker = "❌" if failure_type in BAD_TYPES else "✓"
        color = c.color_for_outcome(failure_type)
        print(f"  {color(marker)} {color(failure_type)}: {count}")

    if sources:
        print("\nLikely source:")
        for source, count in sources.most_common():
            print(f"  • {c.green(source)}: {count}")

    headline = _pick_headline(failures)
    if headline:
        print()
        print(c.dim("─" * 60))
        print(c.bold("Most interesting finding:"))
        print(c.dim("─" * 60))
        _print_headline(headline)

    if failures:
        print()
        print(c.dim("─" * 60))
        print("Failures (top 20):")
        print(c.dim("─" * 60))
        for r in failures[:20]:
            failure_type = r["failure_type"]
            color = c.color_for_outcome(failure_type)
            header = (
                f"\n{color('[' + failure_type.upper() + ']')} "
                f"tool={r['target_tool']} scenario={r['scenario']}"
            )
            print(header)
            source = r["trace"].get("source")
            if source:
                print(f"  source: {c.green(source)}")
            hypothesis = r["trace"].get("hypothesis") or r["trace"].get("error", "")
            if hypothesis:
                print(c.dim(f"  → {hypothesis}"))

    print()
    print(c.dim(f"For full trace of any failure: "
                f"tool-pouch show {run_id[:8]} --filter <type>"))


def _pick_headline(failures):
    """Return the single most-surprising failure, or None."""
    if not failures:
        return None
    by_type = {ft: [] for ft in HEADLINE_PRIORITY}
    for r in failures:
        ft = r["failure_type"]
        if ft in by_type:
            by_type[ft].append(r)
    for ft in HEADLINE_PRIORITY:
        if by_type[ft]:
            return by_type[ft][0]
    return failures[0]


def _print_headline(r):
    """One block that tells a story: scenario -> behavior -> output -> hint."""
    failure_type = r["failure_type"]
    trace = r["trace"]
    color = c.color_for_outcome(failure_type)

    print()
    print(f"  {color('[' + failure_type.upper() + ']')} "
          f"tool=`{r['target_tool']}` scenario=`{r['scenario']}`")

    output = trace.get("output")
    if output:
        truncated = output[:240] + ("…" if len(output) > 240 else "")
        print(f"\n  Your agent said:")
        print(c.dim(f'    "{truncated}"'))

    error = trace.get("error")
    if error and not output:
        print(f"\n  Error: {c.red(error)}")

    source = trace.get("source")
    hypothesis = trace.get("hypothesis")
    if source:
        print(f"\n  Likely source: {c.green(source)}")
    if hypothesis:
        print(c.dim(f"  → {hypothesis}"))


def show(run_id, filter_type=None):
    """Prints full trace details. This is the 'stack trace' view."""
    store = Store()
    results = store.results_for(run_id)

    if filter_type:
        results = [r for r in results if r["failure_type"] == filter_type]

    if not results:
        print(f"No results found{' matching ' + filter_type if filter_type else ''}.")
        return

    for i, r in enumerate(results, 1):
        failure_type = r["failure_type"]
        color = c.color_for_outcome(failure_type)
        print()
        print(c.dim("=" * 70))
        print(f"#{i}  {color('[' + failure_type.upper() + ']')}  "
              f"tool={r['target_tool']}  scenario={r['scenario']}")
        print(c.dim("=" * 70))

        trace = r["trace"]
        if trace.get("error"):
            print(f"\n{c.bold('Error:')} {c.red(trace['error'])}")
        if trace.get("source"):
            print(f"\n{c.bold('Likely source:')} {c.green(trace['source'])}")
        if trace.get("hypothesis"):
            print(f"\n{c.bold('Hypothesis:')} {trace['hypothesis']}")

        # Agent output: stress-test runs use trace["output"]; production
        # captures from wrap_*() store assistant content in the last
        # role=assistant message.
        agent_output = trace.get("output") or _last_assistant_text(trace)
        print(f"\n{c.bold('Agent output:')}")
        print(f"  {agent_output or c.dim('(none)')}")

        if trace.get("user_input"):
            print(f"\n{c.bold('User input:')}")
            print(f"  {trace['user_input']}")

        tool_calls = trace.get("tool_calls", [])
        if tool_calls:
            print(f"\n{c.bold(f'Tool calls ({len(tool_calls)}):')}")
            for j, call in enumerate(tool_calls, 1):
                # Stress-test shape uses tool/args/result; wrap shape uses
                # name/arguments. Render whichever is present.
                name = call.get("tool") or call.get("name")
                args = call.get("args") or call.get("arguments") or {}
                args_str = args if isinstance(args, str) else json.dumps(args)
                print(f"  {j}. {name}({args_str})")
                if "result" in call and call["result"] is not None:
                    result_str = json.dumps(call["result"])[:120]
                    print(f"     {c.dim('→')} {c.dim(result_str)}")
