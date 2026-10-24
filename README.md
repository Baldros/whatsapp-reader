# WhatsApp Reader

Aplicação local em Python + SQLite para importar conversas exportadas do WhatsApp, consultar mensagens estruturadas e disponibilizar esses dados para agentes de IA por CLI, chat no terminal ou MCP.

O importador, as consultas e o servidor MCP não enviam dados por conta própria e não exigem uma chave da OpenAI. Ao conectar um agent ou client de IA, porém, os resultados recuperados podem ser enviados ao provedor do modelo conforme o funcionamento e a política desse client.

## Funcionalidades

- Importação idempotente de arquivos `.txt` exportados pelo WhatsApp.
- Ingestão de exports locais diretamente por ferramenta MCP.
- Consultas por chat, remetente, data e intervalo de horário.
- Busca de texto que ignora acentos e maiúsculas, com o índice FTS5 do SQLite.
- Exportação de contexto limpo em JSON ou texto.
- Agent LangChain para perguntas sobre as conversas.
- Chat interativo com streaming, histórico e memória por sessão.
- Servidor MCP por `stdio` ou Streamable HTTP.
- Execução HTTP manual ou automática com `systemd` no Linux ou Agendador de Tarefas no Windows.

## Requisitos

- Python 3.11 ou mais recente, com o módulo `sqlite3` compilado com FTS5, como nos instaladores oficiais do Python e nas distribuições Linux comuns.
- Linux, macOS, Windows ou outro sistema capaz de executar Python para os recursos normais.
- Linux com `systemd` ou Windows com Agendador de Tarefas para as opções de auto-start documentadas.
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
WHATSAPP_READER_DB_ROOT=
WHATSAPP_READER_TIMEZONE=America/Sao_Paulo
WHATSAPP_READER_SESSION=default
WHATSAPP_READER_MEMORY_DB=.whatsapp-reader-memory.sqlite3
WHATSAPP_READER_HISTORY_FILE=.whatsapp-reader-history
WHATSAPP_READER_TOOLS_BACKEND=mcp
WHATSAPP_READER_MCP_URL=
```

| Variável | Uso |
| --- | --- |
| `OPENAI_API_KEY` | Credencial usada pelo agent/chat com modelos OpenAI |
| `WHATSAPP_READER_MODEL` | Identificador do modelo LangChain |
| `WHATSAPP_READER_DB` | Caminho do banco SQLite; padrão `whatsapp.sqlite3` |
| `WHATSAPP_READER_DB_ROOT` | Diretório que guarda uma pasta por banco; o banco deste projeto fica em `<raiz>/whatsapp-reader/whatsapp.sqlite3` |
| `WHATSAPP_READER_TIMEZONE` | Timezone usado na importação e nos filtros de data |
| `WHATSAPP_READER_SESSION` | Identificador da memória do chat |
| `WHATSAPP_READER_MEMORY_DB` | Banco SQLite da memória do chat |
| `WHATSAPP_READER_HISTORY_FILE` | Histórico de entradas do terminal |
| `WHATSAPP_READER_TOOLS_BACKEND` | Backend `mcp` (padrão) ou `direct` para diagnóstico |
| `WHATSAPP_READER_MCP_URL` | URL de um MCP HTTP já em execução; vazia, o chat inicia um MCP próprio via `stdio` |

O caminho do banco é resolvido nesta ordem: `--db`, depois `WHATSAPP_READER_DB`, depois `WHATSAPP_READER_DB_ROOT` e, por fim, `whatsapp.sqlite3` no diretório de trabalho. Use `WHATSAPP_READER_DB_ROOT` para manter os bancos fora do repositório, em um diretório com uma pasta por banco; a pasta do projeto é criada na primeira abertura, assim como qualquer pasta ausente no caminho informado em `--db`.

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

O parser atual aceita timestamps do TXT no formato `M/D/YY, HH:mm` ou `M/D/YYYY, HH:mm`, preserva mensagens com múltiplas linhas e diferencia mensagens de usuário e de sistema. Reimportar o mesmo conteúdo não duplica as mensagens. Mensagens idênticas enviadas no mesmo minuto, como várias mídias seguidas ou um "ok" repetido, são mantidas separadamente, e o mesmo export pode ser importado em chats diferentes.

Bancos criados por versões anteriores ao esquema 1 descartavam essas mensagens repetidas. Na primeira abertura com a versão atual, o banco é migrado automaticamente. Depois disso, reimporte os exports originais com o mesmo `--chat` para recuperar as mensagens que faltavam; as que já estão no banco não são duplicadas. Antes de migrar, encerre os processos que ainda usam a versão antiga, como o MCP HTTP iniciado pelo Agendador de Tarefas ou pelo `systemd`.

O esquema 2 acrescenta o índice de busca de texto. Ele também é criado automaticamente na primeira abertura, indexa as mensagens já importadas e não altera nenhuma delas.

O esquema 3 acrescenta índices para comparar horários pelo instante, mesmo quando as mensagens têm fusos diferentes. Os timestamps originais, IDs, fingerprints e textos são preservados.

A inicialização e as migrações são executadas em uma única transação. A versão é conferida antes de qualquer alteração e novamente depois de obter o bloqueio de escrita; um esquema mais novo que o código é recusado com uma mensagem para atualizar a aplicação. Uma falha na migração desfaz suas alterações. Isso não substitui encerrar processos antigos antes de atualizar: conexões já abertas continuam usando o código que as iniciou.

Um client MCP também pode fazer a mesma importação chamando `import_conversation` com o caminho absoluto do `.txt`. O `chat_name` é opcional quando o servidor foi iniciado com `--chat`:

```text
import_conversation(
  file_path="C:\Users\usuario\Downloads\WhatsApp Chat.txt",
  chat_name="Nome do grupo"
)
```

Essa ferramenta reutiliza o mesmo parser e a mesma importação idempotente do CLI; o comando de terminal continua disponível.

## Consultar e exportar

```bash
.venv/bin/whatsapp-reader query --date 2026-07-20
.venv/bin/whatsapp-reader query --sender "Alice"
.venv/bin/whatsapp-reader query --chat "Nome do grupo" --limit 50
.venv/bin/whatsapp-reader query --from "2026-07-20 09:00" --to "2026-07-20 12:00"
.venv/bin/whatsapp-reader export-context --date 2026-07-20 --format json
.venv/bin/whatsapp-reader export-context --chat "Nome do grupo" --format txt
```

Os filtros `--chat`, `--sender`, `--date`, `--from` e `--to` podem ser combinados. `--from` e `--to` aceitam `YYYY-MM-DD HH:MM` no timezone configurado ou ISO 8601 com fuso, como `2026-07-20T12:00:00Z`, que é convertido para esse timezone; uma data sem horário é recusada. Execute o help geral ou de um subcomando para ver todas as opções:

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

Por padrão, o chat inicia seu próprio MCP como subprocesso via `stdio`, sem abrir nenhuma porta, e o encerra ao sair. Avisos e erros desse servidor aparecem no próprio terminal. Para usar um MCP HTTP persistente que já esteja rodando:

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

O MCP expõe as seguintes ferramentas estruturadas:

- `import_conversation`
- `list_chats`
- `list_senders`
- `get_messages`
- `search_messages`
- `count_messages`
- `get_context_around`

Cada parâmetro tem descrição no schema da ferramenta, incluindo os formatos de data aceitos. `get_messages` e `search_messages` devolvem as mensagens mais antigas primeiro; `order="desc"` traz as mais recentes. O resultado é limitado por `limit`, e `has_more` indica que outras mensagens também atendiam aos filtros.

Os filtros e a ordenação comparam instantes, inclusive ao consultar em um timezone diferente do usado na importação. As mensagens mantêm o timestamp com o fuso original; os campos `first_timestamp` e `last_timestamp` das listagens e contagens são apresentados em UTC. Os agrupamentos por `date` e `hour` continuam usando a data e a hora locais gravadas na importação.

Falhas de validação das ferramentas MCP são sinalizadas com `isError: true`, incluindo datas inválidas, busca vazia e caminho de importação inválido.

`search_messages` procura palavras inteiras, juntas e na ordem dada, ignorando acentos e maiúsculas: `reuniao` encontra "Reunião". A última palavra também casa com palavras mais longas, então `reuni` encontra "reuniões". Um trecho no meio de uma palavra não é encontrado: `deploy` acha "deployment", mas não "redeploy". Buscas só com emoji ou pontuação comparam o texto literalmente, incluindo `%` e `_`.

O servidor também publica o recurso `whatsapp://chats` e o prompt `analyze_whatsapp_conversations` para clients que oferecem suporte a esses recursos MCP.

Há três formas comuns de executá-lo:

| Forma | Quando usar | Quem inicia o processo |
| --- | --- | --- |
| `stdio` | Client local como Codex; opção mais simples | O próprio client, sob demanda |
| HTTP manual | Teste, desenvolvimento ou uso temporário | Você, em um terminal |
| HTTP com auto-start | Endpoint persistente para um ou mais clients | `systemd` ou Agendador de Tarefas, no login ou boot |

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

O endpoint é *stateless*, responde em JSON e não tem autenticação. Ele aceita apenas `POST`; `GET` e `DELETE` recebem `405 Method Not Allowed`, pois não há stream SSE nem sessão para encerrar.

O log mostra uma linha por requisição HTTP. Os logs internos do SDK MCP ficam em `WARNING`; use `--log-level DEBUG` para vê-los ou `--log-level WARNING` para esconder também as linhas de requisição.

Ao listar ou conectar o servidor, clients como o Codex verificam se ele oferece login OAuth. No log, isso aparece como algumas requisições `GET /.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server` e `/.well-known/openid-configuration` respondidas com `404`. É o esperado para um servidor sem OAuth, e o Codex mostra `Unsupported` na coluna Auth de `codex mcp list`.

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

### Auto-start no Windows

No Windows, o MCP HTTP pode ser iniciado pelo Agendador de Tarefas no login do usuário, sem elevação administrativa, ou opcionalmente no boot pela conta `SYSTEM`.

O passo a passo completo, incluindo instalação no PowerShell, criação e validação da tarefa, variante de boot, atualização, remoção e solução de problemas, está em [docs/autostart-windows.md](docs/autostart-windows.md).

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
- A ferramenta `import_conversation` pode ler um arquivo `.txt` local informado pelo client. Mantenha o endpoint restrito e só autorize caminhos esperados.
- Não exponha a porta com `--host 0.0.0.0` sem adicionar uma camada externa adequada de autenticação, criptografia e controle de rede.
- A chave da OpenAI não é necessária no serviço MCP; ela só é usada pelo processo do agent/chat.

## Estrutura principal

```text
whatsapp_reader/               código da aplicação
tests/                         testes automatizados
docs/autostart-systemd.md      guia de auto-start no Linux
docs/autostart-windows.md      guia de auto-start no Windows
whatsapp.sqlite3               banco local padrão, criado em tempo de execução
.whatsapp-reader-memory.sqlite3 memória local do chat, criada em tempo de execução
```
