"""Described parameter types shared by the MCP tools and the LangChain agent tools.

The descriptions end up in each tool's JSON schema, which is all a model sees of the
parameters. This module needs pydantic, which only the optional extras install, so it
is imported inside the functions that build tools, after their dependency checks.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field


ImportFilePath = Annotated[
    str,
    Field(description="Absolute path to a WhatsApp .txt export on the server's machine."),
]
ImportChatName = Annotated[
    str | None,
    Field(description="Chat name to store the messages under. Omit to use the server's default chat."),
]
ChatName = Annotated[
    str | None,
    Field(
        description=(
            "Exact chat name as returned by list_chats. Omit to use the server's default "
            "chat; use '*' to include every chat."
        )
    ),
]
Date = Annotated[
    str | None,
    Field(
        description=(
            "Whole local day as YYYY-MM-DD in the server's timezone. "
            "Do not combine with from_time or to_time."
        )
    ),
]
FromTime = Annotated[
    str | None,
    Field(
        description=(
            "Inclusive start as a date and time, not just a time: 'YYYY-MM-DD HH:MM' in the "
            "server's timezone, or ISO 8601 with an offset such as '2026-07-20T12:00:00Z', "
            "which is converted to the server's timezone."
        )
    ),
]
ToTime = Annotated[
    str | None,
    Field(description="Inclusive end, in the same formats as from_time."),
]
Sender = Annotated[
    str | None,
    Field(
        description=(
            "Exact sender name or phone number as returned by list_senders. "
            "Partial names match nothing."
        )
    ),
]
TextQuery = Annotated[
    str,
    Field(
        description=(
            "Text to find inside messages. Case is ignored only for unaccented letters, "
            "and accents must match."
        )
    ),
]
MessageOrder = Annotated[
    Literal["asc", "desc"],
    Field(description="'asc' returns the oldest matches first; 'desc' the most recent first."),
]
MessageLimit = Annotated[
    int,
    Field(
        description=(
            "Maximum messages to return, up to 200. When has_more is true in the result, "
            "more messages matched: raise the limit or narrow the time range."
        )
    ),
]
SenderLimit = Annotated[int, Field(description="Maximum senders to return, up to 500.")]
GroupBy = Annotated[
    Literal["chat", "sender", "date", "hour", "message_type"],
    Field(description="What to count messages by."),
]
BucketLimit = Annotated[int, Field(description="Maximum groups to return, up to 500.")]
MessageId = Annotated[int, Field(description="Message id returned by another tool.")]
ContextBefore = Annotated[int, Field(description="Messages to include before it, 1 to 100.")]
ContextAfter = Annotated[int, Field(description="Messages to include after it, 1 to 100.")]
