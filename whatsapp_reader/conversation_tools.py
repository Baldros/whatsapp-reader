from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .db import (
    StoredMessage,
    connect,
    count_messages as count_message_rows,
    get_message_window,
    import_messages,
    list_chats as list_chat_rows,
    list_senders as list_sender_rows,
    query_messages,
    search_messages as search_message_rows,
)
from .importer import DEFAULT_TIMEZONE, parse_export
from .time_utils import build_time_range


@dataclass(frozen=True)
class ConversationToolbox:
    db_path: str | Path
    timezone: str = DEFAULT_TIMEZONE
    default_chat: str | None = None

    def import_conversation(self, file_path: str, chat_name: str | None = None) -> str:
        """Import a local WhatsApp TXT export into the configured SQLite database."""
        path_value = file_path.strip()
        if not path_value:
            return _to_json({"error": "file_path is required"})

        source_path = Path(path_value).expanduser()
        if not source_path.is_absolute():
            return _to_json({"error": "file_path must be an absolute path"})
        if source_path.suffix.lower() != ".txt":
            return _to_json({"error": "file_path must point to a .txt file"})

        resolved_chat = (chat_name or self.default_chat or "").strip()
        if not resolved_chat or resolved_chat == "*":
            return _to_json(
                {"error": "chat_name is required when the server has no default chat"}
            )

        try:
            source_path = source_path.resolve(strict=True)
            if not source_path.is_file():
                return _to_json({"error": f"file_path is not a file: {source_path}"})

            messages = parse_export(source_path, self.timezone)
            with connect(self.db_path) as connection:
                result = import_messages(connection, messages, resolved_chat, source_path)
        except (OSError, ValueError) as exc:
            return _to_json({"error": str(exc)})

        return _to_json(
            {
                "chat_name": resolved_chat,
                "source_file": str(source_path),
                "timezone": self.timezone,
                "parsed": result.parsed,
                "inserted": result.inserted,
                "skipped": result.skipped,
            }
        )

    def list_chats(self) -> str:
        with connect(self.db_path) as connection:
            rows = list_chat_rows(connection)
        return _to_json({"count": len(rows), "chats": rows})

    def list_senders(self, chat_name: str | None = None, limit: int | None = 100) -> str:
        with connect(self.db_path) as connection:
            rows = list_sender_rows(
                connection,
                chat_name=self._resolve_chat(chat_name),
                limit=_clamp_limit(limit, default=100, maximum=500),
            )
        return _to_json({"count": len(rows), "senders": rows})

    def get_messages(
        self,
        date: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        sender: str | None = None,
        chat_name: str | None = None,
        limit: int | None = 50,
        order: str = "asc",
    ) -> str:
        limit_value = _clamp_limit(limit, default=50, maximum=200)
        try:
            from_timestamp, to_timestamp = build_time_range(
                date,
                from_time,
                to_time,
                self.timezone,
            )
            with connect(self.db_path) as connection:
                messages = query_messages(
                    connection,
                    chat_name=self._resolve_chat(chat_name),
                    sender=sender or None,
                    from_timestamp=from_timestamp,
                    to_timestamp=to_timestamp,
                    limit=limit_value + 1,
                    order=order,
                )
        except ValueError as exc:
            return _to_json({"error": str(exc)})

        return _page_payload(messages, limit_value)

    def search_messages(
        self,
        text_query: str,
        date: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        sender: str | None = None,
        chat_name: str | None = None,
        limit: int | None = 50,
        order: str = "asc",
    ) -> str:
        if not text_query.strip():
            return _to_json({"error": "text_query is required"})

        limit_value = _clamp_limit(limit, default=50, maximum=200)
        try:
            from_timestamp, to_timestamp = build_time_range(
                date,
                from_time,
                to_time,
                self.timezone,
            )
            with connect(self.db_path) as connection:
                messages = search_message_rows(
                    connection,
                    text_query=text_query.strip(),
                    chat_name=self._resolve_chat(chat_name),
                    sender=sender or None,
                    from_timestamp=from_timestamp,
                    to_timestamp=to_timestamp,
                    limit=limit_value + 1,
                    order=order,
                )
        except ValueError as exc:
            return _to_json({"error": str(exc)})

        return _page_payload(messages, limit_value)

    def count_messages(
        self,
        group_by: str = "sender",
        date: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        sender: str | None = None,
        chat_name: str | None = None,
        limit: int | None = 100,
    ) -> str:
        try:
            from_timestamp, to_timestamp = build_time_range(
                date,
                from_time,
                to_time,
                self.timezone,
            )
            with connect(self.db_path) as connection:
                rows = count_message_rows(
                    connection,
                    group_by=group_by,
                    chat_name=self._resolve_chat(chat_name),
                    sender=sender or None,
                    from_timestamp=from_timestamp,
                    to_timestamp=to_timestamp,
                    limit=_clamp_limit(limit, default=100, maximum=500),
                )
        except ValueError as exc:
            return _to_json({"error": str(exc)})

        return _to_json({"count": len(rows), "group_by": group_by, "rows": rows})

    def get_context_around(
        self,
        message_id: int,
        before: int | None = 10,
        after: int | None = 10,
    ) -> str:
        before_value = _clamp_limit(before, default=10, maximum=100)
        after_value = _clamp_limit(after, default=10, maximum=100)
        with connect(self.db_path) as connection:
            messages = get_message_window(
                connection,
                message_id=message_id,
                before=before_value,
                after=after_value,
            )
        return _messages_payload(messages, before_value + after_value + 1)

    def _resolve_chat(self, chat_name: str | None) -> str | None:
        if chat_name == "*":
            return None
        return chat_name or self.default_chat


def _page_payload(messages: list[StoredMessage], limit: int) -> str:
    """Payload for a query run with limit + 1: a surplus row means more messages matched."""
    return _messages_payload(messages[:limit], limit, has_more=len(messages) > limit)


def _messages_payload(
    messages: list[StoredMessage],
    limit: int,
    has_more: bool | None = None,
) -> str:
    payload: dict[str, object] = {"count": len(messages), "limit": limit}
    if has_more is not None:
        payload["has_more"] = has_more
    payload["messages"] = [_message_to_dict(message) for message in messages]
    return _to_json(payload)


def _message_to_dict(message: StoredMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "chat_name": message.chat_name,
        "timestamp": message.timestamp.isoformat(timespec="seconds"),
        "sender": message.sender,
        "text": message.text,
        "message_type": message.message_type,
    }


def _clamp_limit(value: int | None, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    return max(1, min(int(value), maximum))


def _to_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
