# Auto-start do MCP HTTP no Windows

Este guia configura o WhatsApp Reader MCP no Agendador de Tarefas do Windows. A configuração recomendada inicia o servidor quando o usuário entra no Windows, sem privilégios administrativos, e publica somente em `127.0.0.1:8765`.

## Quando usar

Use este auto-start quando um client se conectar ao endpoint HTTP persistente:

```text
http://127.0.0.1:8765/mcp
```

Se o client usa transporte `stdio`, ele normalmente inicia o MCP sob demanda e esta tarefa não é necessária. O chat do próprio projeto também consegue iniciar um MCP temporário automaticamente e encerrá-lo ao sair.

## Pré-requisitos

- Windows 10 ou 11 com PowerShell 5.1 ou mais recente.
- Python 3.11 ou mais recente.
- O projeto em uma pasta local com caminho estável.
- O projeto instalado em `.venv` com o extra `mcp` ou `chat`.
- Um banco já importado ou um caminho no qual o processo tenha permissão para criar o SQLite.

Abra o PowerShell na raiz do projeto e, se necessário, prepare o ambiente:

```powershell
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[mcp]"
Copy-Item .env.example .env
```

Confirme os caminhos e o executável:

```powershell
$project = (Resolve-Path .).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
$db = Join-Path $project 'whatsapp.sqlite3'

$project
Test-Path -LiteralPath $python
Test-Path -LiteralPath $db
```

O banco pode não existir antes da primeira importação. Para confirmar um banco já populado:

```powershell
& $python -m whatsapp_reader query --db $db --limit 1
```

## 1. Testar o servidor manualmente

Antes de criar a tarefa, execute o mesmo comando que será usado no auto-start:

```powershell
$envFile = Join-Path $project '.env'

& $python -m whatsapp_reader.mcp_server `
  --transport http `
  --host 127.0.0.1 `
  --port 8765 `
  --db $db `
  --env-file $envFile `
  --timezone America/Sao_Paulo
```

O endpoint será `http://127.0.0.1:8765/mcp`. Encerre o teste com `Ctrl+C`.

Acrescente `--chat "Nome do grupo"` para definir o escopo padrão. Sem essa opção, as ferramentas podem consultar qualquer chat importado.

## 2. Criar a tarefa para o login do usuário

Execute todo o bloco abaixo no PowerShell aberto na raiz do projeto. Não é necessário executar como administrador:

```powershell
$taskName = 'WhatsApp Reader MCP'
$project = (Resolve-Path .).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
$db = Join-Path $project 'whatsapp.sqlite3'
$envFile = Join-Path $project '.env'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python do ambiente virtual não encontrado: $python"
}

$arguments = @(
    '-m whatsapp_reader.mcp_server'
    '--transport http'
    '--host 127.0.0.1'
    '--port 8765'
    "--db `"$db`""
    "--env-file `"$envFile`""
    '--timezone America/Sao_Paulo'
) -join ' '

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument $arguments `
    -WorkingDirectory $project

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal `
    -UserId $identity `
    -LogonType Interactive `
    -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'WhatsApp Reader MCP em http://127.0.0.1:8765/mcp'

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force
Start-ScheduledTask -TaskName $taskName
```

Essa tarefa:

- inicia somente depois do login desse usuário;
- não pede nem armazena sua senha;
- executa sem elevação administrativa;
- reinicia até três vezes se o processo falhar;
- não abre o servidor para outras máquinas da rede.

O arquivo `.env` é opcional; o programa ignora o caminho se ele não existir. O MCP não precisa de `OPENAI_API_KEY`, pois ele lê o banco e oferece ferramentas, mas não chama o modelo.

## 3. Verificar

Confira o estado e os últimos dados de execução:

```powershell
Get-ScheduledTask -TaskName 'WhatsApp Reader MCP' |
    Select-Object TaskName, State

Get-ScheduledTaskInfo -TaskName 'WhatsApp Reader MCP' |
    Select-Object LastRunTime, LastTaskResult, NextRunTime
```

Enquanto o MCP estiver ativo, o estado esperado da tarefa é `Running`, pois o servidor permanece em primeiro plano.

Confirme que a porta está acessível:

```powershell
Test-NetConnection 127.0.0.1 -Port 8765
```

`TcpTestSucceeded` deve ser `True`. Uma requisição HTTP comum a `/mcp` pode receber `406 Not Acceptable`, pois o endpoint espera cabeçalhos e mensagens do protocolo MCP. A validação funcional deve ser feita por um client MCP.

Por exemplo, no Codex:

```powershell
codex mcp add whatsapp-reader --url http://127.0.0.1:8765/mcp
codex mcp list
```

Se já existir uma entrada com o mesmo nome e outra configuração, remova-a antes de adicionar novamente:

```powershell
codex mcp remove whatsapp-reader
```

Também é possível abrir `taskschd.msc` e localizar **WhatsApp Reader MCP** na Biblioteca do Agendador de Tarefas.

## Iniciar no boot, antes do login

Para disponibilizar o endpoint antes de qualquer login, a tarefa pode usar o gatilho de inicialização e a conta interna `SYSTEM`. Essa opção exige PowerShell como administrador.

Use somente uma das duas modalidades. Primeiro remova a tarefa de login, se ela já existir:

```powershell
Stop-ScheduledTask -TaskName 'WhatsApp Reader MCP' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'WhatsApp Reader MCP' -Confirm:$false
```

Depois, ainda no PowerShell aberto como administrador e na raiz do projeto, registre a variante de boot:

```powershell
$taskName = 'WhatsApp Reader MCP'
$project = (Resolve-Path .).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
$db = Join-Path $project 'whatsapp.sqlite3'
$envFile = Join-Path $project '.env'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python do ambiente virtual não encontrado: $python"
}

$arguments = @(
    '-m whatsapp_reader.mcp_server'
    '--transport http'
    '--host 127.0.0.1'
    '--port 8765'
    "--db `"$db`""
    "--env-file `"$envFile`""
    '--timezone America/Sao_Paulo'
) -join ' '

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument $arguments `
    -WorkingDirectory $project

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal `
    -UserId 'SYSTEM' `
    -LogonType ServiceAccount `
    -RunLevel Highest

$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'WhatsApp Reader MCP em http://127.0.0.1:8765/mcp'

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force
Start-ScheduledTask -TaskName $taskName
```

Na modalidade de boot:

- mantenha projeto, `.venv`, `.env` e banco em um disco local fixo;
- não use unidade de rede mapeada, pois os mapeamentos do usuário não existem para `SYSTEM`;
- garanta que `SYSTEM` tenha leitura no projeto e leitura/escrita no banco e em sua pasta;
- proteja o `.env`, pois ele será legível pela conta do sistema;
- a tarefa continua elevada, embora o servidor escute apenas em `127.0.0.1`.

Para a maioria dos desktops, o início no login é mais simples e tem privilégios menores.

## Operações do dia a dia

```powershell
# Parar ou iniciar sem alterar o auto-start
Stop-ScheduledTask -TaskName 'WhatsApp Reader MCP'
Start-ScheduledTask -TaskName 'WhatsApp Reader MCP'

# Consultar a definição completa
Get-ScheduledTask -TaskName 'WhatsApp Reader MCP' | Format-List *

# Consultar informações da última execução
Get-ScheduledTaskInfo -TaskName 'WhatsApp Reader MCP'
```

Após atualizar o código ou as dependências:

```powershell
Stop-ScheduledTask -TaskName 'WhatsApp Reader MCP'
& .\.venv\Scripts\python.exe -m pip install -e ".[mcp]"
Start-ScheduledTask -TaskName 'WhatsApp Reader MCP'
```

Se mudar caminho, porta, banco, timezone ou chat, pare a tarefa e execute novamente o bloco de registro com os novos argumentos.

## Desabilitar ou remover

Desabilitar o auto-start e parar o processo, preservando a tarefa:

```powershell
Stop-ScheduledTask -TaskName 'WhatsApp Reader MCP' -ErrorAction SilentlyContinue
Disable-ScheduledTask -TaskName 'WhatsApp Reader MCP'
```

Habilitar novamente:

```powershell
Enable-ScheduledTask -TaskName 'WhatsApp Reader MCP'
Start-ScheduledTask -TaskName 'WhatsApp Reader MCP'
```

Remover completamente a tarefa:

```powershell
Stop-ScheduledTask -TaskName 'WhatsApp Reader MCP' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'WhatsApp Reader MCP' -Confirm:$false
```

Remover ou desabilitar a tarefa não apaga o banco, `.env`, ambiente virtual ou código do projeto.

## Solução de problemas

### A tarefa inicia e encerra imediatamente

Normalmente o caminho está incorreto, uma dependência está ausente ou a porta está ocupada. Pare a tarefa e execute manualmente o comando da seção 1 para ver o erro completo no terminal.

Confira também a ação registrada:

```powershell
(Get-ScheduledTask -TaskName 'WhatsApp Reader MCP').Actions |
    Format-List Execute, Arguments, WorkingDirectory
```

### Banco errado ou vazio

Use caminho absoluto em `--db` e confirme que ele aponta para o banco importado:

```powershell
$project = (Resolve-Path .).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
$db = Join-Path $project 'whatsapp.sqlite3'
& $python -m whatsapp_reader query --db $db --limit 1
```

### Porta 8765 já está em uso

Descubra o processo que ocupa a porta:

```powershell
Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, State, OwningProcess

$processId = 1234  # substitua pelo valor de OwningProcess
Get-Process -Id $processId
```

Substitua `1234` pelo valor de `OwningProcess`. Pare o processo duplicado ou altere `--port` na tarefa e também a URL configurada no client.

### Funciona no terminal, mas não na tarefa

O Agendador não herda necessariamente as mesmas variáveis e o mesmo diretório do terminal. Confirme `WorkingDirectory` e use caminhos absolutos para o executável, banco e `.env`. Coloque as variáveis necessárias no `.env` em vez de depender do perfil do PowerShell.

Na variante de boot, confirme ainda que todos os arquivos estão em disco local e acessíveis pela conta `SYSTEM`.

### Código de resultado da tarefa

Consulte o valor decimal e sua forma hexadecimal:

```powershell
$result = (Get-ScheduledTaskInfo -TaskName 'WhatsApp Reader MCP').LastTaskResult
'Decimal: {0}; Hexadecimal: 0x{0:X8}' -f $result
```

Enquanto uma tarefa de longa duração está ativa, o Agendador pode mostrar um código indicando que a instância ainda está em execução. Use em conjunto o estado da tarefa, `Test-NetConnection` e o teste manual para diagnosticar.
