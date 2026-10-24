from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stderr
from datetime import datetime
import importlib.util
from io import StringIO
import json
import logging
from pathlib import Path
import sqlite3
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

from whatsapp_reader import db
from whatsapp_reader.cli import main as cli_main
from whatsapp_reader.conversation_tools import ConversationToolbox
from whatsapp_reader.importer import ParsedMessage
from whatsapp_reader.mcp_server import MCPServerConfig, create_mcp_http_app
from whatsapp_reader.time_utils import build_time_range
from whatsapp_reader.terminal_chat import TerminalChatApp, TerminalChatConfig


class ReviewRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db_path = self.root / "messages.sqlite3"

    def _message(self, text: str, timestamp: str = "2024-01-02T08:00:00-03:00") -> ParsedMessage:
        return ParsedMessage(datetime.fromisoformat(timestamp), "Alice", text, "user")

    def _import(self, messages: list[ParsedMessage]) -> None:
        with db.connect(self.db_path) as connection:
            db.import_messages(connection, messages, "Family", self.root / "chat.txt")

    def test_future_schema_is_rejected_without_creating_tables(self) -> None:
        future = db.SCHEMA_VERSION + 1
        with closing(sqlite3.connect(self.db_path)) as raw:
            raw.execute(f"PRAGMA user_version = {future}")
        with self.assertRaisesRegex(RuntimeError, "schema.*newer"):
            with db.connect(self.db_path):
                pass
        with closing(sqlite3.connect(self.db_path)) as raw:
            self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], future)
            self.assertEqual(raw.execute("SELECT name FROM sqlite_master").fetchall(), [])

    def test_schema_is_rechecked_after_waiting_for_a_concurrent_upgrade(self) -> None:
        self._import([self._message("keep me")])
        for initial_version in (1, db.SCHEMA_VERSION):
            with self.subTest(initial_version=initial_version):
                read_version = Event()
                original_version = db._schema_version

                def observe_version(connection: sqlite3.Connection) -> int:
                    version = original_version(connection)
                    read_version.set()
                    return version

                def open_database() -> None:
                    with db.connect(self.db_path):
                        pass

                with closing(sqlite3.connect(self.db_path)) as writer:
                    writer.execute(f"PRAGMA user_version = {initial_version}")
                    writer.execute("BEGIN IMMEDIATE")
                    writer.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
                    with patch.object(db, "_schema_version", side_effect=observe_version):
                        with ThreadPoolExecutor(max_workers=1) as executor:
                            pending = executor.submit(open_database)
                            try:
                                self.assertTrue(read_version.wait(3), "opener did not read the schema")
                            finally:
                                writer.commit()
                            with self.assertRaisesRegex(RuntimeError, "schema.*newer"):
                                pending.result(timeout=5)
                    self.assertEqual(
                        writer.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION + 1
                    )
                    self.assertEqual(writer.execute("SELECT text FROM messages").fetchone()[0], "keep me")

    def test_failed_initial_migration_rolls_back_tables_and_version(self) -> None:
        with patch.object(db, "_create_text_index", side_effect=RuntimeError("migration failed")):
            with self.assertRaisesRegex(RuntimeError, "migration failed"):
                db.connect(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as raw:
            self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(raw.execute("SELECT name FROM sqlite_master").fetchall(), [])
        with db.connect(self.db_path) as connection:
            self.assertEqual(db.query_messages(connection), [])

    def test_simultaneous_initialization_and_imports_are_idempotent(self) -> None:
        messages = [self._message("same"), self._message("same")]
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: self._import(messages), range(4)))
        with db.connect(self.db_path) as connection:
            self.assertEqual(len(db.query_messages(connection)), 2)
            self.assertEqual(len(db.search_messages(connection, text_query="same")), 2)

    def test_import_does_not_commit_an_outer_transaction(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "abort outer transaction"):
            with db.connect(self.db_path) as connection:
                db.import_messages(connection, [self._message("roll back")], "Family", "chat.txt")
                raise RuntimeError("abort outer transaction")
        with db.connect(self.db_path) as connection:
            self.assertEqual(db.query_messages(connection), [])
            self.assertEqual(db.search_messages(connection, text_query="roll"), [])

    def test_cli_reports_a_future_schema_without_a_traceback(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as raw:
            raw.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
        output = StringIO()
        with redirect_stderr(output):
            status = cli_main(["query", "--db", str(self.db_path), "--env-file", str(self.root / ".env")])
        self.assertEqual(status, 1)
        self.assertIn("newer", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_punctuation_search_treats_sql_wildcards_as_literal_text(self) -> None:
        self._import([self._message(text) for text in ("ordinary", "100%", "a_b", "a\\b", "_%", "😂%")])
        with db.connect(self.db_path) as connection:
            for query, expected in (
                ("%", ["100%", "_%", "😂%"]),
                ("_", ["a_b", "_%"]),
                ("_%", ["_%"]),
                ("\\", ["a\\b"]),
                ("😂%", ["😂%"]),
            ):
                with self.subTest(query=query):
                    self.assertEqual(
                        [row.text for row in db.search_messages(connection, text_query=query)], expected
                    )

    def test_time_filters_compare_instants_across_offsets(self) -> None:
        self._import([self._message("meeting", "2024-01-02T08:00:00-03:00")])
        toolbox = ConversationToolbox(self.db_path, timezone="UTC")
        for method, arguments in (
            (toolbox.get_messages, {}),
            (toolbox.search_messages, {"text_query": "meeting"}),
            (toolbox.count_messages, {}),
        ):
            with self.subTest(method=method.__name__):
                payload = json.loads(method(from_time="2024-01-02T11:00:00Z", to_time="2024-01-02T11:00:00Z", **arguments))
                self.assertEqual(payload["count"], 1)
                payload = json.loads(method(from_time="2024-01-02T11:00:00.000001Z", **arguments))
                self.assertEqual(payload["count"], 0)

    def test_order_and_context_use_chronological_instants(self) -> None:
        self._import([
            self._message("later", "2024-01-02T09:00:00-03:00"),
            self._message("earlier", "2024-01-02T11:00:00+00:00"),
            self._message("same instant", "2024-01-02T08:00:00-03:00"),
        ])
        with db.connect(self.db_path) as connection:
            rows = db.query_messages(connection)
            self.assertEqual([row.text for row in rows], ["earlier", "same instant", "later"])
            self.assertEqual(
                [row.text for row in db.query_messages(connection, order="desc", limit=2)],
                ["later", "same instant"],
            )
            window = db.get_message_window(connection, message_id=rows[1].id, before=1, after=1)
            self.assertEqual([row.id for row in window], [row.id for row in rows])

    def test_migration_adds_time_indexes_without_rewriting_messages(self) -> None:
        self._import([self._message("meeting")])
        with db.connect(self.db_path) as connection:
            original = db.query_messages(connection)
            connection.execute("DROP INDEX idx_messages_instant")
            connection.execute("DROP INDEX idx_messages_chat_instant")
            connection.execute("PRAGMA user_version = 2")
        with db.connect(self.db_path) as connection:
            self.assertEqual(db.query_messages(connection), original)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
            indexes = {row[1] for row in connection.execute("PRAGMA index_list(messages)")}
            self.assertIn("idx_messages_instant", indexes)
            self.assertIn("idx_messages_chat_instant", indexes)
            self.assertEqual(len(db.search_messages(connection, text_query="meeting")), 1)

    def test_aggregate_ranges_report_first_and_last_instants_in_utc(self) -> None:
        self._import([
            self._message("later", "2024-01-02T09:00:00-03:00"),
            self._message("earlier", "2024-01-02T11:00:00+00:00"),
        ])
        with db.connect(self.db_path) as connection:
            for rows in (db.list_chats(connection), db.list_senders(connection),
                         db.count_messages(connection, group_by="chat")):
                self.assertEqual(rows[0]["first_timestamp"], "2024-01-02T11:00:00+00:00")
                self.assertEqual(rows[0]["last_timestamp"], "2024-01-02T12:00:00+00:00")

    def test_range_validation_handles_the_repeated_dst_hour(self) -> None:
        start, end = build_time_range(
            None, "2024-11-03T01:45:00-04:00", "2024-11-03T01:15:00-05:00", "America/New_York"
        )
        self.assertLess(start.timestamp(), end.timestamp())
        with self.assertRaisesRegex(ValueError, "from must be before"):
            build_time_range(
                None, "2024-11-03T01:15:00-05:00", "2024-11-03T01:45:00-04:00", "America/New_York"
            )

    @unittest.skipUnless(importlib.util.find_spec("prompt_toolkit"), "Chat dependencies are missing")
    def test_terminal_chat_accepts_input_inside_its_running_event_loop(self) -> None:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        with create_pipe_input() as terminal_input:
            session = PromptSession(input=terminal_input, output=DummyOutput())
            app = TerminalChatApp(
                config=TerminalChatConfig(str(self.db_path), "UTC", "unused"),
                agent=None,
                console=Mock(),
                prompt_session=session,
                deps={"Panel": lambda body, **kwargs: body},
            )
            terminal_input.send_text("/exit\n")

            async def run() -> None:
                await asyncio.wait_for(app.run(), timeout=5)

            asyncio.run(run())

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP dependencies are missing")
    def test_mcp_validation_failures_are_protocol_errors(self) -> None:
        from starlette.testclient import TestClient

        logging.disable(logging.INFO)
        self.addCleanup(logging.disable, logging.NOTSET)

        app = create_mcp_http_app(MCPServerConfig(self.db_path, "America/Sao_Paulo", transport="http"))
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            for name, arguments in (
                ("get_messages", {"date": "invalid"}),
                ("search_messages", {"text_query": " "}),
                ("import_conversation", {"file_path": "relative.txt", "chat_name": "Family"}),
            ):
                with self.subTest(name=name):
                    response = client.post(
                        "/mcp",
                        headers={"Accept": "application/json, text/event-stream"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
                    )
                    self.assertEqual(response.status_code, 200)
                    result = response.json()["result"]
                    self.assertTrue(result["isError"])
                    self.assertTrue(result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
