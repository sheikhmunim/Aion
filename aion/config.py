"""User config and token storage in ~/.aion/."""

from __future__ import annotations

import json
import os
from pathlib import Path

AION_DIR = Path.home() / ".aion"
CONFIG_FILE = AION_DIR / "config.json"
TOKENS_FILE = AION_DIR / "tokens.json"

_config_cache: dict | None = None


def ensure_dir() -> None:
    AION_DIR.mkdir(exist_ok=True)


def get_config() -> dict:
    """Load config from ~/.aion/config.json merged with env vars."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    cfg: dict = {}
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text())

    env_map = {
        "AION_GOOGLE_CLIENT_ID": "google_client_id",
        "AION_GOOGLE_CLIENT_SECRET": "google_client_secret",
        "AION_OLLAMA_URL": "ollama_url",
        "AION_OLLAMA_MODEL": "ollama_model",
        "AION_TIMEZONE": "timezone",
        "AION_DEFAULT_DURATION": "default_duration",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val:
            cfg[cfg_key] = val

    cfg.setdefault("default_duration", 60)
    cfg.setdefault("timezone", "UTC")
    cfg.setdefault("ollama_url", "http://localhost:11434")
    cfg.setdefault("ollama_model", "qwen2.5:0.5b")

    _config_cache = cfg
    return cfg


def save_config(cfg: dict) -> None:
    global _config_cache
    ensure_dir()
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    _config_cache = cfg


def get_tokens() -> dict | None:
    if not TOKENS_FILE.exists():
        return None
    return json.loads(TOKENS_FILE.read_text())


def save_tokens(tokens: dict) -> None:
    ensure_dir()
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2))


def clear_tokens() -> None:
    if TOKENS_FILE.exists():
        TOKENS_FILE.unlink()


def reload_config() -> None:
    global _config_cache
    _config_cache = None
