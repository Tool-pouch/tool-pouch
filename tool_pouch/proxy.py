"""Wraps tool execution. Stateless - scenario is per-call so we can run in parallel."""
import asyncio
from tool_pouch.scenarios import static


async def call_with_scenario(real_tool_fn, tool_name, args, scenario=None, target_tool=None):
    """Call the real tool, or inject a failure if scenario applies to this tool."""
    should_inject = scenario and (target_tool is None or target_tool == tool_name)

    if not should_inject:
        result = real_tool_fn(tool_name, args)
        if asyncio.iscoroutine(result):
            return await result
        return result

    # Inject the failure
    fn = static.get_scenario(scenario)
    result = fn()
    if asyncio.iscoroutine(result):
        return await result
    return result
