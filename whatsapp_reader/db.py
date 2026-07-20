from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3

from .importer import ParsedMessage


@dataclass(frozen=True)
class StoredMessage:
    id: int
    chat_name: str
    timestamp: datetime
    sender: str | None
    text: str
    message_type: str
    source_file: str
    fingerprint: str
    created_at: datetime


@dataclass(frozen=True)
class ImportResult:
    parsed: int
    inserted: int
    skipped: int


def connect(db_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_name TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            sender TEXT,
            text TEXT NOT NULL,
            message_type TEXT NOT NULL CHECK (message_type IN ('user', 'system')),
            source_file TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_name ON messages(chat_name)")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_fingerprint ON messages(fingerprint)"
    )
    connection.commit()


def import_messages(
    connection: sqlite3.Connection,
    messages: list[ParsedMessage],
    chat_name: str,
    source_file: str | Path,
) -> ImportResult:
    source_path = str(Path(source_file).resolve())
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    inserted = 0

    with connection:
        for message in messages:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO messages (
                    chat_name,
                    timestamp,
                    sender,
                    text,
                    message_type,
                    source_file,
                    fingerprint,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_name,
                    message.timestamp.isoformat(timespec="seconds"),
                    message.sender,
                    message.text,
                    message.message_type,
                    source_path,
                    message.fingerprint,
                    created_at,
                ),
            )
            inserted += cursor.rowcount

    parsed = len(messages)
    return ImportResult(parsed=parsed, inserted=inserted, skipped=parsed - inserted)


def query_messages(
    connection: sqlite3.Connection,
    *,
    chat_name: str | None = None,
    sender: str | None = None,
    from_timestamp: datetime | None = None,
    to_timestamp: datetime | None = None,
    limit: int | None = None,
) -> list[StoredMessage]:
    sql = "SELECT * FROM messages"
    conditions: list[str] = []
    params: list[str | int] = []

    if chat_name is not None:
        conditions.append("chat_name = ?")
        params.append(chat_name)
    if sender is not None:
        conditions.append("sender = ?")
        params.append(sender)
    if from_timestamp is not None:
        conditions.append("timestamp >= ?")
        params.append(from_timestamp.isoformat(timespec="seconds"))
    if to_timestamp is not None:
        conditions.append("timestamp <= ?")
        params.append(to_timestamp.isoformat(timespec="seconds"))

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    sql += " ORDER BY timestamp ASC, id ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    rows = connection.execute(sql, params).fetchall()
    return [_stored_message_from_row(row) for row in rows]


def search_messages(
    connection: sqlite3.Connection,
    *,
    text_query: str,
    chat_name: str | None = None,
    sender: str | None = None,
    from_timestamp: datetime | None = None,
    to_timestamp: datetime | None = None,
    limit: int | None = None,
) -> list[StoredMessage]:
    sql = "SELECT * FROM messages"
    conditions: list[str] = ["text LIKE ?"]
    params: list[object] = [f"%{text_query}%"]

    _append_common_filters(
        conditions,
        params,
        chat_name=chat_name,
        sender=sender,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
    )

    sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY timestamp ASC, id ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    rows = connection.execute(sql, params).fetchall()
    return [_stored_message_from_row(row) for row in rows]


def list_chats(connection: sqlite3.Connection) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT
            chat_name,
            COUNT(*) AS message_count,
            MIN(timestamp) AS first_timestamp,
            MAX(timestamp) AS last_timestamp
        FROM messages
        GROUP BY chat_name
        ORDER BY message_count DESC, chat_name ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_senders(
    connection: sqlite3.Connection,
    *,
    chat_name: str | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    sql = """
        SELECT
            sender,
            COUNT(*) AS message_count,
            MIN(timestamp) AS first_timestamp,
            MAX(timestamp) AS last_timestamp
        FROM messages
        WHERE sender IS NOT NULL
    """
    params: list[object] = []

    if chat_name is not None:
        sql += " AND chat_name = ?"
        params.append(chat_name)

    sql += " GROUP BY sender ORDER BY message_count DESC, sender ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    rows = connection.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def count_messages(
    connection: sqlite3.Connection,
    *,
    group_by: str,
    chat_name: str | None = None,
    sender: str | None = None,
    from_timestamp: datetime | None = None,
    to_timestamp: datetime | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    group_expressions = {
        "chat": "chat_name",
        "sender": "COALESCE(sender, '[system]')",
        "date": "substr(timestamp, 1, 10)",
        "hour": "substr(timestamp, 12, 2)",
        "message_type": "message_type",
    }
    if group_by not in group_expressions:
        raise ValueError(f"unsupported group_by {group_by!r}")

    expression = group_expressions[group_by]
    sql = f"""
        SELECT
            {expression} AS bucket,
            COUNT(*) AS message_count,
            MIN(timestamp) AS first_timestamp,
            MAX(timestamp) AS last_timestamp
        FROM messages
    """
    conditions: list[str] = []
    params: list[object] = []
    _append_common_filters(
        conditions,
        params,
        chat_name=chat_name,
        sender=sender,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
    )
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    sql += " GROUP BY bucket ORDER BY message_count DESC, bucket ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    rows = connection.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_message_window(
    connection: sqlite3.Connection,
    *,
    message_id: int,
    before: int,
    after: int,
) -> list[StoredMessage]:
    target = connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if target is None:
        return []

    before_rows = connection.execute(
        """
        SELECT * FROM messages
        WHERE chat_name = ? AND (timestamp < ? OR (timestamp = ? AND id < ?))
        ORDER BY timestamp DESC, id DESC
        LIMIT ?
        """,
        (target["chat_name"], target["timestamp"], target["timestamp"], message_id, before),
    ).fetchall()
    after_rows = connection.execute(
        """
        SELECT * FROM messages
        WHERE chat_name = ? AND (timestamp > ? OR (timestamp = ? AND id > ?))
        ORDER BY timestamp ASC, id ASC
        LIMIT ?
        """,
        (target["chat_name"], target["timestamp"], target["timestamp"], message_id, after),
    ).fetchall()

    rows = list(reversed(before_rows)) + [target] + list(after_rows)
    return [_stored_message_from_row(row) for row in rows]


def _append_common_filters(
    conditions: list[str],
    params: list[object],
    *,
    chat_name: str | None,
    sender: str | None,
    from_timestamp: datetime | None,
    to_timestamp: datetime | None,
) -> None:
    if chat_name is not None:
        conditions.append("chat_name = ?")
        params.append(chat_name)
    if sender is not None:
        conditions.append("sender = ?")
        params.append(sender)
    if from_timestamp is not None:
        conditions.append("timestamp >= ?")
        params.append(from_timestamp.isoformat(timespec="seconds"))
    if to_timestamp is not None:
        conditions.append("timestamp <= ?")
        params.append(to_timestamp.isoformat(timespec="seconds"))


def _stored_message_from_row(row: sqlite3.Row) -> StoredMessage:
    return StoredMessage(
        id=row["id"],
        chat_name=row["chat_name"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        sender=row["sender"],
        text=row["text"],
        message_type=row["message_type"],
        source_file=row["source_file"],
        fingerprint=row["fingerprint"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
