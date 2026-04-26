# AI Agent — How It Works

> Встроенный агент на базе Claude Code, доступный как сайдбар на любой странице приложения.

---

## Архитектура

```
Browser (SSE)
    │
    ▼
FastAPI  /ai/chat  (ai_handler.py)
    │  проксирует SSE
    ▼
Node.js agent-service  :20129  (agent-service/index.js)
    │  spawn() процесса
    ▼
Claude Code CLI  (cli.js)
    │  ANTHROPIC_BASE_URL
    ▼
OmniRoute  :20128  ──►  Claude API
```

Три слоя намеренно разделены:

| Слой | Язык | Причина |
|---|---|---|
| UI панель | JS | инжектируется на любую страницу без перезагрузки |
| Прокси | Python/FastAPI | единая точка входа, управление lifecycle |
| Agent runner | Node.js | `@anthropic-ai/claude-code` SDK существует только для Node |

---

## Компоненты

### `agent-service/index.js`

Express HTTP сервер. Слушает на `127.0.0.1:20129` (только localhost, не наружу).

При каждом запросе `/chat`:

1. Берёт `sessionId` из Map по `chatId` — если есть, передаёт `--resume <session_id>` в CLI
2. Спавнит `node cli.js` с нужными env переменными
3. Пишет сообщение в `stdin` процесса
4. Читает stdout построчно — каждая строка это JSON event
5. Парсит события и стримит их браузеру через SSE

**Почему `spawn(process.execPath, ...)`** — вместо `"node"` в PATH.  
Python запускает Node сервис с урезанным окружением без полного PATH.  
`process.execPath` — это абсолютный путь к `node.exe` которым запущен сам сервис, он всегда известен без поиска в PATH.

**Почему `cwd` резолвится в директорию** — Claude Code использует `cwd` как корень для файловых операций (Read, Bash, Grep). Если передать путь к файлу вместо папки — `spawn` падает с ENOENT.

### `ai_handler.py`

FastAPI router с префиксом `/ai`. Делает две вещи:

- При старте приложения — проверяет жив ли сервис (`/health`), если нет — пробует запустить
- Все запросы `/ai/chat` — проксирует как SSE поток от Node к браузеру без буферизации

**Почему не вызывать Claude напрямую из Python** — Claude Code SDK (`@anthropic-ai/claude-code`) существует только для Node.js. Он управляет сессиями, инструментами (Read/Bash/Grep/Write), форматом вывода. Переписать это на Python — отдельный большой проект.

### `ai-panel.js`

Самодостаточный модуль. Инжектируется через `<script src="/js/ai-panel.js">` на любую страницу.  
Не требует фреймворков. Создаёт DOM сам при первом вызове `AiPanel.open()`.

Сдвигает `body` через `margin-right: var(--ai-panel-w)` — все элементы страницы сжимаются влево, панель появляется справа. Ширина ресайзабельна, сохраняется в `localStorage`.

---

## Сессии

Сессия = один `sessionId` который возвращает Claude Code при первом сообщении (event `type: system, subtype: init`).

```
chat_id  →  session_id
```

`chat_id` генерируется на фронте (`"chat-" + random`). `session_id` приходит от CLI и хранится в памяти Node сервиса (Map). При перезапуске сервиса — все сессии теряются.

**`↺ New`** в панели — генерирует новый `chat_id`, старый `session_id` не передаётся, CLI стартует чистую сессию.

---

## Рабочая директория (CWD)

CWD определяет что агент "видит" по умолчанию — откуда он читает файлы, где запускает bash команды.

| Кнопка | CWD |
|---|---|
| `⟡ AI` в header | папка `app.py` (корень проекта) |
| `⟡ AI` в карточке задачи | папка скрипта задачи |
| После `↺ New` | сбрасывается на корень проекта |

CWD передаётся в каждом запросе: `POST /ai/chat { chatId, message, cwd }`.

---

## Контекст страницы

При **первом сообщении** в новой сессии к тексту автоматически добавляется контекст страницы:

```
<context>
Current scheduler task:
{
  "id": "...",
  "name": "test.dbridge",
  "executor": "ts-node",
  "script_path": "w:\\code_hard\\ts\\dbridge\\",
  ...
}
</context>

[сообщение пользователя]
```

Контекст регистрируется через `AiPanel.setContext(fn)` — каждая страница передаёт свои данные.

---

## Инструменты агента

Claude Code запускается с флагом `--dangerously-skip-permissions` — он не спрашивает разрешения на каждое действие.

Доступные инструменты (определяются самим Claude Code):

- **Read** — читать файлы по абсолютному пути
- **Bash** — выполнять shell команды в CWD
- **Grep** — поиск по файлам
- **Write / Edit** — запись и редактирование файлов
- **Glob** — поиск файлов по паттерну
- **WebFetch** — HTTP запросы

Агент **не** использует Anthropic API напрямую — все запросы идут через OmniRoute.

---

## OmniRoute

OmniRoute — локальный прокси который перехватывает запросы к Anthropic API и перенаправляет их через собственный роутинг (кастомные модели, балансировка, кеш).

Агент видит его как обычный Anthropic API:

```
ANTHROPIC_BASE_URL = http://localhost:20128/v1
```

`ANTHROPIC_API_KEY` намеренно пустой — аутентификация идёт через `ANTHROPIC_AUTH_TOKEN` который OmniRoute валидирует сам.

---

## Конфиг (`config.py`)

```python
# Порт Node сервиса (не OmniRoute)
AGENT_PORT = 20129

# Абсолютный путь к cli.js — npm global install
# where: %APPDATA%\npm\node_modules\@anthropic-ai\claude-code\cli.js
CLAUDE_CLI_PATH = r"C:\Users\<user>\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\cli.js"

# Абсолютный путь к node.exe
# Нужен потому что Python запускает Node с урезанным PATH
# where node → обычно C:\Program Files\nodejs\node.exe
NODE_PATH = r"C:\Program Files\nodejs\node.exe"

# Токен для OmniRoute (не Anthropic напрямую)
ANTHROPIC_AUTH_TOKEN = "sk-..."

# Модель через OmniRoute
ANTHROPIC_MODEL = "kr/claude-sonnet-4.5"
```

### Что захардкожено в `index.js`

- Порт `20129` — дефолт если `AGENT_PORT` не передан
- Fallback путь к `cli.js` через `USERNAME` env — используется только если `CLAUDE_CLI_PATH` не задан в конфиге
- Флаги CLI: `--output-format stream-json --print --dangerously-skip-permissions --verbose` — фиксированы, определяют формат общения с агентом

---

## Требования

| Компонент | Версия | Примечание |
|---|---|---|
| Node.js | ≥ 18 | для ESM (`"type": "module"`) |
| `@anthropic-ai/claude-code` | latest | `npm install -g @anthropic-ai/claude-code` |
| OmniRoute | — | должен слушать на `:20128` |
| Python | ≥ 3.11 | для `asyncpg` / `aiosqlite` |

---

## Первый запуск

```bash
# 1. Установить Claude Code глобально
npm install -g @anthropic-ai/claude-code

# 2. Установить зависимости agent-service
cd agent-service
npm install

# 3. Заполнить config.py
CLAUDE_CLI_PATH = r"C:\Users\<user>\AppData\Roaming\npm\..."
NODE_PATH       = r"C:\Program Files\nodejs\node.exe"
ANTHROPIC_AUTH_TOKEN = "sk-..."

# 4. Запустить
python app.py
```

При старте Python проверяет `/health` на порту `AGENT_PORT`. Если Node сервис уже запущен (например вручную или через задачу в планировщике) — просто подключается к нему. Если нет — запускает автоматически.

---

## Запуск agent-service как задачи в планировщике

Создать `agent-service/start.bat`:

```bat
@echo off
set AGENT_PORT=20129
set AGENT_CONFIG={"omniRouteHost":"http://localhost:20128/","authToken":"sk-...","model":"kr/claude-sonnet-4.5","claudeCli":"C:/Users/.../cli.js"}
node "%~dp0index.js"
```

В планировщике:
- **Executor**: `bat`
- **Script**: путь к `start.bat`
- **On Overlap**: `skip`

---

## SSE формат (браузер ↔ FastAPI ↔ Node)

```
event: delta
data: {"text": "фрагмент ответа"}

event: tool
data: {"name": "Read", "input": {"file_path": "/path/to/file"}}

event: done
data: {"sessionId": "abc123"}

event: error
data: {"error": "описание ошибки"}
```

`delta` события стримятся по мере генерации — текст появляется в панели посимвольно.  
`tool` события показывают какой инструмент использует агент в данный момент.  
`done` сигнализирует конец ответа и содержит `sessionId` для следующего сообщения.

---

## Переключение на официальный Anthropic API (без OmniRoute)

По умолчанию агент работает через OmniRoute — локальный прокси на `:20128`.  
Для переключения на прямой Anthropic API нужно изменить три файла.

### 1. `config.py`

```python
# Убрать OmniRoute
OMNI_ROUTE_HOST  = ""

# Добавить официальный ключ
ANTHROPIC_API_KEY    = "sk-ant-ваш-ключ"
ANTHROPIC_AUTH_TOKEN = ""  # не используется без OmniRoute

# Официальное имя модели (не kr/...)
ANTHROPIC_MODEL = "claude-sonnet-4-5"
```

### 2. `ai_handler.py`

В функцию `start_agent_service` добавить `apiKey` в `agent_cfg`:

```python
agent_cfg = json.dumps({
    "omniRouteHost": getattr(config, "OMNI_ROUTE_HOST", ""),
    "authToken":     getattr(config, "ANTHROPIC_AUTH_TOKEN", ""),
    "apiKey":        getattr(config, "ANTHROPIC_API_KEY", ""),   # ← добавить
    "model":         getattr(config, "ANTHROPIC_MODEL", "claude-sonnet-4-5"),
    "claudeCli":     getattr(config, "CLAUDE_CLI_PATH", ""),
    "nodePath":      getattr(config, "NODE_PATH", "node"),
})
```

### 3. `agent-service/index.js`

Заменить строку с `ANTHROPIC_BASE_URL`:

```js
// было:
agentEnv.ANTHROPIC_BASE_URL = (cfg.omniRouteHost || '...').replace(/\/$/, '') + '/v1'
agentEnv.ANTHROPIC_AUTH_TOKEN = cfg.authToken || ''
agentEnv.ANTHROPIC_API_KEY    = ''

// стало:
agentEnv.ANTHROPIC_BASE_URL   = cfg.omniRouteHost
  ? cfg.omniRouteHost.replace(/\/$/, '') + '/v1'
  : ''                                            // пустой = https://api.anthropic.com
agentEnv.ANTHROPIC_AUTH_TOKEN = cfg.authToken || ''
agentEnv.ANTHROPIC_API_KEY    = cfg.apiKey    || ''
```

Если `ANTHROPIC_BASE_URL` пустой — Claude Code использует `https://api.anthropic.com` по умолчанию.

### Итог

| Параметр | OmniRoute | Официальный API |
|---|---|---|
| `OMNI_ROUTE_HOST` | `http://localhost:20128/` | `""` |
| `ANTHROPIC_AUTH_TOKEN` | токен OmniRoute | `""` |
| `ANTHROPIC_API_KEY` | `""` | `sk-ant-...` |
| `ANTHROPIC_MODEL` | `kr/claude-sonnet-4.5` | `claude-sonnet-4-5` |
| `ANTHROPIC_BASE_URL` (в env) | `http://localhost:20128/v1` | `""` (дефолт Anthropic) |
