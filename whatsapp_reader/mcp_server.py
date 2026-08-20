from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Literal

from .config import (
    DEFAULT_ENV_FILE,
    DEFAULT_MCP_HOST,
    DEFAULT_MCP_PORT,
    load_env_file,
    resolve_db_path,
    resolve_timezone,
)
from .conversation_tools import ConversationToolbox


MCP_SERVER_NAME = "WhatsApp Reader"
MCP_CONNECTION_NAME = "whatsapp"
MCPTransport = Literal["stdio", "http"]


class MCPDependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class MCPServerConfig:
    db_path: str | Path
    timezone: str
    chat_name: str | None = None
    transport: MCPTransport = "stdio"
    host: str = DEFAULT_MCP_HOST
    port: int = DEFAULT_MCP_PORT
    env_file: str | Path | None = DEFAULT_ENV_FILE


def create_mcp_server(config: MCPServerConfig) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise MCPDependencyError(
            "MCP dependencies are missing. Install them with "
            '`python3 -m pip install -e ".[mcp]"` or `python3 -m pip install -e ".[chat]"`.'
        ) from exc

    toolbox = ConversationToolbox(
        db_path=config.db_path,
        timezone=config.timezone,
        default_chat=config.chat_name,
    )
    server = FastMCP(
        MCP_SERVER_NAME,
        instructions=_build_server_instructions(config),
        host=config.host,
        port=config.port,
        json_response=True,
        stateless_http=True,
    )

    @server.tool()
    def import_conversation(
        file_path: str,
        chat_name: str | None = None,
    ) -> dict[str, Any]:
        """Import a local WhatsApp .txt export by absolute path.

        The import is idempotent. Omit chat_name to use the server's default chat.
        """
        return _json_object(
            toolbox.import_conversation(file_path=file_path, chat_name=chat_name)
        )

    @server.tool()
    def list_chats() -> dict[str, Any]:
        """List imported WhatsApp chats with message counts and date ranges."""
        return _json_object(toolbox.list_chats())

    @server.tool()
    def list_senders(chat_name: str | None = None, limit: int = 100) -> dict[str, Any]:
        """List senders in a chat. Use chat_name='*' to ignore the default chat scope."""
        return _json_object(toolbox.list_senders(chat_name=chat_name, limit=limit))

    @server.tool()
    def get_messages(
        date: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        sender: str | None = None,
        chat_name: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Fetch chronological messages by date, time range, sender, and chat."""
        return _json_object(
            toolbox.get_messages(
                date=date,
                from_time=from_time,
                to_time=to_time,
                sender=sender,
                chat_name=chat_name,
                limit=limit,
            )
        )

    @server.tool()
    def search_messages(
        text_query: str,
        date: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        sender: str | None = None,
        chat_name: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search message text with optional date, time range, sender, and chat filters."""
        return _json_object(
            toolbox.search_messages(
                text_query=text_query,
                date=date,
                from_time=from_time,
                to_time=to_time,
                sender=sender,
                chat_name=chat_name,
                limit=limit,
            )
        )

    @server.tool()
    def count_messages(
        group_by: str = "sender",
        date: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        sender: str | None = None,
        chat_name: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Count messages grouped by chat, sender, date, hour, or message_type."""
        return _json_object(
            toolbox.count_messages(
                group_by=group_by,
                date=date,
                from_time=from_time,
                to_time=to_time,
                sender=sender,
                chat_name=chat_name,
                limit=limit,
            )
        )

    @server.tool()
    def get_context_around(
        message_id: int,
        before: int = 10,
        after: int = 10,
    ) -> dict[str, Any]:
        """Fetch neighboring messages in the same chat around a specific message id."""
        return _json_object(
            toolbox.get_context_around(message_id=message_id, before=before, after=after)
        )

    @server.resource("whatsapp://chats")
    def chats_resource() -> str:
        """Read imported WhatsApp chats as JSON."""
        return toolbox.list_chats()

    @server.prompt(title="WhatsApp Conversation Analyst")
    def analyze_whatsapp_conversations(question: str, chat_name: str | None = None) -> str:
        """Create a short prompt for analyzing imported WhatsApp conversations."""
        scope = chat_name or config.chat_name or "all imported chats"
        return (
            "Answer the user's question using the WhatsApp Reader MCP tools before making "
            "claims about messages, people, dates, counts, or timelines.\n\n"
            f"Timezone: {config.timezone}\n"
            f"Chat scope: {scope}\n"
            f"Question: {question}"
        )

    return server


def run_mcp_server(config: MCPServerConfig) -> None:
    server = create_mcp_server(config)
    transport = "streamable-http" if config.transport == "http" else "stdio"
    server.run(transport=transport)


def build_mcp_stdio_connection(
    *,
    db_path: str | Path,
    timezone: str,
    chat_name: str | None = None,
    env_file: str | Path | None = DEFAULT_ENV_FILE,
    command: str | None = None,
    cwd: str | Path | None = None,
) -> dict[str, object]:
    args = [
        "-m",
        "whatsapp_reader.mcp_server",
        "--transport",
        "stdio",
        "--db",
        str(db_path),
        "--timezone",
        timezone,
    ]
    if env_file:
        args.extend(["--env-file", str(env_file)])
    if chat_name:
        args.extend(["--chat", chat_name])

    connection: dict[str, object] = {
        "transport": "stdio",
        "command": command or sys.executable,
        "args": args,
    }
    if cwd is not None:
        connection["cwd"] = str(cwd)
    return connection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="whatsapp-mcp",
        description="Run the WhatsApp Reader MCP server.",
    )
    parser.add_argument("--db", help="SQLite database path. Default: WHATSAPP_READER_DB.")
    parser.add_argument(
        "--env-file",
        default=DEFAULT_ENV_FILE,
        help=f"Local env file to load. Default: {DEFAULT_ENV_FILE}",
    )
    parser.add_argument("--timezone", help="Timezone used for date filters.")
    parser.add_argument("--chat", help="Default chat or group scope for MCP tools.")
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="MCP transport. Use http for external clients, stdio for local agent hosts.",
    )
    parser.add_argument("--host", default=DEFAULT_MCP_HOST, help="HTTP host.")
    parser.add_argument("--port", type=int, default=DEFAULT_MCP_PORT, help="HTTP port.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        load_env_file(args.env_file)
        run_mcp_server(
            MCPServerConfig(
                db_path=resolve_db_path(args.db),
                timezone=resolve_timezone(args.timezone),
                chat_name=args.chat,
                transport=args.transport,
                host=args.host,
                port=args.port,
                env_file=args.env_file,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _json_object(payload: str) -> dict[str, Any]:
    value = json.loads(payload)
    if isinstance(value, dict):
        return value
    return {"result": value}


def _build_server_instructions(config: MCPServerConfig) -> str:
    chat_scope = config.chat_name or "all imported chats"
    return (
        "Structured local tools for importing and reading WhatsApp conversations in SQLite. "
        "When the user provides the absolute path to a WhatsApp .txt export and asks to "
        "ingest it, use import_conversation. "
        "Use these tools before answering questions about message contents, senders, "
        "dates, timelines, or counts. "
        f"Timezone: {config.timezone}. Default chat scope: {chat_scope}."
    )


if __name__ == "__main__":
    raise SystemExit(main())
