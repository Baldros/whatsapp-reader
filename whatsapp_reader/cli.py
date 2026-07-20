from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .db import StoredMessage, connect, import_messages, query_messages
from .config import (
    DEFAULT_DB,
    DEFAULT_ENV_FILE,
    load_env_file,
    resolve_agent_model,
    resolve_chat_history_file,
    resolve_chat_memory_db,
    resolve_chat_session,
    resolve_db_path,
    resolve_timezone,
)
from .importer import DEFAULT_TIMEZONE, ParseError, parse_export
from .time_utils import build_time_range


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "import":
            return _run_import(args)
        if args.command == "query":
            return _run_query(args)
        if args.command == "export-context":
            return _run_export_context(args)
        if args.command == "ask":
            return _run_ask(args)
        if args.command == "chat":
            return _run_chat(args)
    except (OSError, ParseError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error("missing command")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="whatsapp-reader",
        description="Import and query local WhatsApp TXT exports.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import", help="Import a WhatsApp TXT export.")
    _add_common_options(import_parser)
    import_parser.add_argument("file", type=Path, help="Path to the exported .txt file.")
    import_parser.add_argument("--chat", required=True, help="Chat or group name.")

    query_parser = subparsers.add_parser("query", help="List messages matching filters.")
    _add_common_options(query_parser)
    _add_filter_options(query_parser)
    query_parser.add_argument("--limit", type=int, help="Maximum number of messages to print.")

    export_parser = subparsers.add_parser("export-context", help="Export clean context for LLM use.")
    _add_common_options(export_parser)
    _add_filter_options(export_parser)
    export_parser.add_argument(
        "--format",
        choices=("json", "txt"),
        default="json",
        help="Output format.",
    )

    ask_parser = subparsers.add_parser("ask", help="Ask an agent about imported conversations.")
    _add_common_options(ask_parser)
    ask_parser.add_argument("question", nargs="+", help="Question to answer using conversation tools.")
    ask_parser.add_argument("--chat", help="Default chat or group scope for agent tools.")
    ask_parser.add_argument("--model", help="LangChain model id. Default: WHATSAPP_READER_MODEL.")

    chat_parser = subparsers.add_parser("chat", help="Open an interactive terminal chat.")
    _add_common_options(chat_parser)
    chat_parser.add_argument("message", nargs="*", help="Optional first message to send.")
    chat_parser.add_argument("--chat", help="Default chat or group scope for agent tools.")
    chat_parser.add_argument("--model", help="LangChain model id. Default: WHATSAPP_READER_MODEL.")
    chat_parser.add_argument("--session", help="Conversation memory session id.")
    chat_parser.add_argument("--memory-db", help="SQLite checkpoint database for chat memory.")
    chat_parser.add_argument("--history-file", help="Prompt history file.")
    chat_parser.add_argument("--no-stream", action="store_true", help="Disable streamed answers.")

    return parser


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", help=f"SQLite database path. Default: {DEFAULT_DB}")
    parser.add_argument(
        "--env-file",
        default=DEFAULT_ENV_FILE,
        help=f"Local env file to load. Default: {DEFAULT_ENV_FILE}",
    )
    parser.add_argument(
        "--timezone",
        default=None,
        help=f"Timezone used for TXT and query dates. Default: {DEFAULT_TIMEZONE}",
    )


def _add_filter_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--chat", help="Filter by chat or group name.")
    parser.add_argument("--date", help="Filter by local date in YYYY-MM-DD format.")
    parser.add_argument("--sender", help="Filter by exact sender name or phone number.")
    parser.add_argument(
        "--from",
        dest="from_",
        help='Filter from local datetime, for example "2026-07-20 09:00".',
    )
    parser.add_argument(
        "--to",
        dest="to",
        help='Filter until local datetime, for example "2026-07-20 12:00".',
    )


def _run_import(args: argparse.Namespace) -> int:
    load_env_file(args.env_file)
    db_path = resolve_db_path(args.db)
    timezone = resolve_timezone(args.timezone)
    messages = parse_export(args.file, timezone)
    with connect(db_path) as connection:
        result = import_messages(connection, messages, args.chat, args.file)

    print(
        f"parsed={result.parsed} inserted={result.inserted} skipped={result.skipped} "
        f"db={db_path}"
    )
    return 0


def _run_query(args: argparse.Namespace) -> int:
    messages = _load_filtered_messages(args)
    print(format_messages_as_txt(messages))
    return 0


def _run_export_context(args: argparse.Namespace) -> int:
    messages = _load_filtered_messages(args)
    if args.format == "json":
        print(format_messages_as_json(messages))
    else:
        print(format_messages_as_txt(messages))
    return 0


def _run_ask(args: argparse.Namespace) -> int:
    load_env_file(args.env_file)
    from .agent import ask_conversation_agent

    answer = ask_conversation_agent(
        " ".join(args.question),
        db_path=resolve_db_path(args.db),
        timezone=resolve_timezone(args.timezone),
        model=resolve_agent_model(args.model),
        default_chat=args.chat,
    )
    print(answer)
    return 0


def _run_chat(args: argparse.Namespace) -> int:
    load_env_file(args.env_file)
    from .terminal_chat import TerminalChatConfig, run_terminal_chat

    config = TerminalChatConfig(
        db_path=resolve_db_path(args.db),
        timezone=resolve_timezone(args.timezone),
        model=resolve_agent_model(args.model),
        chat_name=args.chat,
        session_id=resolve_chat_session(args.session),
        memory_db=resolve_chat_memory_db(args.memory_db),
        history_file=resolve_chat_history_file(args.history_file),
        stream=not args.no_stream,
    )
    run_terminal_chat(
        config=config,
        initial_message=" ".join(args.message) if args.message else None,
    )
    return 0


def _load_filtered_messages(args: argparse.Namespace) -> list[StoredMessage]:
    load_env_file(args.env_file)
    db_path = resolve_db_path(args.db)
    timezone = resolve_timezone(args.timezone)
    from_timestamp, to_timestamp = build_time_range(args.date, args.from_, args.to, timezone)
    with connect(db_path) as connection:
        return query_messages(
            connection,
            chat_name=args.chat,
            sender=args.sender,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
            limit=getattr(args, "limit", None),
        )


def format_messages_as_txt(messages: list[StoredMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        prefix = message.timestamp.strftime("[%Y-%m-%d %H:%M]")
        if message.sender:
            lines.append(f"{prefix} {message.sender}: {message.text}")
        else:
            lines.append(f"{prefix} [system] {message.text}")
    return "\n".join(lines)


def format_messages_as_json(messages: list[StoredMessage]) -> str:
    payload = [
        {
            "chat_name": message.chat_name,
            "timestamp": message.timestamp.isoformat(timespec="seconds"),
            "sender": message.sender,
            "text": message.text,
            "message_type": message.message_type,
        }
        for message in messages
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)
