# WhatsApp Reader

CLI local em Python + SQLite para importar arquivos `.txt` exportados do WhatsApp, consultar mensagens estruturadas e gerar contexto limpo para uso posterior com LLMs.

## Uso rapido

```bash
python -m whatsapp_reader import conversa.txt --chat "Nome do grupo"
python -m whatsapp_reader import "/home/techbrain/Documents/WhatsApp Chat with deepmesh - dev group.txt" --chat "deepmesh dev group"
python -m whatsapp_reader query --date 2026-07-20
python -m whatsapp_reader query --sender "Alice"
python -m whatsapp_reader query --from "2026-07-20 09:00" --to "2026-07-20 12:00"
python -m whatsapp_reader export-context --date 2026-07-20 --format json
```

Por padrao o banco fica em `whatsapp.sqlite3` no diretorio atual. Use `--db caminho.sqlite3` em qualquer comando para escolher outro arquivo.

Datas do TXT sao interpretadas como horario local no timezone `America/Sao_Paulo`, no formato `M/D/YY, HH:mm`.
