# z3nd3v

Локальный планировщик задач с веб-интерфейсом и встроенным AI агентом.

Запускает Python, Node.js, TypeScript, bat, PowerShell и exe скрипты по расписанию — cron, интервал, фиксированное время, или вручную. Выводит лог в реальном времени прямо в браузере.

---

## Быстрый старт

```bash
# 1. Установить зависимости
python setup.py

# 2. Запустить
python app.py
```

Открыть: [http://localhost:10993/scheduler.html](http://localhost:10993/scheduler.html)

---

## Требования

| Компонент | Версия |
|---|---|
| Python | ≥ 3.11 |
| Node.js | ≥ 18 |
| `@anthropic-ai/claude-code` | latest (для AI агента) |
| OmniRoute | запущен на `:20128` (для AI агента) |

---

## Конфиг (`config.py`)

```python
# База данных
DB_MODE = "sqlite"           # "sqlite" | "postgres"
DB_PATH = "data.db"          # путь к файлу (sqlite)
DB_DSN  = "postgresql://..."  # DSN (postgres)

# Порт веб-интерфейса
DASHBOARD_PORT = 10993
WWWROOT        = "wwwroot"

# Терминал (для кнопки "Open in Terminal")
TERMINAL      = "cmd"   # "cmd" | "powershell" | "gitbash" | "third_party"
TERMINAL_PATH = ""      # путь к exe (только для third_party)

# AI агент
AGENT_PORT        = 20129
CLAUDE_CLI_PATH   = r"C:\Users\...\claude-code\cli.js"
NODE_PATH         = r"C:\Program Files\nodejs\node.exe"
ANTHROPIC_AUTH_TOKEN = "sk-..."
ANTHROPIC_MODEL      = "kr/claude-sonnet-4.5"
OMNI_ROUTE_HOST   = "http://localhost:20128/"
```

---

## Исполнители (executor)

| Executor | Что запускает |
|---|---|
| `python` | `python script.py` |
| `node` | `node script.js` |
| `ts-node` | `ts-node script.ts` |
| `exe` | прямой запуск `.exe` |
| `bat` | `cmd.exe /c script.bat` |
| `bash` | `bash script.sh` |
| `ps1` | `powershell -File script.ps1` |
| `internal` | внутренняя Python функция |

**npm run** — для js/ts задач доступно через выпадающий список скриптов из `package.json`. Сохраняется в поле args как `__npm_run__<script>`.

---

## Расписание

| Режим | Описание |
|---|---|
| `OnDemand` | только вручную |
| Cron | стандартный cron (`0 * * * *`) |
| Interval | каждые N минут |
| Fixed time | каждый день в HH:MM |

---

## AI агент

Встроенный агент на базе Claude Code. Открывается кнопкой **⟡ AI** — появляется сайдбар справа, страница сжимается влево.

- **⟡ AI в header** — агент запускается в корне проекта
- **⟡ AI в карточке задачи** — агент запускается в папке скрипта

Агент имеет доступ к файлам, может выполнять bash команды, читать и редактировать файлы в рабочей директории.

Подробнее: [AI_AGENT.md](./AI_AGENT.md)

---

## SQLite Viewer

Открыть файл `.db` / `.sqlite` прямо в браузере. Просмотр таблиц, inline редактирование ячеек, удаление строк, сортировка, фильтрация колонок.

Доступен на любой странице через nav dock.

---

## Структура файлов

```
app.py                   — точка входа FastAPI
config.py                — все настройки
db.py                    — абстракция над SQLite / PostgreSQL
scheduler_service.py     — логика планировщика, SSE, процессы
scheduler_handler.py     — HTTP эндпоинты /scheduler/*
ai_handler.py            — HTTP эндпоинты /ai/*
sqlite_viewer_handler.py — HTTP эндпоинты /sqlite-viewer/*
config_handler.py        — GET/POST /config/ui (тема, позиция дока)
agent-service/
  index.js               — Node.js сервис, запускает claude cli
  package.json
setup.py                 — автонастройка
user_prefs.json          — тема и настройки интерфейса (создаётся автоматически)
data.db                  — база данных SQLite (создаётся автоматически)
wwwroot/
  js/
    ai-panel.js          — инжектируемая AI панель
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

## Переменные окружения

Все настройки через `config.py`. Переменные окружения не используются — это локальный инструмент.

---

## Установка `@anthropic-ai/claude-code`

```bash
npm install -g @anthropic-ai/claude-code
```

После установки запустить `python setup.py` — он найдёт пути автоматически и запишет их в `config.py`.
