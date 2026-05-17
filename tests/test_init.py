"""`tool-pouch init` autodetection + config writer."""
import pytest

from tool_pouch.init import detect_provider, detect_tools_path, make_plan, write


def test_detect_provider_prefers_openai_when_both(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-...")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-...")
    assert detect_provider() == "openai"


def test_detect_provider_anthropic_only(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-...")
    assert detect_provider() == "anthropic"


def test_detect_provider_falls_back_to_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert detect_provider() == "openai"


def test_detect_tools_path_finds_tools_py(tmp_path):
    (tmp_path / "tools.py").write_text("# stub")
    assert detect_tools_path(tmp_path) == "./tools.py"


def test_detect_tools_path_finds_tools_dir(tmp_path):
    (tmp_path / "tools").mkdir()
    assert detect_tools_path(tmp_path) == "./tools/"


def test_detect_tools_path_searches_common_layouts(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "tools.py").write_text("# stub")
    assert detect_tools_path(tmp_path) == "./src/tools.py"


def test_detect_tools_path_returns_none_when_missing(tmp_path):
    assert detect_tools_path(tmp_path) is None


def test_make_plan_uses_detected_values(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-...")
    (tmp_path / "tools.py").write_text("# stub")

    plan = make_plan(tmp_path)
    assert plan.provider == "openai"
    assert plan.tools_path == "./tools.py"
    assert plan.model == "gpt-4o-mini"


def test_write_creates_file_and_refuses_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-...")
    plan = make_plan(tmp_path)

    target = write(tmp_path, plan)
    assert target.exists()
    body = target.read_text()
    assert 'provider = "openai"' in body
    assert 'model = "gpt-4o-mini"' in body

    with pytest.raises(FileExistsError):
        write(tmp_path, plan)

    write(tmp_path, plan, force=True)
