"""
setup.py — automated project setup

Does:
  1. Check / install @anthropic-ai/claude-code globally
  2. npm install in agent-service/
  3. Detect node.exe path
  4. Detect cli.js path
  5. Check OmniRoute availability
  6. Install Python dependencies
  7. Write detected values to config.py
  8. Initialize DB tables
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent

# ── Colors ────────────────────────────────────────────────────────────────────

def green(s):  return f"\033[92m{s}\033[0m"
def red(s):    return f"\033[91m{s}\033[0m"
def yellow(s): return f"\033[93m{s}\033[0m"
def cyan(s):   return f"\033[96m{s}\033[0m"
def bold(s):   return f"\033[1m{s}\033[0m"

def ok(msg):   print(f"  {green('✓')} {msg}")
def fail(msg): print(f"  {red('✗')} {msg}")
def warn(msg): print(f"  {yellow('!')} {msg}")
def info(msg): print(f"  {cyan('·')} {msg}")
def step(msg): print(f"\n{bold(msg)}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def run(cmd, cwd=None, capture=True):
    """Run command, return (returncode, stdout, stderr)."""
    r = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=capture,
        text=True,
        shell=(sys.platform == "win32"),
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def find_node() -> str | None:
    """Return absolute path to node.exe / node binary."""
    found = shutil.which("node")
    if found:
        return str(Path(found).resolve())
    # Windows common locations
    candidates = [
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def find_npm() -> str | None:
    found = shutil.which("npm")
    if found:
        return found
    # On Windows npm is a .cmd
    candidates = [
        r"C:\Program Files\nodejs\npm.cmd",
        r"C:\Program Files (x86)\nodejs\npm.cmd",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def find_claude_cli() -> str | None:
    """Find cli.js from @anthropic-ai/claude-code npm global install."""
    # npm global prefix
    rc, out, _ = run(["npm", "root", "-g"])
    if rc == 0 and out:
        cli = Path(out.strip()) / "@anthropic-ai" / "claude-code" / "cli.js"
        if cli.exists():
            return str(cli)

    # fallback: common Windows path
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        cli = Path(appdata) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        if cli.exists():
            return str(cli)

    # Linux / Mac
    home = Path.home()
    for prefix in [
        home / ".npm-global" / "lib" / "node_modules",
        Path("/usr/local/lib/node_modules"),
        Path("/usr/lib/node_modules"),
    ]:
        cli = prefix / "@anthropic-ai" / "claude-code" / "cli.js"
        if cli.exists():
            return str(cli)

    return None


def check_omni_route(host: str) -> bool:
    import urllib.request
    try:
        url = host.rstrip("/") + "/v1/models"
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status < 400
    except Exception:
        return False


def read_config() -> dict:
    cfg = {}
    config_file = ROOT / "config.py"
    if not config_file.exists():
        return cfg
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("config", config_file)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for k in dir(mod):
            if not k.startswith("_"):
                cfg[k] = getattr(mod, k)
    except Exception:
        pass
    return cfg


def write_config_values(updates: dict):
    """Patch specific values in config.py without rewriting the whole file."""
    config_file = ROOT / "config.py"
    if not config_file.exists():
        warn("config.py not found — skipping write")
        return

    content = config_file.read_text(encoding="utf-8")

    for key, value in updates.items():
        import re
        # value as python literal
        if isinstance(value, str):
            lit = 'r"' + value.replace('"', '\\"') + '"'
        elif isinstance(value, int):
            lit = str(value)
        else:
            lit = repr(value)

        # replace existing line
        pattern = rf'^({re.escape(key)}\s*=\s*).*$'
        new_line = f'{key} = {lit}'
        replaced, n = re.subn(pattern, new_line, content, flags=re.MULTILINE)
        if n:
            content = replaced
        else:
            # append
            content += f'\n{new_line}\n'

    config_file.write_text(content, encoding="utf-8")


# ── Steps ─────────────────────────────────────────────────────────────────────

def check_python():
    step("Python")
    v = sys.version_info
    if v >= (3, 11):
        ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        warn(f"Python {v.major}.{v.minor} — recommended 3.11+")


def check_node():
    step("Node.js")
    node = find_node()
    if not node:
        fail("node not found — install from https://nodejs.org")
        return None
    rc, out, _ = run([node, "--version"])
    ok(f"node: {out}  →  {node}")
    return node


def check_npm():
    step("npm")
    npm = find_npm()
    if not npm:
        fail("npm not found")
        return None
    rc, out, _ = run([npm, "--version"])
    ok(f"npm {out}  →  {npm}")
    return npm


def install_claude_code(npm: str) -> str | None:
    step("@anthropic-ai/claude-code")
    cli = find_claude_cli()
    if cli:
        ok(f"already installed  →  {cli}")
        return cli

    info("not found — installing globally...")
    rc, out, err = run([npm, "install", "-g", "@anthropic-ai/claude-code"], capture=False)
    if rc != 0:
        fail(f"npm install failed (exit {rc})")
        return None

    cli = find_claude_cli()
    if cli:
        ok(f"installed  →  {cli}")
        return cli
    else:
        fail("installed but cli.js not found — check npm global prefix")
        return None


def install_agent_service_deps(npm: str):
    step("agent-service dependencies")
    agent_dir = ROOT / "agent-service"
    if not agent_dir.exists():
        fail(f"agent-service/ not found at {agent_dir}")
        return

    pkg = agent_dir / "package.json"
    if not pkg.exists():
        fail("agent-service/package.json missing")
        return

    nm = agent_dir / "node_modules"
    if nm.exists():
        ok("node_modules already exists — skipping")
        return

    info("running npm install...")
    rc, _, err = run([npm, "install"], cwd=agent_dir, capture=False)
    if rc == 0:
        ok("npm install done")
    else:
        fail(f"npm install failed: {err}")


def install_python_deps():
    step("Python dependencies")
    req = ROOT / "requirements.txt"
    if not req.exists():
        warn("requirements.txt not found")
        return

    info("pip install -r requirements.txt...")
    rc, _, err = run(
        [sys.executable, "-m", "pip", "install", "-r", str(req), "--break-system-packages"],
        capture=False,
    )
    if rc == 0:
        ok("Python deps installed")
    else:
        fail(f"pip failed: {err}")


def check_omniRoute():
    step("OmniRoute")
    cfg = read_config()
    host = cfg.get("OMNI_ROUTE_HOST", "http://localhost:20128/")
    info(f"checking {host} ...")
    if check_omni_route(host):
        ok(f"OmniRoute is reachable at {host}")
    else:
        warn(f"OmniRoute not reachable at {host} — start it before using AI agent")


def prompt_auth_token() -> str | None:
    step("Anthropic Auth Token")
    cfg = read_config()
    existing = cfg.get("ANTHROPIC_AUTH_TOKEN", "")
    if existing and not existing.startswith("sk-your"):
        ok(f"token already set: {existing[:12]}...")
        return existing

    print()
    token = input("  Enter ANTHROPIC_AUTH_TOKEN (or press Enter to skip): ").strip()
    if token:
        ok("token accepted")
        return token
    else:
        warn("skipped — set ANTHROPIC_AUTH_TOKEN in config.py manually")
        return None


def check_db():
    step("Database")
    cfg = read_config()
    mode = cfg.get("DB_MODE", "sqlite")
    if mode == "sqlite":
        path = ROOT / cfg.get("DB_PATH", "data.db")
        if path.exists():
            ok(f"SQLite DB exists: {path}")
        else:
            ok(f"SQLite DB will be created on first run: {path}")
    elif mode == "postgres":
        dsn = cfg.get("DB_DSN", "")
        info(f"PostgreSQL mode — DSN: {dsn}")
        info("Tables will be created automatically on first run")


def print_summary(node: str | None, cli: str | None):
    step("Summary")
    cfg = read_config()
    print()
    rows = [
        ("NODE_PATH",          node or red("NOT FOUND")),
        ("CLAUDE_CLI_PATH",    cli  or red("NOT FOUND")),
        ("AGENT_PORT",         str(cfg.get("AGENT_PORT", 20129))),
        ("OMNI_ROUTE_HOST",    cfg.get("OMNI_ROUTE_HOST", "")),
        ("ANTHROPIC_MODEL",    cfg.get("ANTHROPIC_MODEL", "")),
        ("DB_MODE",            cfg.get("DB_MODE", "")),
        ("DASHBOARD_PORT",     str(cfg.get("DASHBOARD_PORT", 10993))),
    ]
    for k, v in rows:
        print(f"  {cyan(k):<30} {v}")

    print()
    if node and cli:
        print(green("  Setup complete. Run:  python app.py"))
    else:
        print(yellow("  Setup incomplete — fix issues above, then run:  python app.py"))
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print()
    print(bold("=" * 52))
    print(bold("  z3nIO — AI Agent Setup"))
    print(bold("=" * 52))

    check_python()
    node = check_node()
    npm  = check_npm()

    cli = None
    if npm:
        cli = install_claude_code(npm)
        install_agent_service_deps(npm)

    install_python_deps()
    check_omniRoute()
    token = prompt_auth_token()
    check_db()

    # write detected values to config.py
    updates = {}
    if node:
        updates["NODE_PATH"] = node
    if cli:
        updates["CLAUDE_CLI_PATH"] = cli
    if token:
        updates["ANTHROPIC_AUTH_TOKEN"] = token

    if updates:
        step("Writing config.py")
        write_config_values(updates)
        for k, v in updates.items():
            ok(f"{k} = {v[:60] if isinstance(v, str) else v}")

    print_summary(node, cli)


if __name__ == "__main__":
    main()
