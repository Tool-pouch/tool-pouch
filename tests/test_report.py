"""Headline-failure picker drives the 'most interesting finding' line."""
from tool_pouch.report import _pick_headline


def _result(failure_type: str, **trace):
    return {
        "scenario": "x", "target_tool": "y",
        "outcome": "completed", "failure_type": failure_type,
        "trace": trace or {"output": "..."},
    }


def test_picks_silent_wrong_over_crashed():
    failures = [_result("crashed"), _result("silent_wrong")]
    assert _pick_headline(failures)["failure_type"] == "silent_wrong"


def test_picks_hallucinated_over_timeout():
    failures = [_result("timeout"), _result("hallucinated")]
    assert _pick_headline(failures)["failure_type"] == "hallucinated"


def test_returns_none_for_empty():
    assert _pick_headline([]) is None


def test_falls_back_to_first_for_unknown_type():
    failures = [_result("weird_unknown_type")]
    assert _pick_headline(failures) is failures[0]
