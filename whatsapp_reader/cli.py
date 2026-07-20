from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import StoredMessage, connect, import_messages, query_messages
from .importer import DEFAULT_TIMEZONE, ParseError, parse_export


DEFAULT_DB = "whatsapp.sqlite3"


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
    except (OSError, ParseError, ValueError) as exc:
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

    return parser


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database path. Default: {DEFAULT_DB}")
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
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
    messages = parse_export(args.file, args.timezone)
    with connect(args.db) as connection:
        result = import_messages(connection, messages, args.chat, args.file)

    print(
        f"parsed={result.parsed} inserted={result.inserted} skipped={result.skipped} "
        f"db={args.db}"
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


def _load_filtered_messages(args: argparse.Namespace) -> list[StoredMessage]:
    from_timestamp, to_timestamp = _build_time_range(args.date, args.from_, args.to, args.timezone)
    with connect(args.db) as connection:
        return query_messages(
            connection,
            chat_name=args.chat,
            sender=args.sender,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
            limit=getattr(args, "limit", None),
        )


def _build_time_range(
    date_value: str | None,
    from_value: str | None,
    to_value: str | None,
    timezone_name: str,
) -> tuple[datetime | None, datetime | None]:
    if date_value and (from_value or to_value):
        raise ValueError("use either --date or --from/--to, not both")

    timezone = ZoneInfo(timezone_name)
    if date_value:
        start = datetime.strptime(date_value, "%Y-%m-%d").replace(tzinfo=timezone)
        return start, start + timedelta(days=1) - timedelta(seconds=1)

    start = _parse_cli_datetime(from_value, timezone) if from_value else None
    end = _parse_cli_datetime(to_value, timezone) if to_value else None
    if start and end and start > end:
        raise ValueError("--from must be before or equal to --to")
    return start, end


def _parse_cli_datetime(value: str, timezone: ZoneInfo) -> datetime:
    normalized = value.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized, fmt).replace(tzinfo=timezone)
        except ValueError:
            pass
    raise ValueError(f"invalid datetime {value!r}; expected YYYY-MM-DD HH:MM")


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
