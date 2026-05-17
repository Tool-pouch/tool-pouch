"""Framework adapters. The user's tool functions stay sync; the adapter
drives the model loop and dispatches into tool-pouch's failure-injection proxy.
"""
from tool_pouch.adapters.openai_adapter import test_openai
from tool_pouch.adapters.anthropic_adapter import test_anthropic

__all__ = ["test_openai", "test_anthropic"]
