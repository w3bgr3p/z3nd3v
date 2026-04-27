# VibeIO

[Русская версия](./README_RU.md) | English

A local task scheduler with web interface and built-in AI agent.

Runs Python, Node.js, TypeScript, bat, PowerShell, and exe scripts on schedule — cron, interval, fixed time, or manually. Streams logs in real-time directly in the browser.

---

## Quick Start

```bash
# 1. Install dependencies
python setup.py

# 2. Run
python app.py
```

Open: [http://localhost:10993/scheduler.html](http://localhost:10993/scheduler.html)

---

## Requirements

| Component | Version |
|---|---|
| Python | ≥ 3.11 |
| Node.js | ≥ 18 |
| `@anthropic-ai/claude-code` | latest (for AI agent) |
| OmniRoute | running on `:20128` (for AI agent) |

---

## Configuration (`config.py`)

```python
# Database
DB_MODE = "sqlite"           # "sqlite" | "postgres"
DB_PATH = "data.db"          # file path (sqlite)
DB_DSN  = "postgresql://..."  # DSN (postgres)

# Web interface port
DASHBOARD_PORT = 10993
WWWROOT        = "wwwroot"

# Terminal (for "Open in Terminal" button)
TERMINAL      = "cmd"   # "cmd" | "powershell" | "gitbash" | "third_party"
TERMINAL_PATH = ""      # path to exe (third_party only)

# AI agent
AGENT_PORT        = 20129
CLAUDE_CLI_PATH   = r"C:\Users\...\claude-code\cli.js"
NODE_PATH         = r"C:\Program Files\nodejs\node.exe"
ANTHROPIC_AUTH_TOKEN = "sk-..."
ANTHROPIC_MODEL      = "kr/claude-sonnet-4.5"
OMNI_ROUTE_HOST   = "http://localhost:20128/"
```

---

## Executors

| Executor | Runs |
|---|---|
| `python` | `python script.py` |
| `node` | `node script.js` |
| `ts-node` | `ts-node script.ts` |
| `exe` | direct `.exe` execution |
| `bat` | `cmd.exe /c script.bat` |
| `bash` | `bash script.sh` |
| `ps1` | `powershell -File script.ps1` |
| `internal` | internal Python function |

**npm run** — for js/ts tasks, available via dropdown list of scripts from `package.json`. Saved in args field as `__npm_run__<script>`.

---

## Scheduling

| Mode | Description |
|---|---|
| `OnDemand` | manual only |
| Cron | standard cron (`0 * * * *`) |
| Interval | every N minutes |
| Fixed time | daily at HH:MM |

---

## AI Agent

Built-in agent powered by Claude Code. Opens via **⟡ AI** button — sidebar appears on the right, page compresses to the left.

- **⟡ AI in header** — agent starts in project root
- **⟡ AI in task card** — agent starts in script folder

The agent has access to files, can execute bash commands, read and edit files in the working directory.

Learn more: [AI_AGENT.md](./AI_AGENT.md)

---

## SQLite Viewer

Open `.db` / `.sqlite` files directly in the browser. View tables, inline cell editing, delete rows, sorting, column filtering.

Available on any page via nav dock.

---

## File Structure

```
app.py                   — FastAPI entry point
config.py                — all settings
db.py                    — SQLite / PostgreSQL abstraction
scheduler_service.py     — scheduler logic, SSE, processes
scheduler_handler.py     — HTTP endpoints /scheduler/*
ai_handler.py            — HTTP endpoints /ai/*
sqlite_viewer_handler.py — HTTP endpoints /sqlite-viewer/*
config_handler.py        — GET/POST /config/ui (theme, dock position)
agent-service/
  index.js               — Node.js service, runs claude cli
  package.json
setup.py                 — auto-setup
user_prefs.json          — theme and UI settings (auto-created)
data.db                  — SQLite database (auto-created)
wwwroot/
  js/
    ai-panel.js          — injectable AI panel
    scheduler.js
    nav.js
    theme.js
    icons.js
  css/
    themes.css
  scheduler.html
  sqlite.html
```

---

## Environment Variables

All settings are in `config.py`. Environment variables are not used — this is a local tool.

---

## Installing `@anthropic-ai/claude-code`

```bash
npm install -g @anthropic-ai/claude-code
```

After installation, run `python setup.py` — it will find paths automatically and write them to `config.py`.

---

## Features

### Task Management
- Create, edit, delete, and run tasks
- Multiple scheduling modes (cron, interval, fixed time)
- Real-time log streaming via SSE
- Process monitoring (CPU, memory, threads)
- Queue management for overlapping runs
- Task-specific terminal configuration
- npm script integration for Node.js projects

### AI Agent Integration
- Claude Code-powered assistant
- Context-aware (knows current task/page)
- File system access (read, write, edit)
- Bash command execution
- Session persistence
- Streaming responses

### SQLite Viewer
- In-browser database viewer
- Table browsing and querying
- Inline cell editing
- Row deletion
- Column filtering and sorting

### UI/UX
- Multiple themes (dark, light, tokyo, gruvbox, nord, etc.)
- Resizable AI panel
- Configurable dock position
- Responsive design
- Real-time updates

---

## API Reference

See [CLAUDE.md](./CLAUDE.md) for complete HTTP API documentation.

---

## License

MIT
