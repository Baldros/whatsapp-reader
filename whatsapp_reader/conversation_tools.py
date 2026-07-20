from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .db import (
    StoredMessage,
    connect,
    count_messages as count_message_rows,
    get_message_window,
    list_chats as list_chat_rows,
    list_senders as list_sender_rows,
    query_messages,
    search_messages as search_message_rows,
)
from .importer import DEFAULT_TIMEZONE
from .time_utils import build_time_range


@dataclass(frozen=True)
class ConversationToolbox:
    db_path: str | Path
    timezone: str = DEFAULT_TIMEZONE
    default_chat: str | None = None

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
    ) -> str:
        try:
            from_timestamp, to_timestamp = build_time_range(
                date,
                from_time,
                to_time,
                self.timezone,
            )
        except ValueError as exc:
            return _to_json({"error": str(exc)})

        limit_value = _clamp_limit(limit, default=50, maximum=200)
        with connect(self.db_path) as connection:
            messages = query_messages(
                connection,
                chat_name=self._resolve_chat(chat_name),
                sender=sender or None,
                from_timestamp=from_timestamp,
                to_timestamp=to_timestamp,
                limit=limit_value,
            )
        return _messages_payload(messages, limit_value)

    def search_messages(
        self,
        text_query: str,
        date: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        sender: str | None = None,
        chat_name: str | None = None,
        limit: int | None = 50,
    ) -> str:
        if not text_query.strip():
            return _to_json({"error": "text_query is required"})

        try:
            from_timestamp, to_timestamp = build_time_range(
                date,
                from_time,
                to_time,
                self.timezone,
            )
        except ValueError as exc:
            return _to_json({"error": str(exc)})

        limit_value = _clamp_limit(limit, default=50, maximum=200)
        with connect(self.db_path) as connection:
            messages = search_message_rows(
                connection,
                text_query=text_query.strip(),
                chat_name=self._resolve_chat(chat_name),
                sender=sender or None,
                from_timestamp=from_timestamp,
                to_timestamp=to_timestamp,
                limit=limit_value,
            )
        return _messages_payload(messages, limit_value)

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


def _messages_payload(messages: list[StoredMessage], limit: int) -> str:
    return _to_json(
        {
            "count": len(messages),
            "limit": limit,
            "messages": [_message_to_dict(message) for message in messages],
        }
    )


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
