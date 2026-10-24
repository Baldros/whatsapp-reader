from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from pathlib import Path
import sqlite3
from types import TracebackType

from .importer import ParsedMessage, message_fingerprints


# Stored in PRAGMA user_version.
# 1: fingerprints include the chat and the occurrence of identical messages.
# 2: messages_fts, a full-text index of message text that ignores case and accents.
# 3: indexes for ordering and filtering by instant instead of local timestamp text.
SCHEMA_VERSION = 3

# strftime('%s') supports SQLite versions predating unixepoch(). Stored messages have
# whole-second precision; numeric bounds retain fractional seconds supplied by callers.
_TIME_SQL = "CAST(strftime('%s', timestamp) AS INTEGER)"


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


class ManagedConnection(sqlite3.Connection):
    """SQLite connection whose outermost context commits or rolls back, then closes."""

    def __enter__(self) -> ManagedConnection:
        super().__enter__()
        self._context_depth = getattr(self, "_context_depth", 0) + 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        self._context_depth = max(getattr(self, "_context_depth", 1) - 1, 0)
        if self._context_depth:
            # Helpers may enter again; successful nested contexts must not commit.
            if exc_type is not None:
                self.rollback()
            return None
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(db_path: str | Path) -> ManagedConnection:
    _ensure_parent_directory(db_path)
    connection = sqlite3.connect(str(db_path), factory=ManagedConnection)
    connection.row_factory = sqlite3.Row
    try:
        initialize_schema(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def _ensure_parent_directory(db_path: str | Path) -> None:
    """Create the folder that will hold the database.

    A database root holding one folder per database starts empty, and SQLite refuses to
    create a file in a directory that does not exist. In-memory databases name no file.
    """
    text = str(db_path)
    if not text or text == ":memory:" or text.startswith("file:"):
        return
    Path(text).parent.mkdir(parents=True, exist_ok=True)


def initialize_schema(connection: sqlite3.Connection) -> None:
    _migrate_schema(connection)


def _create_base_schema(connection: sqlite3.Connection) -> None:
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


def _migrate_schema(connection: sqlite3.Connection) -> None:
    _check_schema_version(_schema_version(connection))

    connection.execute("BEGIN IMMEDIATE")
    try:
        # Even an apparently current database may be undergoing an upgrade. Wait for
        # its writer, then validate again before touching any schema or version marker.
        version = _schema_version(connection)
        _check_schema_version(version)
        if version == SCHEMA_VERSION:
            connection.commit()
            return

        _create_base_schema(connection)
        if version < 1:
            _rebuild_fingerprints(connection)
        if version < 2:
            _create_text_index(connection)
        if version < 3:
            _create_time_indexes(connection)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _check_schema_version(version: int) -> None:
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema {version} is newer than supported schema {SCHEMA_VERSION}. "
            "Upgrade WhatsApp Reader before opening this database."
        )


def _schema_version(connection: sqlite3.Connection) -> int:
    return connection.execute("PRAGMA user_version").fetchone()[0]


def _rebuild_fingerprints(connection: sqlite3.Connection) -> None:
    """Recompute fingerprints written before version 1.

    Older fingerprints ignored the chat and collapsed identical messages sent in the
    same minute. Rows are renumbered in insertion order, so reimporting the original
    export afterwards inserts only the messages that were collapsed.
    """
    rows = connection.execute(
        """
        SELECT id, chat_name, timestamp, sender, text, message_type
        FROM messages
        ORDER BY chat_name ASC, timestamp ASC, id ASC
        """
    ).fetchall()
    for chat_name, chat_rows in groupby(rows, key=lambda row: row[1]):
        chat_rows = list(chat_rows)
        messages = [
            ParsedMessage(
                timestamp=datetime.fromisoformat(timestamp),
                sender=sender,
                text=text,
                message_type=message_type,
            )
            for _id, _chat, timestamp, sender, text, message_type in chat_rows
        ]
        connection.executemany(
            "UPDATE messages SET fingerprint = ? WHERE id = ?",
            zip(message_fingerprints(messages, chat_name), (row[0] for row in chat_rows)),
        )


def _create_text_index(connection: sqlite3.Connection) -> None:
    """Create the full-text index of message text and index the existing rows.

    The unicode61 tokenizer with remove_diacritics folds case and accents, so
    "reuniao" finds "Reunião". The index keeps no copy of the text: it reads it from
    messages, and triggers keep it in sync with inserts, updates and deletes.
    """
    try:
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                text,
                content='messages',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )
    except sqlite3.OperationalError as exc:
        if "fts5" not in str(exc):
            raise
        raise RuntimeError(
            "This Python's SQLite has no FTS5, which text search needs. "
            "Use a Python build whose sqlite3 module includes FTS5."
        ) from exc

    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, text)
            VALUES ('delete', old.id, old.text);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE OF text ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, text)
            VALUES ('delete', old.id, old.text);
            INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
        END
        """
    )
    connection.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")


def _create_time_indexes(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_messages_instant ON messages({_TIME_SQL}, id)"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_messages_chat_instant "
        f"ON messages(chat_name, {_TIME_SQL}, id)"
    )


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
        for message, fingerprint in zip(messages, message_fingerprints(messages, chat_name)):
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
                    fingerprint,
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
    order: str = "asc",
) -> list[StoredMessage]:
    sql = "SELECT * FROM messages"
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

    sql += _order_by_time(order)
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
    order: str = "asc",
) -> list[StoredMessage]:
    sql = "SELECT * FROM messages"
    conditions: list[str]
    params: list[object]
    if any(character.isalnum() for character in text_query):
        # Adjacent words in this order, ignoring case and accents. The trailing * lets
        # the last word match longer words too ("reuni" finds "reuniões").
        conditions = ["id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)"]
        params = ['"' + text_query.replace('"', '""') + '"*']
    else:
        # Only emoji or punctuation: the index has no words for these, so scan the text.
        conditions = ["instr(text, ?) > 0"]
        params = [text_query]

    _append_common_filters(
        conditions,
        params,
        chat_name=chat_name,
        sender=sender,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
    )

    sql += " WHERE " + " AND ".join(conditions)
    sql += _order_by_time(order)
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
            MIN(strftime('%Y-%m-%dT%H:%M:%S+00:00', timestamp)) AS first_timestamp,
            MAX(strftime('%Y-%m-%dT%H:%M:%S+00:00', timestamp)) AS last_timestamp
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
            MIN(strftime('%Y-%m-%dT%H:%M:%S+00:00', timestamp)) AS first_timestamp,
            MAX(strftime('%Y-%m-%dT%H:%M:%S+00:00', timestamp)) AS last_timestamp
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
            MIN(strftime('%Y-%m-%dT%H:%M:%S+00:00', timestamp)) AS first_timestamp,
            MAX(strftime('%Y-%m-%dT%H:%M:%S+00:00', timestamp)) AS last_timestamp
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

    # The explicit time bound lets SQLite seek the expression index before the id tie-breaker.
    target_instant = datetime.fromisoformat(target["timestamp"]).timestamp()
    before_rows = connection.execute(
        f"""
        SELECT * FROM messages
        WHERE chat_name = ? AND {_TIME_SQL} <= ? AND ({_TIME_SQL}, id) < (?, ?)
        ORDER BY {_TIME_SQL} DESC, id DESC
        LIMIT ?
        """,
        (target["chat_name"], target_instant, target_instant, message_id, before),
    ).fetchall()
    after_rows = connection.execute(
        f"""
        SELECT * FROM messages
        WHERE chat_name = ? AND {_TIME_SQL} >= ? AND ({_TIME_SQL}, id) > (?, ?)
        ORDER BY {_TIME_SQL} ASC, id ASC
        LIMIT ?
        """,
        (target["chat_name"], target_instant, target_instant, message_id, after),
    ).fetchall()

    rows = list(reversed(before_rows)) + [target] + list(after_rows)
    return [_stored_message_from_row(row) for row in rows]


def _order_by_time(order: str) -> str:
    directions = {"asc": "ASC", "desc": "DESC"}
    if order not in directions:
        raise ValueError(f"unsupported order {order!r}; use 'asc' or 'desc'")
    direction = directions[order]
    return f" ORDER BY {_TIME_SQL} {direction}, id {direction}"


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
        conditions.append(f"{_TIME_SQL} >= ?")
        params.append(from_timestamp.timestamp())
    if to_timestamp is not None:
        conditions.append(f"{_TIME_SQL} <= ?")
        params.append(to_timestamp.timestamp())


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
