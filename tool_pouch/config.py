"""Provider config resolution. Decides which LLM backend to use for the judge.

Resolution order (first match wins):
1. Explicit CLI flag (passed via env var AGENT_SIM_JUDGE_PROVIDER for the call)
2. Saved config at ~/.tool_pouch/config.json
3. Env vars (AGENT_SIM_JUDGE_PROVIDER and friends)
4. Auto-detect from ANTHROPIC_API_KEY or OPENAI_API_KEY
5. Interactive setup (if running in a TTY and the user wants it)
6. None - judge falls back gracefully and the user sees a hint
"""
import json
import os
import sys
from pathlib import Path


CONFIG_PATH = Path.home() / ".tool-pouch" / "config.json"


def resolve_provider():
    """Return the resolved provider config dict or None if nothing's configured.

    Sets AGENT_SIM_JUDGE_* env vars for the duration of the process so the
    judge module picks them up without further coordination.
    """
    cfg = (
        _from_env()
        or _from_config_file()
        or _autodetect()
    )

    if cfg:
        _apply_to_env(cfg)

    return cfg


def first_run_setup_if_needed():
    """If no config exists anywhere, prompt the user once and save their choice.

    Skipped silently if not running in an interactive terminal (CI, scripts).
    """
    if resolve_provider():
        return  # Already configured

    if not sys.stdin.isatty():
        return  # Non-interactive, skip the prompt

    cfg = _interactive_setup()
    if cfg:
        save_config(cfg)
        _apply_to_env(cfg)


def save_config(cfg):
    """Persist config to ~/.tool_pouch/config.json."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


# ---------- internal helpers ----------

def _from_env():
    """User explicitly set AGENT_SIM_JUDGE_PROVIDER."""
    provider = os.environ.get("AGENT_SIM_JUDGE_PROVIDER")
    if not provider:
        return None
    return {
        "provider": provider,
        "model": os.environ.get("AGENT_SIM_JUDGE_MODEL"),
        "base_url": os.environ.get("AGENT_SIM_JUDGE_BASE_URL"),
    }


def _from_config_file():
    if not CONFIG_PATH.exists():
        return None
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _autodetect():
    """Pick a provider based on what's already in the environment.

    Preference order matches `tool_pouch.init.detect_provider` so that an
    autodetected judge agrees with an autodetected agent provider when
    both API keys are present.
    """
    if os.environ.get("OPENAI_API_KEY"):
        return {"provider": "openai"}
    if os.environ.get("ANTHROPIC_API_KEY"):
        return {"provider": "anthropic"}
    return None


def _interactive_setup():
    print()
    print("─" * 60)
    print("tool-pouch: first-run setup")
    print("─" * 60)
    print()
    print("Tool Pouch uses an LLM to classify failures (hallucination,")
    print("silent_wrong, etc.) and suggest fixes. Pick a provider:")
    print()
    print("  [1] Anthropic   (recommended; needs ANTHROPIC_API_KEY)")
    print("  [2] OpenAI      (needs OPENAI_API_KEY)")
    print("  [3] Ollama      (local, fully offline)")
    print("  [4] Skip        (run without classification)")
    print()

    choice = input("Choice [1]: ").strip() or "1"

    if choice == "1":
        return {"provider": "anthropic"}
    if choice == "2":
        return {"provider": "openai"}
    if choice == "3":
        url = input("Ollama URL [http://localhost:11434/v1]: ").strip()
        model = input("Model [llama3.1]: ").strip()
        return {
            "provider": "ollama",
            "base_url": url or "http://localhost:11434/v1",
            "model": model or "llama3.1",
        }
    return None  # User chose to skip


def _apply_to_env(cfg):
    """Set env vars so the judge module reads the resolved config."""
    if cfg.get("provider"):
        os.environ["AGENT_SIM_JUDGE_PROVIDER"] = cfg["provider"]
    if cfg.get("model"):
        os.environ["AGENT_SIM_JUDGE_MODEL"] = cfg["model"]
    if cfg.get("base_url"):
        os.environ["AGENT_SIM_JUDGE_BASE_URL"] = cfg["base_url"]
