"""Static scenarios produce the expected exception or payload."""
import asyncio

import pytest

from tool_pouch.proxy import call_with_scenario
from tool_pouch.scenarios import static


def real_tool(name, args):
    return {"real": True, "name": name, "args": args}


@pytest.mark.asyncio
async def test_passthrough_when_no_scenario():
    result = await call_with_scenario(real_tool, "search", {"q": "x"})
    assert result == {"real": True, "name": "search", "args": {"q": "x"}}


@pytest.mark.asyncio
async def test_passthrough_when_target_tool_differs():
    result = await call_with_scenario(
        real_tool, "search", {"q": "x"},
        scenario="server_error", target_tool="fetch",
    )
    assert result["real"] is True


@pytest.mark.asyncio
async def test_server_error_raises():
    with pytest.raises(Exception, match="500"):
        await call_with_scenario(
            real_tool, "search", {}, scenario="server_error",
            target_tool="search",
        )


@pytest.mark.asyncio
async def test_null_response_returns_none():
    result = await call_with_scenario(
        real_tool, "search", {}, scenario="null_response",
        target_tool="search",
    )
    assert result is None


@pytest.mark.asyncio
async def test_latency_spike_actually_waits():
    start = asyncio.get_event_loop().time()
    await asyncio.wait_for(
        call_with_scenario(
            real_tool, "search", {}, scenario="latency_spike",
            target_tool="search",
        ),
        timeout=10,
    )
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed >= 4.5


def test_every_scenario_is_callable():
    """Guards against half-added scenarios."""
    for name in static.list_scenarios():
        fn = static.get_scenario(name)
        assert callable(fn)
