from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import json
import shlex
import uuid
from typing import Any, Iterator

from .agent import create_conversation_agent
from .config import (
    DEFAULT_CHAT_HISTORY_FILE,
    DEFAULT_CHAT_MEMORY_DB,
    DEFAULT_CHAT_SESSION,
)


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
    stream: bool = True


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

    def run(self, initial_message: str | None = None) -> None:
        self._render_header()
        self._render_help_hint()

        if initial_message:
            self._send_message(initial_message)

        while True:
            try:
                user_input = self.prompt_session.prompt("voce > ").strip()
            except KeyboardInterrupt:
                self.console.print("\n[dim]Use /exit para sair.[/]")
                continue
            except EOFError:
                self.console.print()
                return

            if not user_input:
                continue
            if user_input.startswith("/"):
                if self._handle_command(user_input):
                    return
                continue

            self._send_message(user_input)

    def _send_message(self, user_input: str) -> None:
        if self.config.stream:
            self._stream_message(user_input)
            return

        result = self.agent.invoke(
            {"messages": [{"role": "user", "content": user_input}]},
            config=self._runtime_config(),
        )
        answer = _extract_final_text(result)
        self.console.print("[bold green]agent >[/]")
        self.console.print(self.deps["Markdown"](answer))

    def _stream_message(self, user_input: str) -> None:
        started_answer = False
        emitted_text = False
        seen_tool_call_ids: set[str] = set()

        try:
            events = self.agent.stream(
                {"messages": [{"role": "user", "content": user_input}]},
                config=self._runtime_config(),
                stream_mode=["messages", "updates"],
            )
            for event in events:
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

    def _handle_command(self, command_line: str) -> bool:
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
            self._render_session()
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
            f"memoria: [cyan]{self.config.memory_db}[/]"
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
        table.add_row("/new [nome]", "Troca para uma nova sessao de memoria.")
        table.add_row("/stream", "Alterna streaming on/off.")
        table.add_row("/stream on|off", "Define streaming explicitamente.")
        table.add_row("/tools", "Mostra as ferramentas estruturadas disponiveis.")
        table.add_row("/clear", "Limpa a tela.")
        table.add_row("/exit", "Sai do chat.")
        self.console.print(table)

    def _render_tools(self) -> None:
        Table = self.deps["Table"]
        table = Table(title="Ferramentas do Agent")
        table.add_column("Ferramenta", style="cyan", no_wrap=True)
        table.add_column("Uso")
        table.add_row("list_chats", "Lista chats importados e intervalos.")
        table.add_row("list_senders", "Lista participantes com contagens.")
        table.add_row("get_messages", "Busca mensagens por data, intervalo, pessoa e chat.")
        table.add_row("search_messages", "Busca texto nas mensagens com filtros.")
        table.add_row("count_messages", "Agrega por chat, pessoa, data, hora ou tipo.")
        table.add_row("get_context_around", "Busca mensagens vizinhas pelo message_id.")
        self.console.print(table)

    def _render_session(self) -> None:
        message_count = "?"
        try:
            state = self.agent.get_state(self._runtime_config())
            messages = state.values.get("messages", []) if state.values else []
            message_count = str(len(messages))
        except Exception:
            pass

        self.console.print(f"[bold]sessao:[/] {self.config.session_id}")
        self.console.print(f"[bold]mensagens na memoria:[/] {message_count}")
        self.console.print(f"[bold]stream:[/] {'on' if self.config.stream else 'off'}")
        self.console.print(f"[bold]chat scope:[/] {self.config.chat_name or '*'}")


def run_terminal_chat(
    *,
    config: TerminalChatConfig,
    initial_message: str | None = None,
) -> None:
    deps = _load_terminal_dependencies()
    console = deps["Console"]()
    config.history_file = _expand_local_path(config.history_file)
    config.memory_db = _expand_local_path(config.memory_db)
    _ensure_parent_directory(config.history_file)
    prompt_session = deps["PromptSession"](
        history=deps["FileHistory"](config.history_file),
    )

    with _open_checkpointer(config.memory_db) as checkpointer:
        agent = create_conversation_agent(
            db_path=config.db_path,
            timezone=config.timezone,
            model=config.model,
            default_chat=config.chat_name,
            checkpointer=checkpointer,
        )
        app = TerminalChatApp(
            config=config,
            agent=agent,
            console=console,
            prompt_session=prompt_session,
            deps=deps,
        )
        app.run(initial_message=initial_message)


def _load_terminal_dependencies() -> dict[str, Any]:
    try:
        from prompt_toolkit import PromptSession
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
        "Markdown": Markdown,
        "Panel": Panel,
        "PromptSession": PromptSession,
        "Table": Table,
    }


@contextmanager
def _open_checkpointer(memory_db: str) -> Iterator[Any]:
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
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise TerminalChatDependencyError(
            "SQLite checkpoint dependency is missing. Install with "
            '`python3 -m pip install -e ".[chat]"`.'
        ) from exc

    _ensure_parent_directory(memory_db)
    with SqliteSaver.from_conn_string(_expand_local_path(memory_db)) as saver:
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


def _summarize_tool_payload(content: str) -> str:
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
