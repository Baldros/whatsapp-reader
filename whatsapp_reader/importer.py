from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import re
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "America/Sao_Paulo"
MESSAGE_START_RE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s+"
    r"(?P<time>\d{1,2}:\d{2})\s+-\s+"
    r"(?P<body>.*)$"
)
SENDER_SEPARATOR = ": "


class ParseError(ValueError):
    """Raised when a WhatsApp export line cannot be parsed."""


@dataclass(frozen=True)
class ParsedMessage:
    timestamp: datetime
    sender: str | None
    text: str
    message_type: str


def message_fingerprints(messages: Iterable[ParsedMessage], chat_name: str) -> list[str]:
    """Fingerprint messages of one chat, in export order.

    Identical messages in the same minute are common (several photos, repeated "ok"),
    so each one is numbered by occurrence. Reimporting the same export yields the same
    fingerprints, which keeps imports idempotent without collapsing real duplicates.
    """
    occurrences: Counter[tuple[str, str, str, str]] = Counter()
    fingerprints: list[str] = []
    for message in messages:
        key = (
            message.timestamp.isoformat(timespec="seconds"),
            message.sender or "",
            message.text,
            message.message_type,
        )
        occurrences[key] += 1
        payload = "\x1f".join([chat_name, *key, str(occurrences[key])])
        fingerprints.append(sha256(payload.encode("utf-8")).hexdigest())
    return fingerprints


def parse_export(path: str | Path, timezone_name: str = DEFAULT_TIMEZONE) -> list[ParsedMessage]:
    """Parse a WhatsApp TXT export into structured messages."""
    timezone = ZoneInfo(timezone_name)
    messages: list[ParsedMessage] = []
    current: ParsedMessage | None = None

    with Path(path).open("r", encoding="utf-8-sig", errors="replace") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.rstrip("\r\n")
            match = MESSAGE_START_RE.match(line)
            if match:
                if current is not None:
                    messages.append(current)
                current = _parse_message_start(match, timezone, line_number)
                continue

            if current is None:
                if line.strip():
                    raise ParseError(f"Line {line_number} is not a WhatsApp message start: {line!r}")
                continue

            current = ParsedMessage(
                timestamp=current.timestamp,
                sender=current.sender,
                text=f"{current.text}\n{line}",
                message_type=current.message_type,
            )

    if current is not None:
        messages.append(current)

    return messages


def _parse_message_start(
    match: re.Match[str],
    timezone: ZoneInfo,
    line_number: int,
) -> ParsedMessage:
    timestamp = _parse_timestamp(match.group("date"), match.group("time"), timezone, line_number)
    body = match.group("body")

    if SENDER_SEPARATOR in body:
        sender, text = body.split(SENDER_SEPARATOR, 1)
        return ParsedMessage(
            timestamp=timestamp,
            sender=sender,
            text=text,
            message_type="user",
        )

    return ParsedMessage(
        timestamp=timestamp,
        sender=None,
        text=body,
        message_type="system",
    )


def _parse_timestamp(date_part: str, time_part: str, timezone: ZoneInfo, line_number: int) -> datetime:
    raw_value = f"{date_part}, {time_part}"
    for fmt in ("%m/%d/%y, %H:%M", "%m/%d/%Y, %H:%M"):
        try:
            parsed = datetime.strptime(raw_value, fmt)
            return parsed.replace(tzinfo=timezone)
        except ValueError:
            pass

    raise ParseError(
        f"Line {line_number} has unsupported timestamp {raw_value!r}; expected M/D/YY, HH:mm"
    )
