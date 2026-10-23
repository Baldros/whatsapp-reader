from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import sys
from typing import Any, Literal, get_args

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
MCP_HTTP_PATH = "/mcp"
MCPTransport = Literal["stdio", "http"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LOG_LEVELS = get_args(LogLevel)

logger = logging.getLogger(__name__)


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
    log_level: LogLevel = "INFO"


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
        streamable_http_path=MCP_HTTP_PATH,
        json_response=True,
        stateless_http=True,
        log_level=config.log_level,
    )
    _configure_sdk_logging(config.log_level)

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


def create_mcp_http_app(config: MCPServerConfig) -> Any:
    """Build the ASGI app served by the HTTP transport."""
    server = create_mcp_server(config)
    return PostOnlyEndpoint(server.streamable_http_app(), path=MCP_HTTP_PATH)


class PostOnlyEndpoint:
    """ASGI wrapper that answers 405 for anything but POST on the MCP endpoint.

    The server is stateless and replies in plain JSON, so it has no server-initiated
    SSE stream to offer on GET and no session to end on DELETE. The Streamable HTTP
    spec allows 405 for exactly that; without it, the SDK keeps a GET that accepts
    text/event-stream open indefinitely without ever sending an event.
    """

    def __init__(self, app: Any, *, path: str) -> None:
        self.app = app
        self.path = path

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] != "POST"
            and scope["path"].rstrip("/") == self.path
        ):
            from starlette.responses import JSONResponse

            response = JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32600,
                        "message": "Method Not Allowed: this stateless endpoint only accepts POST",
                    },
                },
                status_code=405,
                headers={"Allow": "POST"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def run_mcp_server(config: MCPServerConfig) -> None:
    if config.transport == "http":
        _run_http_server(config)
    else:
        create_mcp_server(config).run(transport="stdio")


def _run_http_server(config: MCPServerConfig) -> None:
    import uvicorn

    app = create_mcp_http_app(config)
    logger.info(
        "MCP endpoint http://%s:%s%s (stateless JSON, POST only, no authentication)",
        config.host,
        config.port,
        MCP_HTTP_PATH,
    )
    uvicorn.run(app, host=config.host, port=config.port, log_level=config.log_level.lower())


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
    add_mcp_server_options(parser)
    return parser


def add_mcp_server_options(parser: argparse.ArgumentParser) -> None:
    """Server options shared by `whatsapp-mcp` and `whatsapp-reader mcp`."""
    parser.add_argument("--chat", help="Default chat or group scope for MCP tools.")
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="MCP transport. Use http for external clients, stdio for local agent hosts.",
    )
    parser.add_argument("--host", default=DEFAULT_MCP_HOST, help="HTTP host.")
    parser.add_argument("--port", type=int, default=DEFAULT_MCP_PORT, help="HTTP port.")
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default="INFO",
        help="Server and HTTP access log level. MCP SDK internals stay at WARNING unless DEBUG.",
    )


def mcp_server_config_from_args(args: argparse.Namespace) -> MCPServerConfig:
    """Build the server config from parsed options, after the .env file was loaded."""
    return MCPServerConfig(
        db_path=resolve_db_path(args.db),
        timezone=resolve_timezone(args.timezone),
        chat_name=args.chat,
        transport=args.transport,
        host=args.host,
        port=args.port,
        env_file=args.env_file,
        log_level=args.log_level,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        load_env_file(args.env_file)
        run_mcp_server(mcp_server_config_from_args(args))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _json_object(payload: str) -> dict[str, Any]:
    value = json.loads(payload)
    if isinstance(value, dict):
        return value
    return {"result": value}


def _configure_sdk_logging(level: LogLevel) -> None:
    # In stateless mode the SDK logs every request's transport lifecycle at INFO
    # ("Processing request of type ...", "Terminating session: None"). The HTTP access
    # log already records each request, so keep the SDK at WARNING unless debugging.
    sdk_level = logging.getLevelNamesMapping()[level]
    if sdk_level > logging.DEBUG:
        sdk_level = max(sdk_level, logging.WARNING)
    logging.getLogger("mcp").setLevel(sdk_level)


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
