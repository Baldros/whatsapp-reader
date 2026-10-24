from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import getpass
from html import escape
import os
from pathlib import Path
import json
import shlex
import sys
import uuid
from typing import Any, AsyncIterator

from .agent import create_conversation_agent
from .config import (
    DEFAULT_CHAT_HISTORY_FILE,
    DEFAULT_CHAT_MEMORY_DB,
    DEFAULT_CHAT_SESSION,
    DEFAULT_ENV_FILE,
)
from .db import connect, list_chats as list_chat_rows


class TerminalChatDependencyError(RuntimeError):
    pass


@dataclass
class TerminalChatConfig:
    db_path: str
    timezone: str
    model: str
    chat_name: str | None = None
    session_id: str = DEFAULT_CHAT_SESSION
    memory_db: str = DEFAULT_CHAT_MEMORY_DB
    history_file: str = DEFAULT_CHAT_HISTORY_FILE
    tools_backend: str = "mcp"
    mcp_url: str | None = None
    stream: bool = True
    onboarding: bool = True
    env_file: str = DEFAULT_ENV_FILE


class TerminalChatApp:
    def __init__(
        self,
        *,
        config: TerminalChatConfig,
        agent: Any,
        console: Any,
        prompt_session: Any,
        deps: dict[str, Any],
    ) -> None:
        self.config = config
        self.agent = agent
        self.console = console
        self.prompt_session = prompt_session
        self.deps = deps

    async def run(self, initial_message: str | None = None) -> None:
        self._render_header()
        self._render_help_hint()

        if initial_message:
            await self._send_message(initial_message)

        while True:
            try:
                user_input = (await self.prompt_session.prompt_async("voce > ")).strip()
            except KeyboardInterrupt:
                self.console.print("\n[dim]Use /exit para sair.[/]")
                continue
            except EOFError:
                self.console.print()
                return

            if not user_input:
                continue
            if user_input.startswith("/"):
                if await self._handle_command(user_input):
                    return
                continue

            await self._send_message(user_input)

    async def _send_message(self, user_input: str) -> None:
        if self.config.stream:
            await self._stream_message(user_input)
            return

        result = await self.agent.ainvoke(
            {"messages": [{"role": "user", "content": user_input}]},
            config=self._runtime_config(),
        )
        answer = _extract_final_text(result)
        self.console.print("[bold green]agent >[/]")
        self.console.print(self.deps["Markdown"](answer))

    async def _stream_message(self, user_input: str) -> None:
        started_answer = False
        emitted_text = False
        seen_tool_call_ids: set[str] = set()

        try:
            events = self.agent.astream(
                {"messages": [{"role": "user", "content": user_input}]},
                config=self._runtime_config(),
                stream_mode=["messages", "updates"],
            )
            async for event in events:
                mode, payload = _normalize_stream_event(event)
                if mode == "messages":
                    text = _extract_chunk_text(payload)
                    if text:
                        if not started_answer:
                            self.console.print("[bold green]agent > [/]", end="")
                            started_answer = True
                        self.console.print(text, end="", highlight=False, soft_wrap=True)
                        emitted_text = True
                    continue

                if mode == "updates":
                    self._render_update(payload, seen_tool_call_ids)

        except KeyboardInterrupt:
            self.console.print("\n[yellow]Resposta interrompida.[/]")
            return

        if emitted_text:
            self.console.print("\n")
        else:
            self.console.print("[dim]Nenhuma resposta textual foi emitida.[/]\n")

    def _render_update(self, payload: Any, seen_tool_call_ids: set[str]) -> None:
        if not isinstance(payload, dict):
            return

        model_update = payload.get("model")
        if isinstance(model_update, dict):
            for message in model_update.get("messages", []):
                for tool_call in getattr(message, "tool_calls", []) or []:
                    call_id = str(tool_call.get("id") or tool_call.get("name") or tool_call)
                    if call_id in seen_tool_call_ids:
                        continue
                    seen_tool_call_ids.add(call_id)
                    name = tool_call.get("name", "tool")
                    args = _shorten(json.dumps(tool_call.get("args", {}), ensure_ascii=False), 180)
                    self.console.print(f"[dim]-> {name} {args}[/]")

        tools_update = payload.get("tools")
        if isinstance(tools_update, dict):
            for message in tools_update.get("messages", []):
                name = getattr(message, "name", None) or "tool"
                summary = _summarize_tool_payload(getattr(message, "content", ""))
                self.console.print(f"[dim]ok {name}: {summary}[/]")

    async def _handle_command(self, command_line: str) -> bool:
        try:
            parts = shlex.split(command_line)
        except ValueError as exc:
            self.console.print(f"[red]Comando invalido:[/] {exc}")
            return False

        command = parts[0].lower()
        args = parts[1:]

        if command in {"/exit", "/quit", "/q"}:
            self.console.print("[dim]Sessao encerrada.[/]")
            return True
        if command == "/help":
            self._render_commands()
            return False
        if command == "/clear":
            self.console.clear()
            self._render_header()
            return False
        if command == "/session":
            await self._render_session()
            return False
        if command == "/config":
            self._render_header()
            return False
        if command == "/chats":
            self._render_chats()
            return False
        if command == "/tools":
            self._render_tools()
            return False
        if command == "/new":
            self.config.session_id = args[0] if args else f"session-{uuid.uuid4().hex[:8]}"
            self.console.print(f"[green]Sessao ativa:[/] {self.config.session_id}")
            return False
        if command == "/stream":
            if args and args[0].lower() in {"on", "1", "true"}:
                self.config.stream = True
            elif args and args[0].lower() in {"off", "0", "false"}:
                self.config.stream = False
            else:
                self.config.stream = not self.config.stream
            state = "on" if self.config.stream else "off"
            self.console.print(f"[green]stream:[/] {state}")
            return False

        self.console.print("[red]Comando desconhecido.[/] Use /help.")
        return False

    def _runtime_config(self) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": self.config.session_id}}

    def _render_header(self) -> None:
        Panel = self.deps["Panel"]
        body = (
            f"[bold]WhatsApp Reader Chat[/]\n"
            f"modelo: [cyan]{self.config.model}[/]\n"
            f"db: [cyan]{self.config.db_path}[/]\n"
            f"chat: [cyan]{self.config.chat_name or '*'}[/]\n"
            f"sessao: [cyan]{self.config.session_id}[/]\n"
            f"memoria: [cyan]{self.config.memory_db}[/]\n"
            f"tools: [cyan]{self.config.tools_backend}[/]\n"
            f"mcp: [cyan]{self.config.mcp_url or 'stdio'}[/]"
        )
        self.console.print(Panel(body, border_style="cyan"))

    def _render_help_hint(self) -> None:
        self.console.print("[dim]Digite /help para comandos, /exit para sair.[/]\n")

    def _render_commands(self) -> None:
        Table = self.deps["Table"]
        table = Table(title="Comandos")
        table.add_column("Comando", style="cyan", no_wrap=True)
        table.add_column("Acao")
        table.add_row("/help", "Mostra esta lista.")
        table.add_row("/session", "Mostra configuracao e tamanho aproximado da memoria atual.")
        table.add_row("/config", "Mostra modelo, banco, chat e arquivos locais.")
        table.add_row("/chats", "Lista chats importados no banco.")
        table.add_row("/new [nome]", "Troca para uma nova sessao de memoria.")
        table.add_row("/stream", "Alterna streaming on/off.")
        table.add_row("/stream on|off", "Define streaming explicitamente.")
        table.add_row("/tools", "Mostra as ferramentas estruturadas disponiveis.")
        table.add_row("/clear", "Limpa a tela.")
        table.add_row("/exit", "Sai do chat.")
        self.console.print(table)

    def _render_tools(self) -> None:
        Table = self.deps["Table"]
        title = "Ferramentas MCP" if self.config.tools_backend == "mcp" else "Ferramentas do Agent"
        table = Table(title=title)
        table.add_column("Ferramenta", style="cyan", no_wrap=True)
        table.add_column("Uso")
        table.add_row("list_chats", "Lista chats importados e intervalos.")
        table.add_row("list_senders", "Lista participantes com contagens.")
        table.add_row("get_messages", "Busca mensagens por data, intervalo, pessoa e chat.")
        table.add_row("search_messages", "Busca texto nas mensagens com filtros.")
        table.add_row("count_messages", "Agrega por chat, pessoa, data, hora ou tipo.")
        table.add_row("get_context_around", "Busca mensagens vizinhas pelo message_id.")
        self.console.print(table)

    def _render_chats(self) -> None:
        try:
            chats = _load_available_chats(self.config.db_path)
        except RuntimeError as exc:
            self.console.print(f"[red]{exc}[/]")
            return
        _render_chat_table(chats, self.console, self.deps)

    async def _render_session(self) -> None:
        message_count = "?"
        try:
            if hasattr(self.agent, "aget_state"):
                state = await self.agent.aget_state(self._runtime_config())
            else:
                state = self.agent.get_state(self._runtime_config())
            messages = state.values.get("messages", []) if state.values else []
            message_count = str(len(messages))
        except Exception:
            pass

        self.console.print(f"[bold]sessao:[/] {self.config.session_id}")
        self.console.print(f"[bold]mensagens na memoria:[/] {message_count}")
        self.console.print(f"[bold]stream:[/] {'on' if self.config.stream else 'off'}")
        self.console.print(f"[bold]chat scope:[/] {self.config.chat_name or '*'}")
        self.console.print(f"[bold]tools:[/] {self.config.tools_backend}")
        self.console.print(f"[bold]mcp:[/] {self.config.mcp_url or 'stdio'}")


def run_terminal_chat(
    *,
    config: TerminalChatConfig,
    initial_message: str | None = None,
) -> None:
    asyncio.run(_run_terminal_chat_async(config=config, initial_message=initial_message))


async def _run_terminal_chat_async(
    *,
    config: TerminalChatConfig,
    initial_message: str | None = None,
) -> None:
    deps = _load_terminal_dependencies()
    console = deps["Console"]()
    config.db_path = _expand_local_path(config.db_path)
    config.history_file = _expand_local_path(config.history_file)
    config.memory_db = _expand_local_path(config.memory_db)
    config.env_file = _expand_local_path(config.env_file)
    config.tools_backend = config.tools_backend.lower()
    if config.tools_backend not in {"mcp", "direct"}:
        raise RuntimeError(f"unsupported tools backend {config.tools_backend!r}")

    if config.onboarding:
        _run_onboarding(config, console, deps)

    _ensure_parent_directory(config.history_file)
    prompt_session = deps["PromptSession"](
        history=deps["FileHistory"](config.history_file),
        bottom_toolbar=lambda: deps["HTML"](_status_toolbar(config)),
    )

    async with (
        _open_checkpointer(config.memory_db) as checkpointer,
        _open_tool_backend(config) as tools,
    ):
        agent = create_conversation_agent(
            db_path=config.db_path,
            timezone=config.timezone,
            model=config.model,
            default_chat=config.chat_name,
            checkpointer=checkpointer,
            tools=tools,
        )
        app = TerminalChatApp(
            config=config,
            agent=agent,
            console=console,
            prompt_session=prompt_session,
            deps=deps,
        )
        await app.run(initial_message=initial_message)


def _load_terminal_dependencies() -> dict[str, Any]:
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.history import FileHistory
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.panel import Panel
        from rich.table import Table
    except ImportError as exc:
        raise TerminalChatDependencyError(
            "Terminal chat dependencies are missing. Install them with "
            '`python3 -m pip install -e ".[chat]"` inside the virtualenv.'
        ) from exc

    return {
        "Console": Console,
        "FileHistory": FileHistory,
        "HTML": HTML,
        "Markdown": Markdown,
        "Panel": Panel,
        "PromptSession": PromptSession,
        "Table": Table,
    }


def _run_onboarding(config: TerminalChatConfig, console: Any, deps: dict[str, Any]) -> None:
    chats = _load_available_chats(config.db_path)
    if config.chat_name is None:
        config.chat_name = _choose_chat_scope(chats, console, deps)

    if config.model.startswith("openai:") and not os.environ.get("OPENAI_API_KEY"):
        _prompt_for_openai_key(config.env_file, console)


def _load_available_chats(db_path: str) -> list[dict[str, object]]:
    db_path = _expand_local_path(db_path)
    if not Path(db_path).exists():
        raise RuntimeError(
            f"Database not found: {db_path}. Import a WhatsApp export first with "
            '`whatsapp-reader import "chat.txt" --chat "Chat name"`.'
        )

    with connect(db_path) as connection:
        chats = list_chat_rows(connection)

    if not chats:
        raise RuntimeError(
            f"No imported messages found in {db_path}. Import a WhatsApp export first with "
            '`whatsapp-reader import "chat.txt" --chat "Chat name"`.'
        )
    return chats


def _choose_chat_scope(chats: list[dict[str, object]], console: Any, deps: dict[str, Any]) -> str | None:
    if len(chats) == 1:
        chat_name = str(chats[0]["chat_name"])
        console.print(f"[green]Chat selecionado:[/] {chat_name}")
        return chat_name

    _render_chat_table(chats, console, deps)
    console.print("[dim]Enter usa todos os chats. Digite o numero para fixar um chat.[/]")

    if not sys.stdin.isatty():
        return None

    while True:
        choice = console.input("chat > ").strip()
        selected = _select_chat_from_choice(chats, choice)
        if selected is not _INVALID_CHOICE:
            if selected is None:
                console.print("[green]Escopo:[/] todos os chats")
            else:
                console.print(f"[green]Chat selecionado:[/] {selected}")
            return selected
        console.print("[red]Escolha invalida.[/] Use um numero da tabela ou Enter.")


def _render_chat_table(chats: list[dict[str, object]], console: Any, deps: dict[str, Any]) -> None:
    Table = deps["Table"]
    table = Table(title="Chats importados")
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("Chat")
    table.add_column("Mensagens", justify="right")
    table.add_column("Primeira")
    table.add_column("Ultima")
    for index, chat in enumerate(chats, start=1):
        table.add_row(
            str(index),
            str(chat["chat_name"]),
            str(chat["message_count"]),
            str(chat["first_timestamp"])[:10],
            str(chat["last_timestamp"])[:10],
        )
    console.print(table)


_INVALID_CHOICE = object()


def _select_chat_from_choice(
    chats: list[dict[str, object]],
    choice: str,
) -> str | None | object:
    if not choice:
        return None
    if not choice.isdigit():
        return _INVALID_CHOICE
    index = int(choice)
    if index < 1 or index > len(chats):
        return _INVALID_CHOICE
    return str(chats[index - 1]["chat_name"])


def _prompt_for_openai_key(env_file: str, console: Any) -> None:
    console.print("[yellow]OPENAI_API_KEY nao encontrada.[/]")
    if not sys.stdin.isatty():
        raise RuntimeError("OPENAI_API_KEY is required. Add it to .env or export it.")

    key = getpass.getpass("Cole sua OPENAI_API_KEY para esta sessao: ").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is required. Add it to .env or export it.")

    os.environ["OPENAI_API_KEY"] = key
    save = console.input(f"Salvar em {env_file}? [s/N] ").strip().lower()
    if save in {"s", "sim", "y", "yes"}:
        _upsert_env_value(env_file, "OPENAI_API_KEY", key)
        console.print(f"[green]Chave salva em {env_file}.[/]")
    else:
        console.print("[dim]Chave usada apenas nesta execucao.[/]")


def _upsert_env_value(path_value: str, key: str, value: str) -> None:
    path = Path(path_value)
    _ensure_parent_directory(path_value)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    replacement = f"{key}={value}"
    replaced = False
    output: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            output.append(replacement)
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(replacement)
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def _status_toolbar(config: TerminalChatConfig) -> str:
    return (
        f"<b>session</b> {escape(config.session_id)} | "
        f"<b>chat</b> {escape(config.chat_name or '*')} | "
        f"<b>tools</b> {escape(config.tools_backend)} | "
        f"<b>stream</b> {'on' if config.stream else 'off'} | "
        "/help /exit"
    )


@asynccontextmanager
async def _open_tool_backend(config: TerminalChatConfig) -> AsyncIterator[list[Any] | None]:
    if config.tools_backend == "direct":
        yield None
        return
    if config.tools_backend != "mcp":
        raise RuntimeError(f"unsupported tools backend {config.tools_backend!r}")

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from langchain_mcp_adapters.tools import load_mcp_tools
    except ImportError as exc:
        raise TerminalChatDependencyError(
            "MCP chat dependencies are missing. Install them with "
            '`python3 -m pip install -e ".[chat]"` inside the virtualenv.'
        ) from exc

    from .mcp_server import MCP_CONNECTION_NAME

    client = MultiServerMCPClient({MCP_CONNECTION_NAME: _mcp_connection(config)})
    async with client.session(MCP_CONNECTION_NAME) as session:
        tools = await load_mcp_tools(session, server_name=MCP_CONNECTION_NAME)
        yield tools


def _mcp_connection(config: TerminalChatConfig) -> dict[str, object]:
    if config.mcp_url:
        return {"transport": "http", "url": config.mcp_url}

    from .mcp_server import build_mcp_stdio_connection

    # A private server over stdio: no port to pick or leave open, it exits with the
    # chat, and its warnings and errors reach this terminal through stderr.
    return build_mcp_stdio_connection(
        db_path=config.db_path,
        timezone=config.timezone,
        chat_name=config.chat_name,
        env_file=config.env_file,
        cwd=Path.cwd(),
    )


@asynccontextmanager
async def _open_checkpointer(memory_db: str) -> AsyncIterator[Any]:
    if memory_db in {"", ":memory:", "memory", "none"}:
        try:
            from langgraph.checkpoint.memory import InMemorySaver
        except ImportError as exc:
            raise TerminalChatDependencyError(
                "LangGraph memory dependency is missing. Install with "
                '`python3 -m pip install -e ".[chat]"`.'
            ) from exc
        yield InMemorySaver()
        return

    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    except ImportError as exc:
        raise TerminalChatDependencyError(
            "SQLite checkpoint dependency is missing. Install with "
            '`python3 -m pip install -e ".[chat]"`.'
        ) from exc

    _ensure_parent_directory(memory_db)
    async with AsyncSqliteSaver.from_conn_string(_expand_local_path(memory_db)) as saver:
        yield saver


def _expand_local_path(path_value: str) -> str:
    if path_value in {"", ":memory:", "memory", "none"}:
        return path_value
    return str(Path(path_value).expanduser())


def _ensure_parent_directory(path_value: str) -> None:
    if path_value in {"", ":memory:", "memory", "none"}:
        return
    parent = Path(path_value).parent
    if str(parent) and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


def _normalize_stream_event(event: Any) -> tuple[str, Any]:
    if isinstance(event, tuple) and len(event) == 2 and isinstance(event[0], str):
        return event
    return "updates", event


def _extract_chunk_text(payload: Any) -> str:
    if isinstance(payload, tuple) and payload:
        chunk = payload[0]
    else:
        chunk = payload

    content = getattr(chunk, "content", None)
    return _content_to_text(content)


def _extract_final_text(result: Any) -> str:
    if isinstance(result, dict) and result.get("messages"):
        return _content_to_text(result["messages"][-1].content)
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
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


def _summarize_tool_payload(content: Any) -> str:
    if not isinstance(content, str):
        content = _content_to_text(content)

    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return _shorten(str(content), 120)

    if isinstance(payload, dict):
        if "error" in payload:
            return f"erro: {_shorten(str(payload['error']), 100)}"
        if "messages" in payload:
            return f"{payload.get('count', len(payload['messages']))} mensagens"
        if "rows" in payload:
            return f"{payload.get('count', len(payload['rows']))} linhas"
        if "chats" in payload:
            return f"{payload.get('count', len(payload['chats']))} chats"
        if "senders" in payload:
            return f"{payload.get('count', len(payload['senders']))} participantes"
    return _shorten(json.dumps(payload, ensure_ascii=False), 120)


def _shorten(value: str, limit: int) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
