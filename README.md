# WhatsApp Reader

Aplicação local em Python + SQLite para importar conversas exportadas do WhatsApp, consultar mensagens estruturadas e disponibilizar esses dados para agentes de IA por CLI, chat no terminal ou MCP.

O importador, as consultas e o servidor MCP não enviam dados por conta própria e não exigem uma chave da OpenAI. Ao conectar um agent ou client de IA, porém, os resultados recuperados podem ser enviados ao provedor do modelo conforme o funcionamento e a política desse client.

## Funcionalidades

- Importação idempotente de arquivos `.txt` exportados pelo WhatsApp.
- Consultas por chat, remetente, data e intervalo de horário.
- Exportação de contexto limpo em JSON ou texto.
- Agent LangChain para perguntas sobre as conversas.
- Chat interativo com streaming, histórico e memória por sessão.
- Servidor MCP por `stdio` ou Streamable HTTP.
- Execução HTTP manual ou automática com `systemd` no Linux.

## Requisitos

- Python 3.11 ou mais recente.
- Linux, macOS ou outro sistema capaz de executar Python para os recursos normais.
- Linux com `systemd` para o auto-start descrito neste projeto.
- Uma chave `OPENAI_API_KEY` apenas para os comandos `ask` e `chat` quando for usado um modelo OpenAI.

## Instalação

Entre no diretório do projeto e crie um ambiente virtual:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[chat]"
cp .env.example .env
```

O extra `chat` é a instalação recomendada porque inclui o CLI, o agent, o frontend de terminal e o MCP. Para uma instalação menor, use um dos extras abaixo:

| Comando | Conteúdo |
| --- | --- |
| `.venv/bin/python -m pip install -e .` | Apenas importação, consulta e exportação |
| `.venv/bin/python -m pip install -e ".[agent]"` | CLI + agent LangChain |
| `.venv/bin/python -m pip install -e ".[mcp]"` | CLI + servidor MCP |
| `.venv/bin/python -m pip install -e ".[chat]"` | Todos os recursos do projeto |

Os executáveis `whatsapp-reader`, `whatsapp-chat` e `whatsapp-mcp` são criados dentro de `.venv/bin/`.

## Configuração

O projeto lê, por padrão, um arquivo `.env` no diretório de trabalho. Valores já existentes no ambiente não são sobrescritos.

```dotenv
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

| Variável | Uso |
| --- | --- |
| `OPENAI_API_KEY` | Credencial usada pelo agent/chat com modelos OpenAI |
| `WHATSAPP_READER_MODEL` | Identificador do modelo LangChain |
| `WHATSAPP_READER_DB` | Caminho do banco SQLite; padrão `whatsapp.sqlite3` |
| `WHATSAPP_READER_TIMEZONE` | Timezone usado na importação e nos filtros de data |
| `WHATSAPP_READER_SESSION` | Identificador da memória do chat |
| `WHATSAPP_READER_MEMORY_DB` | Banco SQLite da memória do chat |
| `WHATSAPP_READER_HISTORY_FILE` | Histórico de entradas do terminal |
| `WHATSAPP_READER_TOOLS_BACKEND` | Backend `mcp` (padrão) ou `direct` para diagnóstico |
| `WHATSAPP_READER_MCP_URL` | URL de um MCP HTTP já em execução |
| `WHATSAPP_READER_MCP_HOST` | Host do MCP temporário iniciado pelo chat |
| `WHATSAPP_READER_MCP_PORT` | Porta desse MCP temporário; `0` escolhe uma porta livre |

As opções passadas na linha de comando, como `--db` e `--timezone`, têm precedência. Use caminhos absolutos ao configurar um client MCP ou serviço que possa iniciar fora do diretório do projeto.

## Importar uma conversa

Exporte a conversa pelo próprio WhatsApp, sem necessidade de incluir mídias, e importe o `.txt` informando um nome para o chat:

```bash
.venv/bin/whatsapp-reader import conversa.txt --chat "Nome do grupo"
```

Outro banco ou timezone podem ser informados explicitamente:

```bash
.venv/bin/whatsapp-reader import conversa.txt \
  --chat "Nome do grupo" \
  --db dados/whatsapp.sqlite3 \
  --timezone America/Sao_Paulo
```

O parser atual aceita timestamps do TXT no formato `M/D/YY, HH:mm` ou `M/D/YYYY, HH:mm`, preserva mensagens com múltiplas linhas e diferencia mensagens de usuário e de sistema. Reimportar o mesmo conteúdo não duplica as mensagens.

## Consultar e exportar

```bash
.venv/bin/whatsapp-reader query --date 2026-07-20
.venv/bin/whatsapp-reader query --sender "Alice"
.venv/bin/whatsapp-reader query --chat "Nome do grupo" --limit 50
.venv/bin/whatsapp-reader query --from "2026-07-20 09:00" --to "2026-07-20 12:00"
.venv/bin/whatsapp-reader export-context --date 2026-07-20 --format json
.venv/bin/whatsapp-reader export-context --chat "Nome do grupo" --format txt
```

Os filtros `--chat`, `--sender`, `--date`, `--from` e `--to` podem ser combinados. Execute o help geral ou de um subcomando para ver todas as opções:

```bash
.venv/bin/whatsapp-reader --help
.venv/bin/whatsapp-reader query --help
```

## Agent e chat no terminal

Preencha `OPENAI_API_KEY` no `.env` e faça uma pergunta única:

```bash
.venv/bin/whatsapp-reader ask --chat "Nome do grupo" \
  "Quem falou sobre deploy ontem?"
```

O agent não usa RAG nesta versão: ele consulta, conta e recorta mensagens diretamente no SQLite por meio de ferramentas estruturadas e então pede ao modelo que sintetize a resposta.

Para abrir o frontend interativo com streaming e memória por sessão:

```bash
.venv/bin/whatsapp-chat
```

O onboarding verifica o banco e a chave, permite escolher o chat quando houver mais de um e então abre a conversa. Também é possível fixar o chat, a sessão ou enviar a primeira mensagem diretamente:

```bash
.venv/bin/whatsapp-chat --chat "Nome do grupo"
.venv/bin/whatsapp-chat --session projeto-a --chat "Nome do grupo"
.venv/bin/whatsapp-chat --chat "Nome do grupo" "Resuma o que aconteceu ontem"
.venv/bin/python -m whatsapp_reader chat --chat "Nome do grupo"
```

Por padrão, o chat inicia seu próprio MCP HTTP em uma porta livre e o encerra ao sair. Para usar um MCP HTTP persistente que já esteja rodando:

```bash
.venv/bin/whatsapp-chat --mcp-url http://127.0.0.1:8765/mcp
```

Para diagnóstico, o backend direto antigo continua disponível:

```bash
.venv/bin/whatsapp-chat --direct-tools
```

Comandos do chat:

- `/help`: lista os comandos.
- `/session`: mostra a sessão e o tamanho aproximado da memória.
- `/config`: mostra modelo, banco, chat e arquivos locais.
- `/chats`: lista os chats importados.
- `/new [nome]`: troca para outra sessão de memória.
- `/stream on|off`: controla o streaming.
- `/tools`: mostra as ferramentas estruturadas.
- `/clear`: limpa a tela.
- `/exit`: encerra o chat.

## Servidor MCP

O MCP expõe as mesmas ferramentas estruturadas usadas pelo agent:

- `list_chats`
- `list_senders`
- `get_messages`
- `search_messages`
- `count_messages`
- `get_context_around`

O servidor também publica o recurso `whatsapp://chats` e o prompt `analyze_whatsapp_conversations` para clients que oferecem suporte a esses recursos MCP.

Há três formas comuns de executá-lo:

| Forma | Quando usar | Quem inicia o processo |
| --- | --- | --- |
| `stdio` | Client local como Codex; opção mais simples | O próprio client, sob demanda |
| HTTP manual | Teste, desenvolvimento ou uso temporário | Você, em um terminal |
| HTTP com auto-start | Endpoint persistente para um ou mais clients | `systemd`, no login ou boot |

### Subir o servidor HTTP manualmente

Este modo mantém o processo em primeiro plano e o encerra com `Ctrl+C`:

```bash
.venv/bin/whatsapp-mcp \
  --transport http \
  --host 127.0.0.1 \
  --port 8765 \
  --db "$PWD/whatsapp.sqlite3" \
  --timezone America/Sao_Paulo \
  --chat "Nome do grupo"
```

O endpoint será `http://127.0.0.1:8765/mcp`. Remova `--chat` para permitir consultas a todos os chats por padrão.

O mesmo servidor pode ser iniciado pelo comando principal:

```bash
.venv/bin/python -m whatsapp_reader mcp --transport http --host 127.0.0.1 --port 8765
```

### Auto-start no Linux

Para manter o MCP HTTP como um serviço de usuário do `systemd`, crie uma unit que execute o comando acima e habilite-a com:

```bash
systemctl --user enable --now whatsapp-reader-mcp.service
```

Sem *linger*, o serviço inicia quando o usuário entra na sessão. Com `sudo loginctl enable-linger "$USER"`, o gerenciador de serviços desse usuário também pode iniciar no boot e continuar após logout; o MCP permanece executando sem privilégios de root.

O passo a passo completo, incluindo criação da unit, validação, logs, atualização, remoção e solução de problemas, está em [docs/autostart-systemd.md](docs/autostart-systemd.md).

### Adicionar ao Codex via `stdio`

Esta é a opção recomendada quando somente o Codex usará o servidor. O Codex inicia e encerra o processo MCP sob demanda; não é necessário executar o servidor manualmente nem configurar auto-start.

Execute a partir da raiz do projeto:

```bash
codex mcp add whatsapp-reader -- \
  "$PWD/.venv/bin/python" -m whatsapp_reader.mcp_server \
  --db "$PWD/whatsapp.sqlite3" \
  --timezone America/Sao_Paulo \
  --chat "Nome do grupo"
```

Valide a configuração:

```bash
codex mcp list
```

Dentro do Codex TUI, use `/mcp` para verificar a conexão e as ferramentas. Se precisar recriar uma configuração existente:

```bash
codex mcp remove whatsapp-reader
```

Também é possível configurar `~/.codex/config.toml` manualmente. Use caminhos absolutos reais:

```toml
[mcp_servers.whatsapp-reader]
command = "/caminho/absoluto/whatsapp-reader/.venv/bin/python"
args = [
  "-m",
  "whatsapp_reader.mcp_server",
  "--db",
  "/caminho/absoluto/whatsapp-reader/whatsapp.sqlite3",
  "--timezone",
  "America/Sao_Paulo",
  "--chat",
  "Nome do grupo",
]
```

A configuração também pode ficar em `.codex/config.toml` para ser limitada ao projeto; ela só é carregada quando o repositório está marcado como confiável no Codex.

### Adicionar ao Codex via HTTP

Primeiro suba o servidor manualmente ou pelo auto-start. Depois registre sua URL:

```bash
codex mcp add whatsapp-reader --url http://127.0.0.1:8765/mcp
codex mcp list
```

Configuração TOML equivalente:

```toml
[mcp_servers.whatsapp-reader]
url = "http://127.0.0.1:8765/mcp"
```

### Outros clients MCP

Exemplo genérico de configuração `stdio`:

```json
{
  "mcpServers": {
    "whatsapp-reader": {
      "command": "/caminho/absoluto/whatsapp-reader/.venv/bin/python",
      "args": [
        "-m",
        "whatsapp_reader.mcp_server",
        "--db",
        "/caminho/absoluto/whatsapp-reader/whatsapp.sqlite3",
        "--timezone",
        "America/Sao_Paulo",
        "--chat",
        "Nome do grupo"
      ]
    }
  }
}
```

## Testes

```bash
.venv/bin/python -m unittest -v
```

## Segurança e dados locais

- O banco SQLite contém o texto das conversas; proteja-o e inclua-o em sua rotina de backup conforme necessário.
- Não versione `.env`, bancos, históricos ou exports privados. O `.gitignore` já cobre `.env`, `*.sqlite3` e `.whatsapp-reader-*`, mas arquivos `.txt` exportados não são ignorados automaticamente; confirme o status antes de fazer commit.
- O servidor HTTP não implementa autenticação. Mantenha `--host 127.0.0.1` para acesso apenas pela própria máquina.
- Não exponha a porta com `--host 0.0.0.0` sem adicionar uma camada externa adequada de autenticação, criptografia e controle de rede.
- A chave da OpenAI não é necessária no serviço MCP; ela só é usada pelo processo do agent/chat.

## Estrutura principal

```text
whatsapp_reader/               código da aplicação
tests/                         testes automatizados
docs/autostart-systemd.md      guia completo de auto-start
whatsapp.sqlite3               banco local padrão, criado em tempo de execução
.whatsapp-reader-memory.sqlite3 memória local do chat, criada em tempo de execução
```
