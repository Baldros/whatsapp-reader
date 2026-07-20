from __future__ import annotations

import os
from pathlib import Path

from .importer import DEFAULT_TIMEZONE


DEFAULT_DB = "whatsapp.sqlite3"
DEFAULT_ENV_FILE = ".env"
DEFAULT_AGENT_MODEL = "openai:gpt-5.6-terra"
DEFAULT_CHAT_HISTORY_FILE = ".whatsapp-reader-history"
DEFAULT_CHAT_MEMORY_DB = ".whatsapp-reader-memory.sqlite3"
DEFAULT_CHAT_SESSION = "default"


def load_env_file(path: str | Path | None = DEFAULT_ENV_FILE) -> None:
    """Load KEY=VALUE pairs from a local .env file without overriding the process env."""
    if path is None:
        return

    env_path = Path(path)
    if not env_path.exists():
        return

    with env_path.open("r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = _strip_env_value(value.strip())
            if key and key not in os.environ:
                os.environ[key] = value


def resolve_db_path(cli_value: str | Path | None = None) -> str:
    if cli_value:
        return str(cli_value)
    return os.environ.get("WHATSAPP_READER_DB", DEFAULT_DB)


def resolve_timezone(cli_value: str | None = None) -> str:
    if cli_value:
        return cli_value
    return os.environ.get("WHATSAPP_READER_TIMEZONE", DEFAULT_TIMEZONE)


def resolve_agent_model(cli_value: str | None = None) -> str:
    if cli_value:
        return cli_value
    return os.environ.get("WHATSAPP_READER_MODEL", DEFAULT_AGENT_MODEL)


def resolve_chat_history_file(cli_value: str | Path | None = None) -> str:
    if cli_value:
        return str(cli_value)
    return os.environ.get("WHATSAPP_READER_HISTORY_FILE", DEFAULT_CHAT_HISTORY_FILE)


def resolve_chat_memory_db(cli_value: str | Path | None = None) -> str:
    if cli_value:
        return str(cli_value)
    return os.environ.get("WHATSAPP_READER_MEMORY_DB", DEFAULT_CHAT_MEMORY_DB)


def resolve_chat_session(cli_value: str | None = None) -> str:
    if cli_value:
        return cli_value
    return os.environ.get("WHATSAPP_READER_SESSION", DEFAULT_CHAT_SESSION)


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
