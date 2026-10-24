# Auto-start do MCP HTTP com systemd

Este guia configura o WhatsApp Reader MCP como um serviço do usuário no Linux. O processo continua pertencendo ao usuário comum, sem privilégios de root, e publica por padrão somente em `127.0.0.1:8765`.

## Quando usar

Use este auto-start quando um client se conectar ao endpoint HTTP persistente:

```text
http://127.0.0.1:8765/mcp
```

Se o client usa transporte `stdio`, ele normalmente inicia o MCP sob demanda e este serviço não é necessário. O chat do próprio projeto também inicia seu próprio MCP via `stdio` e o encerra ao sair.

## Pré-requisitos

- Uma distribuição Linux com `systemd` e suporte a serviços de usuário.
- Python 3.11 ou mais recente.
- O projeto instalado em `.venv` com o extra `mcp` ou `chat`.
- Um banco já importado ou um caminho no qual o processo tenha permissão para criar o SQLite.

Na raiz do projeto, confirme os caminhos e o executável:

```bash
pwd -P
test -x .venv/bin/whatsapp-mcp && echo "whatsapp-mcp encontrado"
test -f whatsapp.sqlite3 && echo "banco encontrado"
```

Se o executável ainda não existir:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[mcp]"
```

## 1. Criar a unit do usuário

Crie o diretório e abra o arquivo da unit:

```bash
mkdir -p "$HOME/.config/systemd/user"
nano "$HOME/.config/systemd/user/whatsapp-reader-mcp.service"
```

Cole o conteúdo abaixo, substituindo todas as ocorrências de `/CAMINHO/ABSOLUTO/whatsapp-reader` pelo resultado de `pwd -P`:

```ini
[Unit]
Description=WhatsApp Reader MCP Server

[Service]
Type=simple
WorkingDirectory=/CAMINHO/ABSOLUTO/whatsapp-reader
ExecStart=/CAMINHO/ABSOLUTO/whatsapp-reader/.venv/bin/whatsapp-mcp --transport http --host 127.0.0.1 --port 8765 --db /CAMINHO/ABSOLUTO/whatsapp-reader/whatsapp.sqlite3 --env-file /CAMINHO/ABSOLUTO/whatsapp-reader/.env --timezone America/Sao_Paulo
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Regras importantes:

- `WorkingDirectory`, o executável, o banco e o `.env` devem usar caminhos absolutos.
- O arquivo `.env` é opcional; o programa ignora esse caminho se o arquivo não existir.
- `--chat "Nome do grupo"` pode ser acrescentado ao final de `ExecStart` para definir o escopo padrão. Sem essa opção, as ferramentas podem consultar qualquer chat importado.
- Mantenha `--host 127.0.0.1`, pois o MCP HTTP não possui autenticação própria.
- A unit não precisa de `OPENAI_API_KEY`: o MCP lê o banco e oferece ferramentas, mas não chama o modelo.

Opcionalmente, valide a sintaxe antes de habilitar:

```bash
systemd-analyze --user verify "$HOME/.config/systemd/user/whatsapp-reader-mcp.service"
```

## 2. Habilitar e iniciar

Recarregue as units, habilite o início automático e inicie o processo agora:

```bash
systemctl --user daemon-reload
systemctl --user enable --now whatsapp-reader-mcp.service
```

Essa operação não usa `sudo`: o arquivo e o serviço pertencem ao usuário atual.

Por padrão, um serviço de usuário habilitado inicia com a sessão desse usuário. Em uma máquina desktop, isso normalmente significa que ele estará disponível após o login.

## 3. Verificar

Confira se o serviço está ativo:

```bash
systemctl --user status whatsapp-reader-mcp.service
systemctl --user is-enabled whatsapp-reader-mcp.service
systemctl --user is-active whatsapp-reader-mcp.service
```

Veja os logs recentes ou acompanhe-os em tempo real:

```bash
journalctl --user -u whatsapp-reader-mcp.service -n 50 --no-pager
journalctl --user -u whatsapp-reader-mcp.service -f
```

Confirme que a porta está aberta localmente:

```bash
ss -ltn 'sport = :8765'
```

Um `curl` simples recebe `405 Method Not Allowed`, pois `/mcp` só aceita `POST` com mensagens do protocolo MCP. Mesmo assim, receber uma resposta HTTP confirma que há um servidor na porta. A validação funcional deve ser feita por um client MCP.

Por exemplo, no Codex:

```bash
codex mcp add whatsapp-reader --url http://127.0.0.1:8765/mcp
codex mcp list
```

Se já existir uma entrada com o mesmo nome e outra configuração, remova-a antes de adicioná-la novamente:

```bash
codex mcp remove whatsapp-reader
```

O `codex mcp list` mostra `Unsupported` na coluna Auth, porque o servidor não usa autenticação. Para chegar a essa conclusão, o Codex consulta alguns endereços `/.well-known/...` de descoberta OAuth, que aparecem no log do serviço com `404`; é o comportamento esperado.

## 4. Iniciar no boot, antes do login

O comando `enable` acima garante o auto-start quando o gerenciador de serviços do usuário for iniciado, normalmente durante o login. Para iniciar também no boot e manter o serviço após logout, habilite *linger*:

```bash
sudo loginctl enable-linger "$USER"
```

Esse comando:

- pede autorização administrativa para alterar a política de login do usuário;
- não transforma o MCP em um processo root;
- permite que o `systemd --user` exista sem uma sessão interativa aberta;
- pode manter outros serviços de usuário habilitados em execução, além deste MCP.

Verifique o estado:

```bash
loginctl show-user "$USER" -p Linger
```

Para desfazer:

```bash
sudo loginctl disable-linger "$USER"
```

Se o MCP só precisa funcionar enquanto você está logado, não é necessário habilitar *linger*.

## Operações do dia a dia

```bash
# Parar ou iniciar sem alterar o auto-start
systemctl --user stop whatsapp-reader-mcp.service
systemctl --user start whatsapp-reader-mcp.service

# Reiniciar após mudar configuração, código ou banco
systemctl --user restart whatsapp-reader-mcp.service

# Consultar estado e logs
systemctl --user status whatsapp-reader-mcp.service
journalctl --user -u whatsapp-reader-mcp.service -n 100 --no-pager
```

Após editar a unit, sempre recarregue sua definição e reinicie:

```bash
systemctl --user daemon-reload
systemctl --user restart whatsapp-reader-mcp.service
```

Após atualizar as dependências do projeto:

```bash
.venv/bin/python -m pip install -e ".[mcp]"
systemctl --user restart whatsapp-reader-mcp.service
```

## Desabilitar ou remover

Desabilitar o auto-start e parar o processo, preservando a unit:

```bash
systemctl --user disable --now whatsapp-reader-mcp.service
```

Remover completamente a unit:

```bash
systemctl --user disable --now whatsapp-reader-mcp.service
rm "$HOME/.config/systemd/user/whatsapp-reader-mcp.service"
systemctl --user daemon-reload
```

Desabilitar a unit não remove o banco, `.env`, ambiente virtual ou código do projeto.

## Solução de problemas

### `status=203/EXEC`

O caminho de `ExecStart` está incorreto ou o executável não existe. Confira:

```bash
ls -l /CAMINHO/ABSOLUTO/whatsapp-reader/.venv/bin/whatsapp-mcp
```

Depois corrija a unit, execute `systemctl --user daemon-reload` e reinicie o serviço.

### Banco errado ou vazio

Caminhos relativos dependem do diretório de trabalho e são uma causa comum desse problema. Use um caminho absoluto em `--db` e confirme as conversas pela CLI:

```bash
/CAMINHO/ABSOLUTO/whatsapp-reader/.venv/bin/whatsapp-reader \
  query --db /CAMINHO/ABSOLUTO/whatsapp-reader/whatsapp.sqlite3 --limit 1
```

### Porta 8765 já está em uso

Descubra o processo que ocupa a porta:

```bash
ss -ltnp 'sport = :8765'
```

Pare o processo duplicado ou altere `--port` na unit e também a URL configurada no client.

### O serviço entra em ciclo de reinício

Pare-o temporariamente e leia o erro completo:

```bash
systemctl --user stop whatsapp-reader-mcp.service
journalctl --user -u whatsapp-reader-mcp.service -n 100 --no-pager
```

As causas mais comuns são caminho incorreto, dependência ausente, banco sem permissão de escrita ou porta ocupada.

### `Failed to connect to bus`

Execute os comandos como o usuário dono da unit, dentro de uma sessão desse usuário. Se a máquina não cria uma sessão de usuário no boot, habilite *linger* conforme a seção anterior.

### Funciona no terminal, mas não no systemd

Serviços não herdam todas as variáveis do shell interativo. Coloque valores necessários no `.env` indicado por `--env-file` ou declare uma linha `Environment=NOME=valor` na seção `[Service]`. Não inclua segredos na unit se ela puder ser lida por outros usuários.
