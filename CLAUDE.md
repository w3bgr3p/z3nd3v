# CLAUDE.md — API Reference for AI Agent

---

## CRITICAL: User Request Confirmation Protocol

**MANDATORY RULE: Before executing ANY user request, you MUST:**

1. **Restate in your own words** what you understood from the user's request
2. **Explain your planned approach** - what you're going to do and how
3. **Wait for explicit confirmation** from the user (e.g., "yes", "ok", "proceed", "correct")
4. **Only after confirmation** - proceed with making changes

**Example:**

User: "добавь логирование в функцию process_data"
Assistant response: "Я понял, что нужно:
- Добавить логирование в функцию process_data
- Вероятно, логировать входные параметры, ошибки и результат работы

Мой план:
1. Найду функцию process_data в коде
2. Добавлю import logging в начало файла (если его нет)
3. Добавлю логи: начало выполнения, входные данные, ошибки (если есть), результат
4. Использую уровни INFO для обычных операций и ERROR для ошибок

Правильно я понял? Можно приступать?"

User: "да, давай"# CLAUDE.md — API Reference for AI Agent

This file describes the z3nIO HTTP API available to the Claude Code agent.
Use it to interact with the task scheduler, read output, manage tasks, and query the database.

---

## Base URL

```
http://localhost:{DASHBOARD_PORT}
```

Default port: **10993**. Check `config.py → DASHBOARD_PORT` for the actual value.

---

## Scheduler API — `/scheduler/*`

### List all tasks

```
GET /scheduler/list
```

Returns array of all schedule objects. Key fields:

| Field | Type | Description |
|---|---|---|
| `id` | string (UUID) | unique task ID |
| `name` | string | display name |
| `executor` | string | `python`, `node`, `ts-node`, `exe`, `bat`, `bash`, `ps1` |
| `script_path` | string | absolute path to script or folder |
| `args` | string | CLI arguments, or `__npm_run__<script>` for npm scripts |
| `enabled` | string | `"true"` / `"false"` |
| `status` | string | `idle`, `running`, `done`, `fail`, `paused` |
| `cron` | string | cron expression, e.g. `0 * * * *` |
| `interval_minutes` | string | interval in minutes (`"0"` = disabled) |
| `fixed_time` | string | daily time `HH:MM` (`""` = disabled) |
| `on_overlap` | string | `skip`, `parallel`, `queue`, `restart` |
| `last_run` | string | ISO datetime of last run |
| `last_exit` | string | exit code of last run |
| `last_output` | string | last output text |
| `runs_total` | string | total run count |
| `runs_success` | string | successful run count |
| `schedule_tag` | string | tag used for log filtering |
| `terminal_override` | string | per-task terminal override |
| `terminal_init_cmd` | string | command to run in terminal after cd |

---

### Run a task now

```
POST /scheduler/run
Content-Type: application/json

{"id": "<task-id>"}
```

Response: `{"ok": true, "id": "<task-id>"}`

---

### Stop a running task

```
POST /scheduler/stop
Content-Type: application/json

{"id": "<task-id>"}
```

Response: `{"ok": true}`

---

### Save / create a task

```
POST /scheduler/save
Content-Type: application/json

{
  "id": "<existing-uuid-or-omit-to-create>",
  "name": "My Task",
  "executor": "python",
  "script_path": "C:/path/to/script.py",
  "args": "",
  "enabled": "true",
  "cron": "",
  "interval_minutes": "0",
  "fixed_time": "",
  "on_overlap": "skip"
}
```

Omit `id` to create new. Include `id` to update existing.

Response: `{"ok": true, "id": "<uuid>"}`

---

### Delete a task

```
POST /scheduler/delete
Content-Type: application/json

{"id": "<task-id>"}
```

Response: `{"ok": true}`

---

### Get task output (last saved)

```
GET /scheduler/output?id=<task-id>
```

Response:
```json
{
  "id": "...",
  "status": "done",
  "isLive": false,
  "output": "...",
  "result": ""
}
```

---

### Stream output (SSE, live)

```
GET /scheduler/output/stream?id=<task-id>
```

Server-Sent Events stream. Each event:

```
event: output
data: {"line": "...", "level": "INFO", "replace_last": false, "done": false}
```

`replace_last: true` — overwrite previous line (progress bars).  
`done: true` — task finished.

---

### Get process stats (running task)

```
GET /scheduler/process-stats?id=<task-id>
```

Response:
```json
{
  "pid": 1234,
  "cpu": 12.5,
  "mem_mb": 45.2,
  "threads": 4,
  "status": "running"
}
```

---

### List running instances

```
GET /scheduler/instances?id=<task-id>
```

Returns array of active process instances for the task.

---

### Kill a specific instance

```
POST /scheduler/kill-instance
Content-Type: application/json

{"id": "<task-id>", "pid": 1234}
```

---

### Get queue

```
GET /scheduler/queue
```

Returns pending queued runs.

---

### Clear queue

```
POST /scheduler/clear-queue
Content-Type: application/json

{"id": "<task-id>"}
```

---

### Get / set payload schema and values

```
GET /scheduler/payload?id=<task-id>
```

```
POST /scheduler/payload
Content-Type: application/json

{
  "id": "<task-id>",
  "schema": "[{\"key\":\"param1\",\"type\":\"string\",\"label\":\"Param 1\"}]",
  "values": "{\"param1\": \"value\"}"
}
```

---

### Read config file (config.json / config.py / package.json)

```
GET /scheduler/config-file?id=<task-id>&type=config
```

`type`: `config` (config.json), `py` (config.py), `package` (package.json)

Response: `{"content": "...", "path": "..."}`

---

### Write config file

```
POST /scheduler/config-file?id=<task-id>&type=config
Content-Type: application/json

{"content": "..."}
```

---

### Scan task folder (detect project type)

```
GET /scheduler/scan-folder?id=<task-id>
```

Response:
```json
{
  "hasConfig": true,
  "hasPackage": true,
  "hasRequirements": false,
  "packageScripts": ["start", "build", "dev"]
}
```

---

### Get npm / package scripts

```
GET /scheduler/package-scripts?id=<task-id>
```

---

### Clear task output

```
POST /scheduler/clear-output
Content-Type: application/json

{"id": "<task-id>"}
```

---

### Open file / folder in OS

```
GET /scheduler/open-file?id=<task-id>
GET /scheduler/open-folder?id=<task-id>
```

Opens in the default OS application.

---

### Open terminal in task folder

```
GET /scheduler/open-terminal?id=<task-id>
```

Opens terminal configured in `config.py → TERMINAL`.

---

### Terminal config

```
GET /scheduler/terminal-config
```

Response:
```json
{
  "terminal": "cmd",
  "terminal_path": "",
  "gitbash_found": "C:/Program Files/Git/bin/bash.exe"
}
```

---

## AI Agent API — `/ai/*`

### Send message to agent (SSE stream)

```
POST /ai/chat
Content-Type: application/json

{
  "chatId": "chat-abc123",
  "message": "What files are in this directory?",
  "cwd": "W:/code_hard/py/myproject"
}
```

Returns SSE stream:

```
event: delta
data: {"text": "Here are the files..."}

event: tool
data: {"name": "Read", "input": {"file_path": "..."}}

event: done
data: {"sessionId": "session-xyz"}

event: error
data: {"error": "..."}
```

`chatId` — arbitrary string, identifies the session.  
`cwd` — working directory for the agent. If it points to a file, parent directory is used.  
`sessionId` from `done` — pass as `chatId` on next call to continue the session (handled automatically by the UI).

---

### Delete session

```
DELETE /ai/session/<chatId>
```

Clears the session so next message starts fresh.

---

### List active sessions

```
GET /ai/sessions
```

---

### Get default CWD (project root)

```
GET /ai/cwd
```

Response: `{"cwd": "W:/code_hard/py/z3nIOpy"}`

---

### Agent health check

```
GET /ai/health
```

Response: `{"ok": true, "agent": {"ok": true}}`

---

## SQLite Viewer API — `/sqlite-viewer/*`

All endpoints accept raw SQLite file bytes as the POST body (`Content-Type: application/octet-stream`).

### List tables

```
POST /sqlite-viewer/tables
Body: <raw sqlite bytes>
```

Response: `{"tables": ["table1", "table2"]}`

---

### Query

```
POST /sqlite-viewer/query?sql=SELECT+*+FROM+table
Body: <raw sqlite bytes>
```

Response:
```json
{
  "columns": ["id", "name", "status"],
  "rows": [["1", "task", "done"]],
  "rowids": [1],
  "table": "table_name"
}
```

Only `SELECT` and `WITH` are allowed.

---

### Update cell

```
POST /sqlite-viewer/update?table=T&rowid=1&col=name&value=new_value
Body: <raw sqlite bytes>
```

Returns updated file bytes (`application/octet-stream`).

---

### Delete row

```
POST /sqlite-viewer/delete?table=T&rowid=1
Body: <raw sqlite bytes>
```

Returns updated file bytes (`application/octet-stream`).

---

## UI Preferences — `/config/ui`

### Get preferences

```
GET /config/ui
```

Response:
```json
{
  "theme": "dark",
  "dockPosition": "bottom"
}
```

### Save preferences

```
POST /config/ui
Content-Type: application/json

{"theme": "tokyo", "dockPosition": "left"}
```

Response: merged preferences object.

Available themes: `dark`, `light`, `hyper`, `tokyo`, `gruvbox`, `nord`, `amber`, `amoled`, `ethereum`, `optimism`, `arbitrum`, `polygon`, `base`, `solana`, `avalanche`, `bnb`, `sui`, `blast`

Dock positions: `bottom`, `top`, `left`, `right`

---

## Finding the port

1. Check `config.py → DASHBOARD_PORT` (default: `10993`)
2. Or read it at runtime:

```bash
python -c "import config; print(config.DASHBOARD_PORT)"
```

---

## Typical agent workflows

**List all tasks and find a failing one:**
```
GET /scheduler/list
→ filter by status == "fail", read last_output
```

**Run a task and stream its output:**
```
POST /scheduler/run {"id": "..."}
GET /scheduler/output/stream?id=...
```

**Read and edit a task's config.json:**
```
GET /scheduler/config-file?id=...&type=config
POST /scheduler/config-file?id=...&type=config {"content": "..."}
```

**Check if a task is currently running:**
```
GET /scheduler/list → check status == "running"
GET /scheduler/process-stats?id=...
```
