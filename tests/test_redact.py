"""Built-in redactor + custom extra_patterns + walking nested structures."""
import re

from tool_pouch import redact


def test_email_redacted():
    r = redact.builtin()
    out = r("contact me at jane.doe@example.com please")
    assert "jane.doe@example.com" not in out
    assert "[REDACTED]" in out


def test_phone_redacted():
    r = redact.builtin()
    assert "555-123-4567" not in r("call 555-123-4567 today")
    assert "555-123-4567" not in r("call (555) 123-4567 today")
    assert "555-123-4567" not in r("call +1 555 123 4567 today")


def test_ssn_redacted():
    r = redact.builtin()
    assert "123-45-6789" not in r("SSN: 123-45-6789")


def test_credit_card_redacted():
    r = redact.builtin()
    assert "4111111111111111" not in r("card: 4111-1111-1111-1111")


def test_ipv4_redacted():
    r = redact.builtin()
    assert "192.168.1.1" not in r("from 192.168.1.1")


def test_ipv6_redacted():
    r = redact.builtin()
    assert "2001:db8::1" not in r("ipv6 2001:0db8:0000:0000:0000:0000:0000:0001")


def test_openai_key_redacted():
    r = redact.builtin()
    raw = "key sk-abcdef0123456789ABCDEF0123456789"
    assert "sk-abcdef" not in r(raw)


def test_anthropic_key_redacted():
    r = redact.builtin()
    raw = "key sk-ant-abcdefghij1234567890ABCDEFGHIJ1234567890"
    assert "sk-ant-abc" not in r(raw)


def test_generic_bearer_redacted():
    r = redact.builtin()
    out = r("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpX")
    assert "Bearer eyJ" not in out


def test_aws_access_key_redacted():
    r = redact.builtin()
    assert "AKIAIOSFODNN7EXAMPLE" not in r("AWS_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE")


def test_github_token_redacted():
    r = redact.builtin()
    raw = "ghp_" + "x" * 36
    assert raw not in r(f"gh token {raw}")


def test_walks_nested_dict():
    r = redact.builtin()
    out = r({
        "user": {"email": "x@example.com"},
        "messages": ["hi", "from a@b.com"],
        "count": 7,
        "ok": True,
    })
    assert out["user"]["email"] == "[REDACTED]"
    assert out["messages"][1].count("[REDACTED]") == 1
    assert out["count"] == 7
    assert out["ok"] is True


def test_walks_tuple():
    r = redact.builtin()
    out = r(("a@b.com", 42))
    assert out[0] == "[REDACTED]"
    assert out[1] == 42


def test_extra_patterns_string_and_compiled():
    r = redact.builtin(extra_patterns=[
        r"acct_\d{6}",
        re.compile(r"customer_token=[A-Za-z0-9]+"),
    ])
    out = r("acct_123456 customer_token=abc123XYZ")
    assert "acct_123456" not in out
    assert "customer_token=abc123XYZ" not in out


def test_custom_token():
    r = redact.builtin(token="<***>")
    assert "<***>" in r("a@b.com")


def test_apply_passes_through_when_redactor_is_none():
    payload = {"email": "a@b.com"}
    assert redact.apply(None, payload) is payload


def test_apply_fails_open_when_redactor_raises():
    def bad(value):
        raise RuntimeError("boom")
    payload = {"email": "a@b.com"}
    out = redact.apply(bad, payload)
    assert out is payload  # original returned, no exception


def test_non_string_passthrough():
    r = redact.builtin()
    assert r(42) == 42
    assert r(None) is None
    assert r(True) is True
