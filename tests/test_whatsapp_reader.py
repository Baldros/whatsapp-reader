from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import json
import logging
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from whatsapp_reader.cli import format_messages_as_json, main as cli_main
from whatsapp_reader.config import (
    load_env_file,
    resolve_chat_history_file,
    resolve_chat_memory_db,
    resolve_chat_session,
    resolve_db_path,
    resolve_mcp_url,
    resolve_timezone,
    resolve_tools_backend,
)
from whatsapp_reader.conversation_tools import ConversationToolbox
from whatsapp_reader.db import (
    SCHEMA_VERSION,
    connect,
    count_messages,
    get_message_window,
    import_messages,
    list_chats,
    list_senders,
    query_messages,
    search_messages,
)
from whatsapp_reader.importer import message_fingerprints, parse_export
from whatsapp_reader.mcp_server import (
    MCPServerConfig,
    build_mcp_stdio_connection,
    create_mcp_http_app,
    create_mcp_server,
)
from whatsapp_reader.terminal_chat import (
    _content_to_text,
    _mcp_connection,
    _normalize_stream_event,
    _open_tool_backend,
    _select_chat_from_choice,
    _status_toolbar,
    _summarize_tool_payload,
    _upsert_env_value,
    TerminalChatConfig,
)


SAMPLE_EXPORT = """1/2/24, 08:00 - Alice: First message
1/2/24, 08:01 - Bob: List:
- item one
- item two
1/2/24, 08:02 - Alice pinned a message
1/2/24, 08:03 - Alice: Same minute one
1/2/24, 08:03 - Alice: Same minute two
1/3/24, 09:00 - Carol: Next day
"""

DUPLICATES_EXPORT = """1/2/24, 08:00 - Alice: <Media omitted>
1/2/24, 08:00 - Alice: <Media omitted>
1/2/24, 08:00 - Alice: <Media omitted>
1/2/24, 08:01 - Bob: ok
1/2/24, 08:01 - Bob: ok
1/2/24, 08:02 - Bob: Done
"""


class WhatsAppReaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.export_path = self.root / "chat.txt"
        self.export_path.write_text(SAMPLE_EXPORT, encoding="utf-8")
        self.db_path = self.root / "messages.sqlite3"

    def _write_export(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_parse_simple_multiline_system_and_same_minute_messages(self) -> None:
        messages = parse_export(self.export_path)

        self.assertEqual(len(messages), 6)
        self.assertEqual(messages[0].sender, "Alice")
        self.assertEqual(messages[0].text, "First message")
        self.assertEqual(messages[1].text, "List:\n- item one\n- item two")
        self.assertIsNone(messages[2].sender)
        self.assertEqual(messages[2].message_type, "system")
        self.assertEqual(messages[2].text, "Alice pinned a message")
        self.assertEqual(messages[3].timestamp, messages[4].timestamp)
        fingerprints = message_fingerprints(messages, "Family")
        self.assertNotEqual(fingerprints[3], fingerprints[4])

    def test_fingerprints_number_identical_messages_and_depend_on_chat(self) -> None:
        messages = parse_export(self._write_export("duplicates.txt", DUPLICATES_EXPORT))
        family = message_fingerprints(messages, "Family")

        self.assertEqual(messages[0], messages[1])
        self.assertEqual(len(set(family)), len(messages))
        self.assertEqual(family, message_fingerprints(messages, "Family"))
        self.assertTrue(set(family).isdisjoint(message_fingerprints(messages, "Work")))

    def test_reimport_skips_existing_messages(self) -> None:
        messages = parse_export(self.export_path)
        with connect(self.db_path) as connection:
            first = import_messages(connection, messages, "Family", self.export_path)
            second = import_messages(connection, messages, "Family", self.export_path)
            stored = query_messages(connection)

        self.assertEqual(first.parsed, 6)
        self.assertEqual(first.inserted, 6)
        self.assertEqual(first.skipped, 0)
        self.assertEqual(second.parsed, 6)
        self.assertEqual(second.inserted, 0)
        self.assertEqual(second.skipped, 6)
        self.assertEqual(len(stored), 6)

    def test_identical_messages_in_same_minute_are_all_kept(self) -> None:
        export_path = self._write_export("duplicates.txt", DUPLICATES_EXPORT)
        messages = parse_export(export_path)
        with connect(self.db_path) as connection:
            first = import_messages(connection, messages, "Family", export_path)
            second = import_messages(connection, messages, "Family", export_path)
            stored = query_messages(connection)

        self.assertEqual((first.parsed, first.inserted, first.skipped), (6, 6, 0))
        self.assertEqual((second.parsed, second.inserted, second.skipped), (6, 0, 6))
        self.assertEqual(
            [message.text for message in stored],
            ["<Media omitted>"] * 3 + ["ok"] * 2 + ["Done"],
        )

    def test_same_export_imports_into_each_chat(self) -> None:
        messages = parse_export(self.export_path)
        with connect(self.db_path) as connection:
            family = import_messages(connection, messages, "Family", self.export_path)
            work = import_messages(connection, messages, "Work", self.export_path)
            chats = list_chats(connection)

        self.assertEqual(family.inserted, 6)
        self.assertEqual(work.inserted, 6)
        self.assertEqual(
            {chat["chat_name"]: chat["message_count"] for chat in chats},
            {"Family": 6, "Work": 6},
        )

    def test_migration_lets_reimport_recover_collapsed_messages(self) -> None:
        export_path = self._write_export("duplicates.txt", DUPLICATES_EXPORT)
        messages = parse_export(export_path)
        collapsed = list(dict.fromkeys(messages))

        # Simulate a database written before schema version 1: identical messages
        # collapsed into one row and fingerprints computed the old way.
        with connect(self.db_path) as connection:
            import_messages(connection, collapsed, "Family", export_path)
            connection.execute("UPDATE messages SET fingerprint = 'legacy-' || id")
            connection.execute("PRAGMA user_version = 0")

        with connect(self.db_path) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            recovered = import_messages(connection, messages, "Family", export_path)
            again = import_messages(connection, messages, "Family", export_path)
            stored = query_messages(connection)

        self.assertEqual(len(collapsed), 3)
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertEqual(recovered.inserted, 3)
        self.assertEqual(again.inserted, 0)
        self.assertEqual(
            [message.text for message in stored],
            ["<Media omitted>"] * 3 + ["ok"] * 2 + ["Done"],
        )

    def test_connection_context_manager_closes_database(self) -> None:
        with connect(self.db_path) as connection:
            self.assertEqual(query_messages(connection), [])

        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_query_by_date_sender_interval_and_combined_filters(self) -> None:
        messages = parse_export(self.export_path)
        with connect(self.db_path) as connection:
            import_messages(connection, messages, "Family", self.export_path)
            day = query_messages(
                connection,
                from_timestamp=messages[0].timestamp,
                to_timestamp=messages[4].timestamp,
            )
            alice = query_messages(connection, sender="Alice")
            interval = query_messages(
                connection,
                from_timestamp=messages[3].timestamp,
                to_timestamp=messages[4].timestamp,
            )
            alice_interval = query_messages(
                connection,
                sender="Alice",
                from_timestamp=messages[3].timestamp,
                to_timestamp=messages[4].timestamp,
            )

        self.assertEqual(len(day), 5)
        self.assertEqual([message.sender for message in alice], ["Alice", "Alice", "Alice"])
        self.assertEqual([message.text for message in interval], ["Same minute one", "Same minute two"])
        self.assertEqual([message.text for message in alice_interval], ["Same minute one", "Same minute two"])

    def test_export_context_json_is_clean(self) -> None:
        messages = parse_export(self.export_path)
        with connect(self.db_path) as connection:
            import_messages(connection, messages, "Family", self.export_path)
            stored = query_messages(connection, sender="Carol")

        payload = json.loads(format_messages_as_json(stored))

        self.assertEqual(
            payload,
            [
                {
                    "chat_name": "Family",
                    "timestamp": "2024-01-03T09:00:00-03:00",
                    "sender": "Carol",
                    "text": "Next day",
                    "message_type": "user",
                }
            ],
        )

    def test_structured_database_tools(self) -> None:
        messages = parse_export(self.export_path)
        with connect(self.db_path) as connection:
            import_messages(connection, messages, "Family", self.export_path)
            stored = query_messages(connection)
            chats = list_chats(connection)
            senders = list_senders(connection, chat_name="Family")
            search = search_messages(connection, text_query="Same minute", sender="Alice")
            counts = count_messages(connection, group_by="sender", chat_name="Family")
            window = get_message_window(
                connection,
                message_id=stored[3].id,
                before=1,
                after=1,
            )

        self.assertEqual(chats[0]["chat_name"], "Family")
        self.assertEqual(chats[0]["message_count"], 6)
        self.assertEqual(senders[0]["sender"], "Alice")
        self.assertEqual(senders[0]["message_count"], 3)
        self.assertEqual([message.text for message in search], ["Same minute one", "Same minute two"])
        self.assertEqual(counts[0]["bucket"], "Alice")
        self.assertEqual([message.text for message in window], [
            "Alice pinned a message",
            "Same minute one",
            "Same minute two",
        ])

    def test_conversation_toolbox_outputs_json(self) -> None:
        messages = parse_export(self.export_path)
        with connect(self.db_path) as connection:
            import_messages(connection, messages, "Family", self.export_path)

        toolbox = ConversationToolbox(self.db_path, default_chat="Family")
        search_payload = json.loads(toolbox.search_messages("List", limit=5))
        count_payload = json.loads(toolbox.count_messages(group_by="date"))

        self.assertEqual(search_payload["count"], 1)
        self.assertEqual(search_payload["messages"][0]["text"], "List:\n- item one\n- item two")
        self.assertEqual(count_payload["rows"][0]["bucket"], "2024-01-02")
        self.assertEqual(count_payload["rows"][0]["message_count"], 5)

    def test_conversation_toolbox_imports_export_idempotently(self) -> None:
        toolbox = ConversationToolbox(self.db_path, default_chat="Family")

        first = json.loads(toolbox.import_conversation(str(self.export_path)))
        second = json.loads(toolbox.import_conversation(str(self.export_path)))

        self.assertEqual(first["chat_name"], "Family")
        self.assertEqual(first["parsed"], 6)
        self.assertEqual(first["inserted"], 6)
        self.assertEqual(first["skipped"], 0)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["skipped"], 6)

    def test_conversation_toolbox_import_validates_input(self) -> None:
        toolbox = ConversationToolbox(self.db_path)

        relative = json.loads(toolbox.import_conversation("chat.txt", "Family"))
        wrong_extension = json.loads(
            toolbox.import_conversation(str(self.root / "chat.json"), "Family")
        )
        missing_chat = json.loads(toolbox.import_conversation(str(self.export_path)))

        self.assertIn("absolute path", relative["error"])
        self.assertIn(".txt", wrong_extension["error"])
        self.assertIn("chat_name", missing_chat["error"])

    def test_cli_import_remains_available(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = cli_main(
                [
                    "import",
                    str(self.export_path),
                    "--chat",
                    "Family",
                    "--db",
                    str(self.db_path),
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("parsed=6 inserted=6 skipped=0", output.getvalue())
        with connect(self.db_path) as connection:
            self.assertEqual(len(query_messages(connection, chat_name="Family")), 6)

    def test_env_file_loading_does_not_override_existing_values(self) -> None:
        env_path = self.root / ".env"
        env_path.write_text(
            "WHATSAPP_READER_DB=from-file.sqlite3\n"
            "WHATSAPP_READER_TIMEZONE=\"America/Sao_Paulo\"\n",
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"WHATSAPP_READER_DB": "from-env.sqlite3"}, clear=True):
            load_env_file(env_path)

            self.assertEqual(resolve_db_path(), "from-env.sqlite3")
            self.assertEqual(resolve_timezone(), "America/Sao_Paulo")

    def test_chat_env_resolution(self) -> None:
        env_path = self.root / ".env"
        env_path.write_text(
            "WHATSAPP_READER_SESSION=deepmesh\n"
            "WHATSAPP_READER_MEMORY_DB=.memory.sqlite3\n"
            "WHATSAPP_READER_HISTORY_FILE=.history\n"
            "WHATSAPP_READER_TOOLS_BACKEND=direct\n"
            "WHATSAPP_READER_MCP_URL=http://127.0.0.1:9999/mcp\n",
            encoding="utf-8",
        )

        with patch.dict(os.environ, {}, clear=True):
            load_env_file(env_path)

            self.assertEqual(resolve_chat_session(), "deepmesh")
            self.assertEqual(resolve_chat_memory_db(), ".memory.sqlite3")
            self.assertEqual(resolve_chat_history_file(), ".history")
            self.assertEqual(resolve_tools_backend(), "direct")
            self.assertEqual(resolve_mcp_url(), "http://127.0.0.1:9999/mcp")

    def test_mcp_stdio_connection_builder(self) -> None:
        connection = build_mcp_stdio_connection(
            db_path=self.db_path,
            timezone="America/Sao_Paulo",
            chat_name="Family",
            env_file=None,
            command="python",
            cwd=self.root,
        )

        self.assertEqual(connection["transport"], "stdio")
        self.assertEqual(connection["command"], "python")
        self.assertIn("whatsapp_reader.mcp_server", connection["args"])
        self.assertIn("--chat", connection["args"])
        self.assertEqual(connection["cwd"], str(self.root))

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP dependencies are missing")
    def test_mcp_server_exposes_conversation_tools(self) -> None:
        messages = parse_export(self.export_path)
        with connect(self.db_path) as connection:
            import_messages(connection, messages, "Family", self.export_path)

        async def run() -> tuple[list[str], dict[str, object], dict[str, object]]:
            server = create_mcp_server(
                MCPServerConfig(
                    db_path=self.db_path,
                    timezone="America/Sao_Paulo",
                    chat_name="Family",
                )
            )
            tools = await server.list_tools()
            _content, structured = await server.call_tool("list_chats", {})
            _import_content, import_payload = await server.call_tool(
                "import_conversation",
                {"file_path": str(self.export_path)},
            )
            return [tool.name for tool in tools], structured, import_payload

        tool_names, payload, import_payload = asyncio.run(run())

        self.assertIn("import_conversation", tool_names)
        self.assertIn("list_chats", tool_names)
        self.assertIn("search_messages", tool_names)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["chats"][0]["chat_name"], "Family")
        self.assertEqual(import_payload["inserted"], 0)
        self.assertEqual(import_payload["skipped"], 6)

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP dependencies are missing")
    def test_mcp_http_endpoint_only_accepts_post(self) -> None:
        from starlette.testclient import TestClient

        # Keep the test client's per-request INFO lines out of the test output.
        logging.disable(logging.INFO)
        self.addCleanup(logging.disable, logging.NOTSET)
        app = create_mcp_http_app(
            MCPServerConfig(db_path=self.db_path, timezone="America/Sao_Paulo", transport="http")
        )
        list_tools = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

        # The SDK only accepts Host headers of the 127.0.0.1 server it was configured for.
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            sse_get = client.get("/mcp", headers={"Accept": "text/event-stream"})
            delete = client.delete("/mcp")
            tools = client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json=list_tools,
            )
            discovery = client.get("/.well-known/oauth-protected-resource")

        self.assertEqual(sse_get.status_code, 405)
        self.assertEqual(sse_get.headers["allow"], "POST")
        self.assertEqual(delete.status_code, 405)
        self.assertEqual(tools.status_code, 200)
        self.assertIn("list_chats", [tool["name"] for tool in tools.json()["result"]["tools"]])
        self.assertEqual(discovery.status_code, 404)

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP dependencies are missing")
    def test_mcp_sdk_logs_stay_at_warning_unless_debugging(self) -> None:
        sdk_logger = logging.getLogger("mcp")
        self.addCleanup(sdk_logger.setLevel, sdk_logger.level)
        logging.disable(logging.DEBUG)
        self.addCleanup(logging.disable, logging.NOTSET)

        levels = {}
        for level in ("INFO", "DEBUG", "ERROR"):
            create_mcp_server(
                MCPServerConfig(db_path=self.db_path, timezone="America/Sao_Paulo", log_level=level)
            )
            levels[level] = sdk_logger.level

        self.assertEqual(
            levels,
            {"INFO": logging.WARNING, "DEBUG": logging.DEBUG, "ERROR": logging.ERROR},
        )

    def test_terminal_chat_uses_stdio_unless_mcp_url_is_set(self) -> None:
        config = TerminalChatConfig(
            db_path=str(self.db_path),
            timezone="America/Sao_Paulo",
            model="openai:gpt-5.6-terra",
            chat_name="Family",
        )
        stdio = _mcp_connection(config)
        config.mcp_url = "http://127.0.0.1:8765/mcp"
        http = _mcp_connection(config)

        self.assertEqual(stdio["transport"], "stdio")
        self.assertIn(str(self.db_path), stdio["args"])
        self.assertIn("Family", stdio["args"])
        self.assertEqual(http, {"transport": "http", "url": "http://127.0.0.1:8765/mcp"})

    @unittest.skipUnless(
        importlib.util.find_spec("langchain_mcp_adapters"),
        "MCP chat dependencies are missing",
    )
    def test_terminal_chat_loads_tools_from_private_stdio_server(self) -> None:
        messages = parse_export(self.export_path)
        with connect(self.db_path) as connection:
            import_messages(connection, messages, "Family", self.export_path)
        config = TerminalChatConfig(
            db_path=str(self.db_path),
            timezone="America/Sao_Paulo",
            model="openai:gpt-5.6-terra",
            chat_name="Family",
            env_file=str(self.root / ".env"),
        )

        async def run() -> tuple[list[str], object]:
            async with _open_tool_backend(config) as tools:
                list_chats_tool = next(tool for tool in tools if tool.name == "list_chats")
                return [tool.name for tool in tools], await list_chats_tool.ainvoke({})

        tool_names, chats = asyncio.run(run())

        self.assertIn("get_messages", tool_names)
        self.assertEqual(json.loads(_content_to_text(chats))["chats"][0]["chat_name"], "Family")

    def test_terminal_chat_stream_helpers(self) -> None:
        mode, payload = _normalize_stream_event(("messages", "payload"))
        summary = _summarize_tool_payload(json.dumps({"count": 2, "messages": []}))
        mcp_summary = _summarize_tool_payload(
            [{"type": "text", "text": json.dumps({"count": 1, "chats": []})}]
        )
        text = _content_to_text([{"text": "hello"}, {"content": " world"}])

        self.assertEqual((mode, payload), ("messages", "payload"))
        self.assertEqual(summary, "2 mensagens")
        self.assertEqual(mcp_summary, "1 chats")
        self.assertEqual(text, "hello world")

    def test_terminal_chat_onboarding_helpers(self) -> None:
        chats = [{"chat_name": "A&B"}, {"chat_name": "Other"}]
        config = TerminalChatConfig(
            db_path=str(self.db_path),
            timezone="America/Sao_Paulo",
            model="openai:gpt-5.6-terra",
            chat_name="A&B",
            session_id="dev<1>",
        )
        env_path = self.root / ".env"
        env_path.write_text("WHATSAPP_READER_SESSION=old\n", encoding="utf-8")

        _upsert_env_value(str(env_path), "OPENAI_API_KEY", "sk-test")
        _upsert_env_value(str(env_path), "WHATSAPP_READER_SESSION", "new")

        self.assertEqual(_select_chat_from_choice(chats, ""), None)
        self.assertEqual(_select_chat_from_choice(chats, "1"), "A&B")
        self.assertIn("OPENAI_API_KEY=sk-test", env_path.read_text(encoding="utf-8"))
        self.assertIn("WHATSAPP_READER_SESSION=new", env_path.read_text(encoding="utf-8"))
        self.assertIn("A&amp;B", _status_toolbar(config))
        self.assertIn("dev&lt;1&gt;", _status_toolbar(config))
        self.assertIn("<b>tools</b> mcp", _status_toolbar(config))


if __name__ == "__main__":
    unittest.main()
