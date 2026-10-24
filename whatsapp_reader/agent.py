# No `from __future__ import annotations`: the tool signatures in build_langchain_tools
# use parameter types imported inside that function, and LangChain resolves string
# annotations against module globals only.

from datetime import date
import os
from pathlib import Path
from typing import Any

from .conversation_tools import ConversationToolbox


class AgentDependencyError(RuntimeError):
    pass


class AgentConfigurationError(RuntimeError):
    pass


def ask_conversation_agent(
    question: str,
    *,
    db_path: str | Path,
    timezone: str,
    model: str,
    default_chat: str | None = None,
) -> str:
    agent = create_conversation_agent(
        db_path=db_path,
        timezone=timezone,
        model=model,
        default_chat=default_chat,
    )
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    return _extract_final_text(result)


def create_conversation_agent(
    *,
    db_path: str | Path,
    timezone: str,
    model: str,
    default_chat: str | None = None,
    checkpointer: Any | None = None,
    tools: list[Any] | None = None,
) -> Any:
    try:
        from langchain.agents import create_agent
    except ImportError as exc:
        raise AgentDependencyError(
            "LangChain dependencies are missing. Install them with "
            '`python3 -m pip install -e ".[agent]"` inside the virtualenv.'
        ) from exc

    if model.startswith("openai:") and not os.environ.get("OPENAI_API_KEY"):
        raise AgentConfigurationError(
            "OPENAI_API_KEY is required for OpenAI models. Add it to .env or export it."
        )

    if tools is None:
        toolbox = ConversationToolbox(
            db_path=db_path,
            timezone=timezone,
            default_chat=default_chat,
        )
        tools = build_langchain_tools(toolbox)

    return create_agent(
        model=model,
        tools=tools,
        system_prompt=_build_system_prompt(timezone=timezone, default_chat=default_chat),
        checkpointer=checkpointer,
    )


def build_langchain_tools(toolbox: ConversationToolbox) -> list[Any]:
    try:
        from langchain.tools import tool
    except ImportError as exc:
        raise AgentDependencyError(
            "LangChain dependencies are missing. Install them with "
            '`python3 -m pip install -e ".[agent]"` inside the virtualenv.'
        ) from exc

    from .tool_params import (
        BucketLimit,
        ChatName,
        ContextAfter,
        ContextBefore,
        Date,
        FromTime,
        GroupBy,
        MessageId,
        MessageLimit,
        MessageOrder,
        Sender,
        SenderLimit,
        TextQuery,
        ToTime,
    )

    @tool
    def list_chats() -> str:
        """List imported WhatsApp chats with message counts and date ranges."""
        return toolbox.list_chats()

    @tool
    def list_senders(chat_name: ChatName = None, limit: SenderLimit = 100) -> str:
        """List the senders of a chat with message counts, most active first."""
        return toolbox.list_senders(chat_name=chat_name, limit=limit)

    @tool
    def get_messages(
        date: Date = None,
        from_time: FromTime = None,
        to_time: ToTime = None,
        sender: Sender = None,
        chat_name: ChatName = None,
        limit: MessageLimit = 50,
        order: MessageOrder = "asc",
    ) -> str:
        """Fetch messages by day, time range, sender, and chat.

        Oldest first unless order='desc'. Check has_more before treating the result as
        complete.
        """
        return toolbox.get_messages(
            date=date,
            from_time=from_time,
            to_time=to_time,
            sender=sender,
            chat_name=chat_name,
            limit=limit,
            order=order,
        )

    @tool
    def search_messages(
        text_query: TextQuery,
        date: Date = None,
        from_time: FromTime = None,
        to_time: ToTime = None,
        sender: Sender = None,
        chat_name: ChatName = None,
        limit: MessageLimit = 50,
        order: MessageOrder = "asc",
    ) -> str:
        """Search message text, optionally filtered by day, time range, sender, and chat.

        Oldest first unless order='desc'. Check has_more before treating the result as
        complete.
        """
        return toolbox.search_messages(
            text_query=text_query,
            date=date,
            from_time=from_time,
            to_time=to_time,
            sender=sender,
            chat_name=chat_name,
            limit=limit,
            order=order,
        )

    @tool
    def count_messages(
        group_by: GroupBy = "sender",
        date: Date = None,
        from_time: FromTime = None,
        to_time: ToTime = None,
        sender: Sender = None,
        chat_name: ChatName = None,
        limit: BucketLimit = 100,
    ) -> str:
        """Count messages per chat, sender, day, hour, or message type."""
        return toolbox.count_messages(
            group_by=group_by,
            date=date,
            from_time=from_time,
            to_time=to_time,
            sender=sender,
            chat_name=chat_name,
            limit=limit,
        )

    @tool
    def get_context_around(
        message_id: MessageId,
        before: ContextBefore = 10,
        after: ContextAfter = 10,
    ) -> str:
        """Fetch the messages around a message, in the same chat and in order."""
        return toolbox.get_context_around(message_id=message_id, before=before, after=after)

    return [
        list_chats,
        list_senders,
        get_messages,
        search_messages,
        count_messages,
        get_context_around,
    ]


def _build_system_prompt(*, timezone: str, default_chat: str | None) -> str:
    chat_scope = (
        f"Default chat scope: {default_chat!r}. Use chat_name='*' only when the user asks "
        "to compare across all chats."
        if default_chat
        else "No default chat scope is set. Inspect chats first when the target chat is unclear."
    )
    return f"""You are an analyst specialized in imported WhatsApp conversations.

Use the structured SQLite tools before making claims about messages, people, dates, counts,
or timelines. Do not invent message contents. Prefer exact timestamps, sender names, and
message ids as evidence when answering.

Message results are capped by limit and come oldest first. Use order='desc' for the most
recent messages, and when a result has has_more=true, fetch more before concluding.

The conversation export timestamps use timezone {timezone}. Today's date is {date.today().isoformat()}.
{chat_scope}

Answer in the user's language. Be concise, but include enough evidence for the user to verify
the conclusion. If a question is ambiguous, ask a short clarification or state the assumption
you used before querying."""


def _extract_final_text(result: Any) -> str:
    if isinstance(result, dict) and result.get("messages"):
        content = result["messages"][-1].content
        return _content_to_text(content)
    return _content_to_text(result)


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if text:
                    parts.append(str(text))
        if parts:
            return "\n".join(parts)
    return str(content)
