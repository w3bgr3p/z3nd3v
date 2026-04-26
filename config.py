DB_MODE = "sqlite"  # "sqlite" | "postgres"
DB_PATH = "data.db"  # only for sqlite
DB_DSN = "postgresql://postgres:baracuda69@localhost/postgres"

DASHBOARD_PORT = 10993
WWWROOT = "wwwroot"
# AI
AI_PROVIDER = "omniroute"  # "aiio" | "omniroute"
OMNI_ROUTE_HOST = "http://localhost:20128/"


AGENT_PORT = 20129
ANTHROPIC_AUTH_TOKEN = "sk-your-token-here"
ANTHROPIC_MODEL = "kr/claude-sonnet-4.5"
CLAUDE_CLI_PATH = (
    r"C:\Users\l3gi0n\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\cli.js"
)
NODE_PATH = r"C:\Program Files\nodejs\node.exe"


# Terminal
TERMINAL = "cmd"  # "cmd" | "powershell" | "gitbash" | "third_party"
TERMINAL_PATH = "C:/Users/l3gi0n/AppData/Local/Programs/Hyper/Hyper.exe"  # only for third_party, e.g. "C:/Users/.../Hyper.exe"
