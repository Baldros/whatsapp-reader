from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from whatsapp_reader.cli import format_messages_as_json
from whatsapp_reader.db import connect, import_messages, query_messages
from whatsapp_reader.importer import parse_export


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


if __name__ == "__main__":
    unittest.main()
