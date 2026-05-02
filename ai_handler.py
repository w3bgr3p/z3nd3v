"""
AI Agent handler.

Routes:
  POST /ai/chat          body: {chatId, message, cwd?} → SSE stream
  DELETE /ai/session/{chatId}
  GET  /ai/sessions
  GET  /ai/health

Lifecycle:
  start_agent_service()  — called on app startup
  stop_agent_service()   — called on app shutdown
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

import config

router = APIRouter(prefix="/ai")

_agent_proc: Optional[subprocess.Popen] = None
_agent_base  = f"http://127.0.0.1:{getattr(config, 'AGENT_PORT', 20129)}"
_agent_dir   = Path(__file__).parent / "agent-service"


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def _kill_port(port: int):
    """Kill whatever process is listening on port using psutil."""
    import psutil
    import signal
    killed = []
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            for conn in proc.net_connections(kind='tcp'):
                if conn.laddr.port == port and conn.status == 'LISTEN':
                    proc.kill()
                    killed.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if killed:
        print(f"[ai] killed stale process(es) on port {port}: {killed}")

import psutil
for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
    try:
        for conn in proc.net_connections(kind='tcp'):
            if conn.laddr.port == 20129:
                print(f"[ai] port 20129 held by PID {proc.pid} name={proc.name()} cmd={proc.cmdline()[:2]}")
    except Exception:
        pass

async def start_agent_service():
    """Check if agent-service running, try to start if not."""
    # First check if already running (e.g. started manually)
    try:
        async with httpx.AsyncClient(timeout=2) as http:
            r = await http.get(f"{_agent_base}/health")
            if r.status_code == 200:
                print(f"[ai] agent-service already running on {_agent_base}")
                return
    except Exception:
        pass

    # Kill stale process holding the port
    _kill_port(getattr(config, "AGENT_PORT", 20129))
    await asyncio.sleep(0.5)

    if not _agent_dir.exists():
        print(f"[ai] agent-service dir not found: {_agent_dir}")
        return

    agent_cfg = json.dumps({
        "omniRouteHost": getattr(config, "OMNI_ROUTE_HOST", "http://localhost:20128/"),
        "authToken":     getattr(config, "ANTHROPIC_AUTH_TOKEN", ""),
        "model":         getattr(config, "ANTHROPIC_MODEL", "kr/claude-sonnet-4.5"),
        "claudeCli":     getattr(config, "CLAUDE_CLI_PATH", ""),
        "nodePath":      getattr(config, "NODE_PATH", "node"),
    })

    env = os.environ.copy()
    env["AGENT_PORT"]   = str(getattr(config, "AGENT_PORT", 20129))
    env["AGENT_CONFIG"] = agent_cfg
    sys32 = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32")
    if sys32.lower() not in env.get("PATH", "").lower():
        env["PATH"] = env.get("PATH", "") + ";" + sys32

    node_cmd = getattr(config, "NODE_PATH", "node")
    print(f"[ai] starting agent-service: {node_cmd}")

    global _agent_proc
    try:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        _agent_proc = subprocess.Popen(
            [node_cmd, "index.js"],
            cwd=str(_agent_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    except Exception as ex:
        print(f"[ai] failed to start agent-service: {ex}")
        print(f"[ai] start manually: cd {_agent_dir} && node index.js")
        return

    for _ in range(20):
        await asyncio.sleep(0.5)
        try:
            async with httpx.AsyncClient(timeout=1) as http:
                r = await http.get(f"{_agent_base}/health")
                if r.status_code == 200:
                    print(f"[ai] agent-service ready on {_agent_base}")
                    asyncio.create_task(_pipe_output())
                    return
        except Exception:
            pass

    print(f"[ai] agent-service did not start — run manually:")
    print(f"[ai]   cd {_agent_dir} && node index.js")


async def _pipe_output():
    if not _agent_proc or not _agent_proc.stdout:
        return
    loop = asyncio.get_event_loop()
    while True:
        line = await loop.run_in_executor(None, _agent_proc.stdout.readline)
        if not line:
            break
        print(f"[agent-service] {line.decode('utf-8', errors='replace').rstrip()}")


def stop_agent_service():
    global _agent_proc
    if _agent_proc:
        _agent_proc.terminate()
        try:
            _agent_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _agent_proc.kill()
        _agent_proc = None
        print("[ai] agent-service stopped")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/chat")
async def chat(request: Request):
    body = await request.json()
    if not body.get("chatId") or not body.get("message"):
        return JSONResponse({"error": "chatId and message required"}, status_code=400)

    if not body.get("cwd"):
        body["cwd"] = str(Path(__file__).parent)

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=None) as http:
                async with http.stream(
                    "POST",
                    f"{_agent_base}/chat",
                    json=body,
                    headers={"Accept": "text/event-stream"},
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        except Exception as ex:
            yield f"event: error\ndata: {json.dumps({'error': str(ex)})}\n\n".encode()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.delete("/session/{chat_id}")
async def delete_session(chat_id: str):
    async with httpx.AsyncClient(timeout=5) as http:
        r = await http.delete(f"{_agent_base}/session/{chat_id}")
    return JSONResponse(r.json())


@router.get("/sessions")
async def sessions():
    async with httpx.AsyncClient(timeout=5) as http:
        r = await http.get(f"{_agent_base}/sessions")
    return JSONResponse(r.json())


@router.get("/cwd")
async def get_default_cwd():
    """Return the app root directory (where app.py lives)."""
    return JSONResponse({"cwd": str(Path(__file__).parent)})


@router.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=2) as http:
            r = await http.get(f"{_agent_base}/health")
        return JSONResponse({"ok": True, "agent": r.json()})
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)})


@router.post("/interrupt")
async def interrupt(request: Request):
    """Interrupt active agent process for given chatId."""
    body = await request.json()
    chat_id = body.get("chatId")
    if not chat_id:
        return JSONResponse({"error": "chatId required"}, status_code=400)

    try:
        async with httpx.AsyncClient(timeout=5) as http:
            r = await http.post(f"{_agent_base}/interrupt", json={"chatId": chat_id})
        return JSONResponse(r.json())
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)


@router.get("/providers")
async def get_providers():
    """Get available AI providers and models from omniroute."""
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            # Get providers
            providers_r = await http.get("http://localhost:20128/api/providers")
            providers_data = providers_r.json()

            # Get models
            models_r = await http.get("http://localhost:20128/api/models")
            models_data = models_r.json()

        return JSONResponse({
            "providers": providers_data.get("connections", []),
            "models": models_data.get("models", [])
        })
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)

