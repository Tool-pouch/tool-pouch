"""CLI entry point. Invoke as `pouch` (or its alias `tool-pouch`).

    Test path
        pouch init                                       # autodetect setup
        pouch scan [path] [--quick] [--judge ...]        # decorator path
        pouch run [<module>] [--format human|fix-prompt] # custom orchestration

    Production / replay
        pouch traces [--agent ...] [--since 1h] [--failed] [--request-id RID]
        pouch trace <trace_id|--request-id RID>
        pouch replay <trace_id> [--frozen | --frozen-tools | --chaos]
                     [--repeat N] [--strict | --loose-tools | --match-closest]
        pouch sync                                       # cloud upgrade pitch

    Inspection
        pouch show <run_id> [--filter type]
        pouch runs [--failed]
        pouch fix-prompt [<run_id>]
        pouch config                                     # set LLM judge provider
"""
import argparse
import asyncio
import importlib.util
import os
import sys
from pathlib import Path

# Python 3.11+ has tomllib; fall back to tomli for older versions
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

from tool_pouch import stress_test, summary, show
from tool_pouch import fix_prompt as fix_prompt_mod
from tool_pouch import config as config_mod
from tool_pouch import report as report_mod
from tool_pouch import nudges as nudges_mod
from tool_pouch.discover import discover
from tool_pouch.autogen import autogen_inputs
from tool_pouch import init as init_mod
from tool_pouch.replay import (
    VALID_MATCH_STRATEGIES,
    aggregate_verdicts,
    build_replay_inputs,
)
from tool_pouch.scenarios import static as static_scenarios
from tool_pouch.store import KIND_PRODUCTION, KIND_REPLAY, Store


def _get_version():
    """Read version from installed package metadata. Falls back for dev installs."""
    try:
        from importlib.metadata import version
        return version("tool-pouch")
    except Exception:
        return "0.1.0-dev"


VERSION = _get_version()


CONFIG_FILES = [".tool_pouch.toml", "tool_pouch.toml"]


def load_module(path):
    spec = importlib.util.spec_from_file_location("user_agent", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_project_config():
    """Look for .tool_pouch.toml in cwd. Returns dict or empty dict."""
    if not tomllib:
        return {}
    for name in CONFIG_FILES:
        path = Path.cwd() / name
        if path.exists():
            try:
                data = tomllib.loads(path.read_text())
                return data.get("tool-pouch", {})
            except Exception as e:
                print(f"Warning: failed to parse {name}: {e}", file=sys.stderr)
                return {}
    return {}


def parse_scenarios_arg(s):
    """Parse comma-separated scenario list. Empty string or None means 'all'."""
    if not s:
        return None
    return [x.strip() for x in s.split(",") if x.strip()]


def make_progress_printer(total):
    """Returns a callback that prints live progress to stderr."""
    def on_progress(done, total_, tool, scenario):
        # Carriage return so it overwrites in place
        msg = f"  [{done}/{total_}] {tool} × {scenario}"
        # Pad to clear longer previous lines
        sys.stderr.write(f"\r{msg:<70}")
        sys.stderr.flush()
        if done == total_:
            sys.stderr.write("\n")
    return on_progress


def cmd_run(args):
    if args.judge:
        os.environ["AGENT_SIM_JUDGE_PROVIDER"] = args.judge

    config_mod.first_run_setup_if_needed()
    config_mod.resolve_provider()

    # Resolve agent file: CLI arg > project config > error
    project = load_project_config()
    agent_file = args.agent_file or project.get("agent")

    if not agent_file:
        print("Error: no agent file specified.")
        print()
        print("`pouch run` is for custom orchestration (LangGraph, MCP, your own loop).")
        print("If your tools are plain functions, the simpler path is:")
        print()
        print("  pouch init && pouch scan --quick")
        print()
        print("To stay on `pouch run`, either:")
        print("  1. pouch run path/to/agent.py")
        print("  2. Create .tool_pouch.toml with: agent = \"./agent.py\"")
        sys.exit(2)

    if not Path(agent_file).exists():
        print(f"Error: {agent_file} not found.")
        sys.exit(2)

    try:
        mod = load_module(agent_file)
    except Exception as e:
        print(f"Error: failed to load {agent_file}")
        print(f"  {type(e).__name__}: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        else:
            print("\nRun with --verbose to see the full traceback.")
        sys.exit(2)
    required = ["agent_fn", "real_tool_fn", "tools", "test_inputs"]
    missing = [name for name in required if not hasattr(mod, name)]
    if missing:
        print(f"Error: {agent_file} is missing: {', '.join(missing)}")
        print("\nYour file needs to define:")
        print("  agent_fn      async fn(user_input, tool_caller) -> dict")
        print("  real_tool_fn  fn(tool_name, args) -> result")
        print("  tools         list of tool names to test")
        print("  test_inputs   list of strings to run the agent on")
        sys.exit(2)

    # Resolve scenarios filter: CLI > project config > all
    scenarios = parse_scenarios_arg(args.scenarios) or project.get("scenarios")

    # Resolve tools filter: CLI > project config > all from agent file
    tool_filter = parse_scenarios_arg(args.tools) or project.get("tools")
    if tool_filter:
        unknown = [t for t in tool_filter if t not in mod.tools]
        if unknown:
            print(f"Error: tool(s) not declared in {agent_file}: "
                  f"{', '.join(unknown)}")
            print(f"Available tools: {', '.join(mod.tools)}")
            sys.exit(2)
        tools_to_run = tool_filter
    else:
        tools_to_run = mod.tools

    parallel = project.get("parallel", 8)

    # Calculate total job count for the progress bar
    n_scenarios = len(scenarios) if scenarios else 12  # 12 static built-ins
    total_jobs = len(tools_to_run) * n_scenarios

    print(f"Running {total_jobs} scenarios across {len(tools_to_run)} tools "
          f"(parallel: {parallel})...", file=sys.stderr)

    on_progress = make_progress_printer(total_jobs) if sys.stderr.isatty() else None

    try:
        run_ids = asyncio.run(stress_test(
            agent_fn=mod.agent_fn,
            real_tool_fn=mod.real_tool_fn,
            tools=tools_to_run,
            user_inputs=mod.test_inputs,
            agent_name=Path(agent_file).stem,
            scenarios=scenarios,
            parallel=parallel,
            on_progress=on_progress,
        ))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"\nError running tests: {type(e).__name__}: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        else:
            print("Run with --verbose to see the full traceback.", file=sys.stderr)
        sys.exit(2)

    any_failures = False
    for rid in run_ids:
        if args.format == "fix-prompt":
            print(fix_prompt_mod.render(rid, agent_path=agent_file))
        else:
            summary(rid)
        if report_mod.has_failures(rid):
            any_failures = True

    # Exit code 1 if any failures - lets CI detect regressions like pytest
    sys.exit(1 if any_failures else 0)


def cmd_show(args):
    run_id = args.run_id or _latest_run_id_or_exit()
    show(run_id, filter_type=args.filter)


def cmd_runs(args):
    """List past runs."""
    limit = None if args.all else args.limit
    report_mod.list_runs(limit=limit, failed_only=args.failed)


def cmd_fix_prompt(args):
    run_id = args.run_id or _latest_run_id_or_exit()
    print(fix_prompt_mod.render(run_id))


def _latest_run_id_or_exit():
    """Get the most recent run_id, or print a friendly error and exit."""
    from tool_pouch.store import Store
    rid = Store().latest_run_id()
    if not rid:
        print("No runs found. Run `pouch run` first.")
        sys.exit(2)
    return rid


def cmd_config(args):
    if config_mod.CONFIG_PATH.exists():
        print(f"Existing config at {config_mod.CONFIG_PATH} will be replaced.\n")
    cfg = config_mod._interactive_setup()
    if cfg:
        config_mod.save_config(cfg)
        print(f"\nSaved to {config_mod.CONFIG_PATH}")
    else:
        print("\nSkipped - no config saved.")


def cmd_scan(args):
    """Auto-discover @pouch.tool functions and stress-test them.

    Resolution order:
        CLI flag > .tool_pouch.toml > sensible default / autogenerated
    """
    # Capture whether the user has *explicitly* chosen a judge BEFORE we
    # mutate the environment. Used at the end to decide whether to mirror
    # the agent provider, instead of letting autodetect win silently.
    user_chose_judge = bool(
        args.judge
        or os.environ.get("AGENT_SIM_JUDGE_PROVIDER")
        or config_mod.CONFIG_PATH.exists()
    )

    if args.judge:
        os.environ["AGENT_SIM_JUDGE_PROVIDER"] = args.judge

    config_mod.first_run_setup_if_needed()
    if config_mod.CONFIG_PATH.exists():
        user_chose_judge = True

    config_mod.resolve_provider()
    project = load_project_config()

    tools_path = args.path or project.get("tools")
    if not tools_path:
        print("Error: no tools path. Pass `pouch scan <path>` or set "
              "`tools = \"./your_tools/\"` in .tool_pouch.toml.")
        sys.exit(2)

    if not Path(tools_path).exists():
        print(f"Error: {tools_path} not found.")
        sys.exit(2)

    try:
        tools = discover(tools_path)
    except Exception as e:
        print(f"Error: discover failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        sys.exit(2)

    if not tools:
        print(f"No @pouch.tool functions found under {tools_path}.")
        print("\nDecorate at least one function:")
        print("\n  from tool_pouch import tool")
        print("\n  @tool")
        print("  def search(q: str) -> dict:")
        print("      \"\"\"Search the web.\"\"\"")
        print("      ...")
        sys.exit(2)

    print(f"Discovered {len(tools)} tool(s): "
          f"{', '.join(t.__name__ for t in tools)}", file=sys.stderr)

    provider = (args.provider or project.get("provider") or "openai").lower()
    model = args.model or project.get("model") or _default_model(provider)

    # If the user never expressed an explicit judge preference, mirror the
    # agent provider so "one API key is enough" actually holds — even when
    # only one of OPENAI/ANTHROPIC keys is in the env (autodetect would
    # otherwise pick whichever happens to be present).
    if not user_chose_judge:
        os.environ["AGENT_SIM_JUDGE_PROVIDER"] = provider

    test_inputs = parse_scenarios_arg(args.inputs) or project.get("test_inputs")
    if not test_inputs:
        print(f"Auto-generating test inputs from tool descriptions...",
              file=sys.stderr)
        test_inputs = autogen_inputs(tools, n=3)
        for ti in test_inputs:
            print(f"  • {ti}", file=sys.stderr)

    scenarios = parse_scenarios_arg(args.scenarios) or project.get("scenarios")
    if args.quick and not scenarios:
        scenarios = static_scenarios.quick_scenarios()
        test_inputs = test_inputs[:1]  # one input is plenty for the quick path
        print(f"Quick mode: {len(scenarios)} scenario(s), 1 input "
              f"(skip with full `pouch scan`)", file=sys.stderr)

    parallel = project.get("parallel", 8)

    n_scenarios = len(scenarios) if scenarios else len(static_scenarios.list_scenarios())
    total_jobs = len(tools) * n_scenarios

    print(f"Running {total_jobs} scenarios across {len(tools)} tools "
          f"(parallel: {parallel})...", file=sys.stderr)

    on_progress = make_progress_printer(total_jobs) if sys.stderr.isatty() else None

    if provider == "openai":
        try:
            from openai import OpenAI
        except ImportError:
            print("Error: the OpenAI client isn't installed.\n"
                  "  pip install 'tool-pouch[openai]'", file=sys.stderr)
            sys.exit(2)
        from tool_pouch import test_openai
        run_ids = test_openai(
            client=OpenAI(),
            model=model,
            tools=tools,
            test_inputs=test_inputs,
            scenarios=scenarios,
            parallel=parallel,
            on_progress=on_progress,
        )
    elif provider == "anthropic":
        try:
            from anthropic import Anthropic
        except ImportError:
            print("Error: the Anthropic client isn't installed.\n"
                  "  pip install tool-pouch", file=sys.stderr)
            sys.exit(2)
        from tool_pouch import test_anthropic
        run_ids = test_anthropic(
            client=Anthropic(),
            model=model,
            tools=tools,
            test_inputs=test_inputs,
            scenarios=scenarios,
            parallel=parallel,
            on_progress=on_progress,
        )
    else:
        print(f"Error: unknown provider {provider!r}. Use 'openai' or 'anthropic'.")
        sys.exit(2)

    any_failures = False
    for rid in run_ids:
        summary(rid)
        if report_mod.has_failures(rid):
            any_failures = True

    sys.exit(1 if any_failures else 0)


def _default_model(provider: str) -> str:
    return init_mod.DEFAULT_MODELS.get(provider, "gpt-4o-mini")


def cmd_init(args):
    """Autodetect tools/provider/model and write .tool_pouch.toml."""
    root = Path.cwd()
    plan = init_mod.make_plan(root)

    try:
        target = init_mod.write(root, plan, force=args.force)
    except FileExistsError as e:
        print(f"Error: {e}")
        sys.exit(2)

    print(f"✓ Wrote {target.name}")
    print()
    print(f"  provider: {plan.provider}")
    print(f"  model:    {plan.model}")
    if plan.tools_path:
        print(f"  tools:    {plan.tools_path}")
    else:
        print(f"  tools:    (none detected — set this once you have @pouch.tool functions)")
    print()
    if plan.tools_path:
        print("Next: pouch scan --quick")
    else:
        print("Next: decorate a function with @pouch.tool, set `tools` in .tool_pouch.toml,")
        print("      then run `pouch scan --quick`.")


def cmd_traces(args):
    """List captured production traces."""
    store = Store()
    since_seconds = _parse_since(args.since)
    rows = store.list_traces(
        kind=KIND_PRODUCTION,
        agent_name=args.agent,
        since_seconds=since_seconds,
        failed_only=args.failed,
        request_id=args.request_id,
        limit=args.limit,
    )

    if not rows:
        print("No production traces found.")
        print()
        print("Wrap your client to start capturing:")
        print()
        print("  client = pouch.wrap_anthropic(Anthropic())")
        print("  # ...")
        sys.exit(0)

    from tool_pouch import colors as col
    print()
    print(col.dim("=" * 92))
    header = (
        f"  {'TRACE':<10}  {'WHEN':<14}  {'AGENT':<22}  "
        f"{'OUTCOME':<14}  {'REQUEST_ID':<18}"
    )
    print(col.bold(header))
    print(col.dim("=" * 92))
    import time as time_mod
    now = time_mod.time()
    for row in rows:
        tid = row["id"][:8]
        when = report_mod._relative_time(now - (row["started_at"] or now))
        agent = (row["agent_name"] or "?")[:22]
        outcome = (row["outcome"] or "?")[:14]
        rid = (row["request_id"] or "")[:18]
        rendered_outcome = (
            col.green(outcome) if outcome == "completed" else col.red(outcome)
        )
        print(f"  {tid:<10}  {when:<14}  {agent:<22}  "
              f"{rendered_outcome:<14}  {rid:<18}")
    print()
    print(col.dim(f"Drill into a trace:  pouch trace <trace_id>"))
    print(col.dim(f"Replay a trace:      pouch replay <trace_id>"))

    _maybe_nudge_cloud(len(rows))


def cmd_trace(args):
    """Show full detail of a single captured production trace."""
    trace_id = _resolve_trace_id(args)
    if not trace_id:
        print("No trace matched.")
        sys.exit(2)
    show(trace_id)


def cmd_replay(args):
    """Replay a captured trace under one of frozen / frozen-tools / chaos."""
    trace_id = _resolve_trace_id(args)
    if not trace_id:
        print("No trace matched.")
        sys.exit(2)

    store = Store()
    results = store.results_for(trace_id)
    if not results:
        print(f"Trace {trace_id} has no captured payload.")
        sys.exit(2)
    captured_trace = results[0]["trace"]

    mode, match = _resolve_replay_mode(args)
    user_agent_fn, user_tool_fn = _maybe_load_user_callables(mode, args)

    runs_to_repeat = max(1, args.repeat or 1)
    project = load_project_config()
    parallel = project.get("parallel", 8)

    print(
        f"Replaying {trace_id[:8]} in {mode} mode"
        + (f" (match={match})" if mode == "frozen-tools" else "")
        + (f" × {runs_to_repeat}" if runs_to_repeat > 1 else "")
        + "...",
        file=sys.stderr,
    )

    aggregate_input = []
    last_run_id = None
    for i in range(runs_to_repeat):
        replay_inputs = build_replay_inputs(
            captured_trace, mode=mode, match=match,
            user_agent_fn=user_agent_fn, user_tool_fn=user_tool_fn,
        )
        from tool_pouch.runner import Runner
        replay_store = Store()
        run_id = replay_store.new_run(
            agent_name=f"replay:{captured_trace.get('user_input', '')[:40]}",
            user_input=replay_inputs.user_input,
            kind=KIND_REPLAY,
            metadata={
                "source_trace_id": trace_id,
                "mode": mode,
                "match": match if mode == "frozen-tools" else None,
                "iteration": i + 1,
            },
        )
        runner = Runner(
            agent_fn=replay_inputs.agent_fn,
            real_tool_fn=replay_inputs.real_tool_fn,
            tools=replay_inputs.tools,
            agent_name=f"replay:{trace_id[:8]}",
            store=replay_store,
        )
        # Re-use the existing run id by short-circuiting: run() calls
        # store.new_run internally, so for repeat aggregation we just
        # use the run() output and accept the duplicate run record.
        last_run_id = asyncio.run(runner.run(
            replay_inputs.user_input, parallel=parallel,
        ))
        aggregate_input.append(_extract_verdict_map(replay_store, last_run_id))

    if runs_to_repeat > 1:
        _print_aggregate(aggregate_verdicts(aggregate_input))
    else:
        summary(last_run_id)


def cmd_sync(args):
    """Stub: pitches the upcoming Tool Pouch Cloud sync flow."""
    store = Store()
    rows = store.list_traces(kind=KIND_PRODUCTION, limit=None)

    print()
    print("Tool Pouch Cloud is not live yet.")
    print()
    print(f"  Local production traces captured: {len(rows)}")
    print()
    print("When the cloud ships, `pouch sync` will push traces to your")
    print("workspace, where teammates can replay incidents and triage")
    print("them together.")
    print()
    print("Until then, pipe captures to your existing log aggregator:")
    print()
    print("  client = pouch.wrap_anthropic(")
    print("      client,")
    print("      destinations=[pouch.JSONLogger()],")
    print("  )")
    print()
    print("Or stand up your own backend with HTTPSink:")
    print()
    print("  client = pouch.wrap_anthropic(")
    print("      client,")
    print('      destinations=[pouch.HTTPSink(url="https://your.api/traces")],')
    print("  )")
    print()
    print("Sign up for cloud-launch updates at https://toolpouch.dev")


# --- helpers shared by traces / trace / replay ------------------------------


def _parse_since(since: str | None) -> float | None:
    """Translate '5m', '2h', '3d' into seconds. None → None."""
    if not since:
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    suffix = since[-1]
    if suffix in units and since[:-1].isdigit():
        return int(since[:-1]) * units[suffix]
    if since.isdigit():
        return int(since)
    print(f"Error: invalid --since {since!r}. Use e.g. 30m, 2h, 7d.",
          file=sys.stderr)
    sys.exit(2)


def _resolve_trace_id(args) -> str | None:
    """Accept positional trace_id, --request-id lookup, or default to latest."""
    if getattr(args, "trace_id", None):
        return args.trace_id
    request_id = getattr(args, "request_id", None)
    if request_id:
        store = Store()
        matches = store.list_traces(
            kind=KIND_PRODUCTION, request_id=request_id, limit=2,
        )
        if not matches:
            return None
        if len(matches) > 1:
            print(
                f"Multiple traces match request_id={request_id!r}; "
                "pass the trace id explicitly.",
                file=sys.stderr,
            )
            sys.exit(2)
        return matches[0]["id"]
    store = Store()
    rows = store.list_traces(kind=KIND_PRODUCTION, limit=1)
    return rows[0]["id"] if rows else None


def _resolve_replay_mode(args) -> tuple[str, str]:
    if args.frozen:
        return "frozen", "strict"
    if args.frozen_tools or args.loose_tools or args.match_closest:
        if args.loose_tools:
            return "frozen-tools", "loose"
        if args.match_closest:
            return "frozen-tools", "closest"
        return "frozen-tools", "strict"
    return "chaos", "strict"


def _maybe_load_user_callables(mode: str, args):
    if mode == "frozen":
        return None, None
    project = load_project_config()
    agent_file = args.agent_file or project.get("agent")
    if not agent_file:
        print(
            "Error: replay needs your agent file.\n\n"
            f"  {mode} mode re-calls your agent_fn against the captured trace.\n"
            "  Pass `--agent-file my_agent.py` or set `agent` in .tool_pouch.toml.",
            file=sys.stderr,
        )
        sys.exit(2)
    if not Path(agent_file).exists():
        print(f"Error: {agent_file} not found.", file=sys.stderr)
        sys.exit(2)
    mod = load_module(agent_file)
    user_agent_fn = getattr(mod, "agent_fn", None)
    user_tool_fn = getattr(mod, "real_tool_fn", None)
    if user_agent_fn is None or (mode == "chaos" and user_tool_fn is None):
        print(
            f"Error: {agent_file} must export agent_fn"
            + (" and real_tool_fn for chaos mode." if mode == "chaos" else "."),
            file=sys.stderr,
        )
        sys.exit(2)
    return user_agent_fn, user_tool_fn


def _extract_verdict_map(store: Store, run_id: str) -> dict:
    """Build a {(tool, scenario): failure_type} map for one replay run."""
    out = {}
    for r in store.results_for(run_id):
        out[(r["target_tool"], r["scenario"])] = r["failure_type"]
    return out


def _print_aggregate(aggregated):
    """Show verdict percentages per (tool, scenario) cell."""
    from tool_pouch import colors as col
    print()
    print(col.bold("Failure rates across replays:"))
    print()
    for (tool, scenario), verdicts in sorted(aggregated.items()):
        # Sort verdicts so the worst outcome surfaces first.
        ordered = sorted(verdicts.items(), key=lambda kv: -kv[1])
        line_parts = [f"{int(round(pct * 100))}% {v}" for v, pct in ordered]
        print(f"  {tool:<22} × {scenario:<22}  " + ", ".join(line_parts))
    print()


def _maybe_nudge_cloud(trace_count: int) -> None:
    if trace_count >= 5_000:
        nudges_mod.show_once(
            "trace_count_5k",
            "\n[!] You've captured 5k+ local traces. `pouch sync` will push "
            "these to Tool Pouch Cloud once it ships. Subscribe at toolpouch.dev.",
        )


HELP_SCREEN = """\
{title}

  Stress-test AI agents pre-deploy. Capture production traces. Replay
  incidents on demand.

{first_time_label}
  pouch init                       Autodetect tools/provider/model, write .tool_pouch.toml
  pouch scan --quick               Fastest pre-deploy stress test (~15s)

{test_label}
  init                            Autodetect setup and write .tool_pouch.toml
  scan [path]                     Auto-discover @pouch.tool functions and test
  run [agent.py]                  Stress-test an agent (custom orchestration)

{production_label}
  traces                          List captured production traces
  trace <trace_id>                Show full detail of one captured trace
  replay <trace_id>               Replay a trace (chaos | frozen | frozen-tools)
  sync                            Push captured traces to Tool Pouch Cloud (soon)

{inspection_label}
  runs                            List past test/replay runs
  show <run_id>                   Show the full trace of a past run
  fix-prompt <run_id>             Print past run as markdown for AI coding tools
  config                          Set or change the LLM judge provider
  help                            Show this screen
  --version                       Print the installed version

{flags_label}
  --quick                         (scan) Run highest-signal scenarios + 1 input
  --provider anthropic | openai   (scan) Provider for the agent under test
  --model <name>                  (scan) Model name (e.g. claude-opus-4-7, gpt-4o)
  --inputs "a","b"                (scan) Test inputs; autogenerated if omitted
  --scenarios timeout,...         (scan/run) Run only specific scenarios
  --tools search,fetch            (run only) Limit to specific tools
  --format human | fix-prompt     (run only) Output format (human is default)
  --judge anthropic | openai | ollama
                                  Override the LLM judge for one run
  --filter <type>                 (show) Filter trace by failure type
  --agent <name>                  (traces) Filter by agent_name
  --since 30m | 2h | 7d           (traces) Only recent traces
  --failed                        (traces, runs) Only failed traces/runs
  --request-id <rid>              (traces, trace, replay) Look up by request_id
  --frozen                        (replay) Walk-through, no model/tool calls
  --frozen-tools                  (replay) Re-call model; stub tools (strict)
  --loose-tools                   (replay) Match by tool name only
  --match-closest                 (replay) Pick captured args by nearest neighbor
  --repeat N                      (replay) Run N replays, report % per cell

{examples_label}
  pouch init && pouch scan --quick                        {ex_scan_comment}
  pouch scan ./tools/ --provider anthropic --model claude-opus-4-7
  pouch run my_agent.py --scenarios timeout,malformed_json
  pouch traces --since 1h --failed                   {ex_traces_comment}
  pouch trace --request-id req-abc123                {ex_trace_comment}
  pouch replay <trace_id> --repeat 100               {ex_replay_comment}
  pouch replay <trace_id> --frozen                   {ex_frozen_comment}
  pouch fix-prompt | pbcopy                          {ex1_comment}

{tips_label}
  • `pouch scan` is the recommended entrypoint for new projects.
  • `pouch.wrap_anthropic(client)` captures every production request to the
    local store. Drop in `JSONLogger()` or `HTTPSink(url=...)` for prod.
  • `pouch replay` re-runs a captured trace under chaos by default, so
    you can answer "would this incident reproduce?" in one command.
  • Drop a `.tool_pouch.toml` in your project root and `pouch scan` / `pouch run`
    both work with no flags.
  • The judge mirrors the agent provider by default — one API key is enough.
  • Exit code is 0 when all scenarios pass, 1 when any fail (works in CI).
  • Set NO_COLOR=1 to disable colored output.
  • Set TOOL_POUCH_DISABLE_WRAP=1 to short-circuit production capture (CI, tests).
"""


def print_help():
    from tool_pouch import colors as col
    print(HELP_SCREEN.format(
        title=col.bold("Tool Pouch") + col.dim(" — stress-test agents, capture production, replay incidents"),
        first_time_label=col.green("FIRST TIME?"),
        test_label=col.green("TEST"),
        production_label=col.green("PRODUCTION"),
        inspection_label=col.green("INSPECT"),
        flags_label=col.green("FLAGS"),
        examples_label=col.green("EXAMPLES"),
        tips_label=col.green("TIPS"),
        ex_scan_comment=col.dim("# 0 → wow in ~15s"),
        ex_traces_comment=col.dim("# review prod failures from the last hour"),
        ex_trace_comment=col.dim("# look up by request_id from your logs"),
        ex_replay_comment=col.dim("# 'would it reproduce?' — chaos x 100"),
        ex_frozen_comment=col.dim("# walk through what actually happened"),
        ex1_comment=col.dim("# latest run → clipboard, paste into Cursor"),
    ))


def main():
    # Custom help screen for: no args, `help`, `--help`, `-h`
    if len(sys.argv) == 1 or sys.argv[1] in ("help", "--help", "-h"):
        print_help()
        sys.exit(0)

    # Version flag - both long and short forms
    if sys.argv[1] in ("--version", "-V"):
        print(f"tool-pouch {VERSION}")
        sys.exit(0)

    p = argparse.ArgumentParser(prog="tool-pouch", add_help=False)
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="Stress test an agent")
    r.add_argument("agent_file", nargs="?",
                   help="Path to your agent module (or set in .tool_pouch.toml)")
    r.add_argument("--format", choices=["human", "fix-prompt"], default="human",
                   help="Output format (default: human)")
    r.add_argument("--judge", choices=["anthropic", "openai", "ollama"],
                   help="Override the judge provider for this run only")
    r.add_argument("--scenarios",
                   help="Comma-separated list of scenarios to run "
                        "(e.g. 'timeout,malformed_json'). Default: all.")
    r.add_argument("--tools",
                   help="Comma-separated list of tools to test "
                        "(e.g. 'search,fetch'). Default: all declared in agent file.")
    r.add_argument("--verbose", "-v", action="store_true",
                   help="Show full tracebacks on errors.")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("show", help="Show full trace of a run")
    s.add_argument("run_id", type=str, nargs="?",
                   help="Run ID or prefix. Defaults to the most recent run.")
    s.add_argument("--filter", help="Filter by failure type (e.g. hallucinated)")
    s.set_defaults(func=cmd_show)

    runs_p = sub.add_parser("runs", help="List past runs")
    runs_p.add_argument("--limit", type=int, default=20,
                        help="Max number of runs to show (default: 20)")
    runs_p.add_argument("--all", action="store_true",
                        help="Show all runs (overrides --limit)")
    runs_p.add_argument("--failed", action="store_true",
                        help="Only show runs that had failures")
    runs_p.set_defaults(func=cmd_runs)

    f = sub.add_parser("fix-prompt",
                       help="Print a past run as a markdown prompt for AI coding tools")
    f.add_argument("run_id", type=str, nargs="?",
                   help="Run ID or prefix. Defaults to the most recent run.")
    f.set_defaults(func=cmd_fix_prompt)

    cfg_p = sub.add_parser("config", help="Set or change the LLM judge provider")
    cfg_p.set_defaults(func=cmd_config)

    sc = sub.add_parser("scan",
                        help="Auto-discover @pouch.tool functions and stress-test")
    sc.add_argument("path", nargs="?",
                    help="File or directory to scan (or set tools in .tool_pouch.toml)")
    sc.add_argument("--provider", choices=["openai", "anthropic"],
                    help="LLM provider for the agent under test")
    sc.add_argument("--model", help="Model name (e.g. gpt-4o, claude-opus-4-7)")
    sc.add_argument("--inputs",
                    help="Comma-separated test inputs. If omitted, tool-pouch "
                         "generates them from tool docstrings.")
    sc.add_argument("--scenarios",
                    help="Comma-separated list of scenarios to run. Default: all.")
    sc.add_argument("--judge", choices=["anthropic", "openai", "ollama"],
                    help="Override the LLM judge provider for this run only. "
                         "Defaults to mirroring --provider.")
    sc.add_argument("--quick", action="store_true",
                    help="Fast first-run mode: one input × the highest-signal "
                         "scenarios. Designed for the inner-loop fix-and-rerun "
                         "cycle.")
    sc.set_defaults(func=cmd_scan)

    init_p = sub.add_parser("init",
                            help="Autodetect tools/provider/model and write .tool_pouch.toml")
    init_p.add_argument("--force", action="store_true",
                        help="Overwrite an existing .tool_pouch.toml")
    init_p.set_defaults(func=cmd_init)

    traces_p = sub.add_parser("traces",
                              help="List captured production traces")
    traces_p.add_argument("--agent",
                          help="Filter by agent_name (exact match)")
    traces_p.add_argument("--since",
                          help="Only show traces newer than e.g. 30m, 2h, 7d")
    traces_p.add_argument("--failed", action="store_true",
                          help="Only show failed traces (outcome != completed)")
    traces_p.add_argument("--request-id", dest="request_id",
                          help="Filter by exact request_id")
    traces_p.add_argument("--limit", type=int, default=50,
                          help="Max traces to list (default 50)")
    traces_p.set_defaults(func=cmd_traces)

    trace_p = sub.add_parser("trace",
                             help="Show full detail of one captured trace")
    trace_p.add_argument("trace_id", nargs="?",
                         help="Trace id or prefix; defaults to most recent")
    trace_p.add_argument("--request-id", dest="request_id",
                         help="Look up by exact request_id instead")
    trace_p.set_defaults(func=cmd_trace)

    replay_p = sub.add_parser("replay",
                              help="Replay a captured trace under chaos / frozen / frozen-tools")
    replay_p.add_argument("trace_id", nargs="?",
                          help="Trace id or prefix; defaults to most recent")
    replay_p.add_argument("--request-id", dest="request_id",
                          help="Look up trace by request_id instead")
    replay_p.add_argument("--frozen", action="store_true",
                          help="Deterministic walk-through; no model/tool calls")
    replay_p.add_argument("--frozen-tools", dest="frozen_tools",
                          action="store_true",
                          help="Re-call model, but stub tools with captured outputs")
    replay_p.add_argument("--loose-tools", dest="loose_tools",
                          action="store_true",
                          help="Frozen-tools mode that matches by tool name only")
    replay_p.add_argument("--match-closest", dest="match_closest",
                          action="store_true",
                          help="Frozen-tools mode that picks the closest captured args")
    replay_p.add_argument("--repeat", type=int, default=1,
                          help="Run N replays and report verdict percentages")
    replay_p.add_argument("--agent-file", dest="agent_file",
                          help="Path to your agent module (overrides .tool_pouch.toml)")
    replay_p.set_defaults(func=cmd_replay)

    sync_p = sub.add_parser("sync",
                            help="Push captured traces to Tool Pouch Cloud (cloud signup pitch)")
    sync_p.set_defaults(func=cmd_sync)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
