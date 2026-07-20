from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from whatsapp_reader.cli import format_messages_as_json
from whatsapp_reader.config import (
    load_env_file,
    resolve_chat_history_file,
    resolve_chat_memory_db,
    resolve_chat_session,
    resolve_db_path,
    resolve_timezone,
)
from whatsapp_reader.conversation_tools import ConversationToolbox
from whatsapp_reader.db import (
    connect,
    count_messages,
    get_message_window,
    import_messages,
    list_chats,
    list_senders,
    query_messages,
    search_messages,
)
from whatsapp_reader.importer import parse_export
from whatsapp_reader.terminal_chat import (
    _content_to_text,
    _normalize_stream_event,
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


class WhatsAppReaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.export_path = self.root / "chat.txt"
        self.export_path.write_text(SAMPLE_EXPORT, encoding="utf-8")
        self.db_path = self.root / "messages.sqlite3"

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
        self.assertNotEqual(messages[3].fingerprint, messages[4].fingerprint)

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
            "WHATSAPP_READER_HISTORY_FILE=.history\n",
            encoding="utf-8",
        )

        with patch.dict(os.environ, {}, clear=True):
            load_env_file(env_path)

            self.assertEqual(resolve_chat_session(), "deepmesh")
            self.assertEqual(resolve_chat_memory_db(), ".memory.sqlite3")
            self.assertEqual(resolve_chat_history_file(), ".history")

    def test_terminal_chat_stream_helpers(self) -> None:
        mode, payload = _normalize_stream_event(("messages", "payload"))
        summary = _summarize_tool_payload(json.dumps({"count": 2, "messages": []}))
        text = _content_to_text([{"text": "hello"}, {"content": " world"}])

        self.assertEqual((mode, payload), ("messages", "payload"))
        self.assertEqual(summary, "2 mensagens")
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


if __name__ == "__main__":
    unittest.main()
