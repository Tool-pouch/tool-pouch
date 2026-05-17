"""Path 1 of 3: Pre-deploy.

Decorate your tool functions with `@pouch.tool` (or just `@tool` if you
import it directly). Schemas are derived from type hints and docstrings,
so there is no separate spec file to keep in sync. `pouch scan`
auto-discovers every decorated function in the path you point it at and
stress-tests it against the built-in scenario pack (timeouts, malformed
JSON, rate limits, prompt injection, unicode chaos).

Run from the repo root:
    pouch scan examples/01_pre_deploy.py --quick

In a real project you would point at a folder of tools and let
`pouch init` write a `.tool_pouch.toml` so you can just run
`pouch scan`.
"""

from tool_pouch import tool


@tool
def search(q: str) -> dict:
    """Search the web for q and return a list of result dicts."""
    return {"results": [{"title": "Real result", "url": "https://example.com"}]}


@tool
def fetch(url: str) -> dict:
    """Fetch the URL and return its parsed content."""
    return {"content": "Real page content"}
