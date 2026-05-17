"""Runs the agent against scenarios in parallel."""
import asyncio
import time
import traceback

from tool_pouch.proxy import call_with_scenario
from tool_pouch.scenarios import static
from tool_pouch.store import Store
from tool_pouch.judges import llm_judge


# Phrases that suggest the agent acknowledged the failure - skip the LLM judge
ACKNOWLEDGMENT_HINTS = [
    "encountered an error", "tool failed", "unable to", "couldn't", "could not",
    "failed to", "error occurred", "something went wrong", "no results",
    "wasn't able", "was unable", "didn't work", "did not work",
]


DEFAULT_AGENT_TIMEOUT_S = 20


class Runner:
    def __init__(self, agent_fn, real_tool_fn, tools, agent_name="agent",
                 agent_timeout_s=DEFAULT_AGENT_TIMEOUT_S, store=None):
        """
        agent_fn(user_input, tool_caller) -> {"output": str, "tool_calls": list}
        real_tool_fn(tool_name, args) -> tool's real response
        tools: list of tool names to inject failures into
        agent_timeout_s: per-scenario timeout before the run is marked timeout
        store: optional Store override (used by tests for tmp dbs)
        """
        self.agent_fn = agent_fn
        self.real_tool_fn = real_tool_fn
        self.tools = tools
        self.agent_name = agent_name
        self.agent_timeout_s = agent_timeout_s
        self.store = store if store is not None else Store()

    async def run(self, user_input, scenarios=None, parallel=8, on_progress=None):
        """Run all (tool, scenario) pairs against the agent.

        on_progress: optional callback fn(done, total, tool, scenario) called
                     after each scenario finishes. Used by the CLI for live output.
        """
        scenarios = scenarios or static.list_scenarios()
        run_id = self.store.new_run(self.agent_name, user_input)

        jobs = [(tool, sc) for tool in self.tools for sc in scenarios]
        total = len(jobs)
        sem = asyncio.Semaphore(parallel)
        done = [0]  # mutable container for closure

        async def run_with_limit(tool, scenario):
            async with sem:
                await self._run_one(run_id, user_input, tool, scenario)
                done[0] += 1
                if on_progress:
                    on_progress(done[0], total, tool, scenario)

        await asyncio.gather(*(run_with_limit(t, s) for t, s in jobs))
        return run_id

    async def _run_one(self, run_id, user_input, tool, scenario):
        start = time.time()
        trace = {
            "user_input": user_input,
            "scenario": scenario,
            "tool_calls": [],
            "output": None,
            "error": None,
        }

        # Each run gets its own tool_caller closure with its own scenario
        async def tool_caller(name, args):
            return await call_with_scenario(
                self.real_tool_fn, name, args,
                scenario=scenario, target_tool=tool,
            )

        try:
            result = await asyncio.wait_for(
                self.agent_fn(user_input, tool_caller),
                timeout=self.agent_timeout_s,
            )
            trace["tool_calls"] = result.get("tool_calls", [])
            trace["output"] = result.get("output")
            outcome = "completed"
        except asyncio.TimeoutError:
            outcome = "timeout"
            trace["error"] = f"agent exceeded {self.agent_timeout_s}s"
        except Exception as e:
            outcome = "crashed"
            trace["error"] = f"{type(e).__name__}: {e}"
            trace["traceback"] = traceback.format_exc()

        duration_ms = int((time.time() - start) * 1000)
        failure_type = await self._classify(outcome, tool, trace)

        self.store.add_result(
            run_id, scenario, tool, outcome, failure_type,
            trace, duration_ms,
        )

    async def _classify(self, outcome, tool, trace):
        """Decide failure_type. Loops > crashes > heuristic > judge.

        The judge call is offloaded to a thread so concurrent scenarios
        actually fan out at the LLM provider, instead of serializing on
        a synchronous HTTP client inside the event loop.
        """
        if outcome in ("crashed", "timeout"):
            return outcome

        # Loop check (fast, deterministic)
        tool_count = sum(1 for c in trace["tool_calls"] if c.get("tool") == tool)
        if tool_count > 5:
            return "looped"

        # Heuristic fast-path: did the agent clearly acknowledge the failure?
        output = (trace.get("output") or "").lower()
        if any(hint in output for hint in ACKNOWLEDGMENT_HINTS):
            return "handled"

        # Otherwise let the LLM judge classify. The judge never raises;
        # if it can't reach the LLM it returns a fallback verdict.
        verdict = await asyncio.to_thread(
            llm_judge.judge,
            user_input=trace["user_input"],
            scenario=trace["scenario"],
            tool=tool,
            output=trace.get("output"),
            tool_calls=trace.get("tool_calls", []),
        )
        trace["source"] = verdict.get("source", "unclear")
        trace["hypothesis"] = verdict.get("hypothesis", "")
        return verdict["verdict"]
