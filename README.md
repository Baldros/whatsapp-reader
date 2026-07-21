# WhatsApp Reader

CLI local em Python + SQLite para importar arquivos `.txt` exportados do WhatsApp, consultar mensagens estruturadas e gerar contexto limpo para uso posterior com LLMs.

## Uso rapido

```bash
python3 -m whatsapp_reader import conversa.txt --chat "Nome do grupo"
python3 -m whatsapp_reader import "/home/techbrain/Documents/WhatsApp Chat with deepmesh - dev group.txt" --chat "deepmesh dev group"
python3 -m whatsapp_reader query --date 2026-07-20
python3 -m whatsapp_reader query --sender "Alice"
python3 -m whatsapp_reader query --from "2026-07-20 09:00" --to "2026-07-20 12:00"
python3 -m whatsapp_reader export-context --date 2026-07-20 --format json
```

Por padrao o banco fica em `whatsapp.sqlite3` no diretorio atual. Use `--db caminho.sqlite3` em qualquer comando para escolher outro arquivo.

Datas do TXT sao interpretadas como horario local no timezone `America/Sao_Paulo`, no formato `M/D/YY, HH:mm`.

## Agent

O agent usa LangChain com ferramentas estruturadas sobre o SQLite local. Ele nao usa RAG nesta versao: primeiro busca, conta e recorta mensagens no banco, depois o modelo sintetiza a resposta.

```bash
cp .env.example .env
.venv/bin/python -m pip install -e ".[chat]"
.venv/bin/python -m whatsapp_reader ask --chat "deepmesh dev group" "Quem falou sobre deploy ontem?"
```

Variaveis aceitas no `.env`:

```bash
OPENAI_API_KEY=
WHATSAPP_READER_MODEL=openai:gpt-5.6-terra
WHATSAPP_READER_DB=whatsapp.sqlite3
WHATSAPP_READER_TIMEZONE=America/Sao_Paulo
WHATSAPP_READER_SESSION=default
WHATSAPP_READER_MEMORY_DB=.whatsapp-reader-memory.sqlite3
WHATSAPP_READER_HISTORY_FILE=.whatsapp-reader-history
WHATSAPP_READER_TOOLS_BACKEND=mcp
WHATSAPP_READER_MCP_URL=
WHATSAPP_READER_MCP_HOST=127.0.0.1
WHATSAPP_READER_MCP_PORT=0
```

Ferramentas disponiveis para o agent:

- `list_chats`: lista chats importados e intervalos.
- `list_senders`: lista participantes com contagens.
- `get_messages`: busca mensagens por data, intervalo, pessoa e chat.
- `search_messages`: busca texto nas mensagens com filtros opcionais.
- `count_messages`: agrega por chat, pessoa, data, hora ou tipo.
- `get_context_around`: recupera mensagens vizinhas pelo `message_id`.

## MCP

As mesmas ferramentas tambem podem rodar como um servidor MCP local. Isso permite conectar o banco de conversas em qualquer agent/client que suporte MCP.

Instale as dependencias:

```bash
.venv/bin/python -m pip install -e ".[chat]"
```

Suba um servidor Streamable HTTP para clients externos:

```bash
.venv/bin/whatsapp-mcp --transport http --port 8765 --chat "deepmesh dev group"
# endpoint: http://127.0.0.1:8765/mcp
```

O mesmo servidor tambem esta disponivel pelo comando principal:

```bash
.venv/bin/python -m whatsapp_reader mcp --transport http --port 8765
```

Exemplo de configuracao `stdio` para clients que preferem iniciar o processo MCP sob demanda:

```json
{
  "mcpServers": {
    "whatsapp-reader": {
      "command": "/home/techbrain/andre/whatsapp-reader/.venv/bin/python",
      "args": [
        "-m",
        "whatsapp_reader.mcp_server",
        "--db",
        "/home/techbrain/andre/whatsapp-reader/whatsapp.sqlite3",
        "--timezone",
        "America/Sao_Paulo",
        "--chat",
        "deepmesh dev group"
      ]
    }
  }
}
```

## Chat no terminal

Para conversar com o agent em um terminal com streaming e memoria por sessao:

```bash
.venv/bin/whatsapp-chat
```

O frontend valida se ha mensagens importadas, permite escolher o chat quando existir mais de um, pede `OPENAI_API_KEY` se ela nao estiver configurada e depois abre a conversa no terminal.

Por padrao o chat usa `WHATSAPP_READER_TOOLS_BACKEND=mcp`: ele sobe um MCP local via HTTP em uma porta livre, conecta as ferramentas ao LangChain e encerra esse processo quando voce sai. Para usar um MCP ja rodando:

```bash
.venv/bin/whatsapp-chat --mcp-url http://127.0.0.1:8765/mcp
```

Para diagnostico, ainda existe o backend direto antigo:

```bash
.venv/bin/whatsapp-chat --direct-tools
```

Tambem da para fixar um chat ou enviar a primeira mensagem direto:

```bash
.venv/bin/whatsapp-chat --chat "deepmesh dev group"
.venv/bin/whatsapp-chat --chat "deepmesh dev group" "Resume o que aconteceu ontem"
.venv/bin/python -m whatsapp_reader chat --chat "deepmesh dev group" "Resume o que aconteceu ontem"
```

O chat salva memoria em `WHATSAPP_READER_MEMORY_DB` e usa `WHATSAPP_READER_SESSION` como `thread_id`. Para retomar a mesma conversa, use a mesma sessao; para comecar outra, use `/new` dentro do chat ou passe `--session outro-nome`.

Comandos dentro do chat:

- `/help`: lista comandos.
- `/session`: mostra sessao, streaming e tamanho aproximado da memoria.
- `/config`: mostra modelo, banco, chat e arquivos locais.
- `/chats`: lista chats importados no banco.
- `/new [nome]`: troca para outra memoria de conversa.
- `/stream on|off`: liga ou desliga streaming.
- `/tools`: mostra ferramentas estruturadas.
- `/clear`: limpa a tela.
- `/exit`: sai.
