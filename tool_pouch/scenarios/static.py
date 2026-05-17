"""Hardcoded failure scenarios that apply to any tool."""
import asyncio
import json
import random


SCENARIOS = {
    "timeout": lambda: asyncio.sleep(30),
    "server_error": lambda: _raise(Exception("500 Internal Server Error")),
    "rate_limit": lambda: _raise(Exception("429 Too Many Requests")),
    "empty_response": lambda: {},
    "null_response": lambda: None,
    "malformed_json": lambda: "{not valid json",
    "truncated": lambda: '{"data": [{"id": 1, "na',
    "wrong_type": lambda: "expected an object got a string",
    "huge_payload": lambda: {"data": ["x" * 1000] * 1000},
    "latency_spike": lambda: asyncio.sleep(5),
    "partial_data": lambda: {"data": [{"id": 1}]},  # missing fields
    "unicode_chaos": lambda: {"data": "𝕥𝕖𝕩𝕥 ⚠️ \x00\x01"},
}


def _raise(exc):
    raise exc


# Highest-signal scenarios for `tool-pouch scan --quick` — covers the most
# common silent-failure modes in well under a minute.
QUICK_SCENARIOS = ["null_response", "empty_response", "server_error",
                   "malformed_json"]


def get_scenario(name):
    return SCENARIOS[name]


def list_scenarios():
    return list(SCENARIOS.keys())


def quick_scenarios():
    return list(QUICK_SCENARIOS)
