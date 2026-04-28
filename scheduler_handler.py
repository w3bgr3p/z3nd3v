import asyncio
import json
import os
import platform
import subprocess
import sys
import config
from typing import Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from scheduler_service import SchedulerService, TABLE, QUEUE_TABLE, sse_subscribe, sse_unsubscribe

router = APIRouter(prefix="/scheduler")

# Set by app startup: pool and service instances
_pool = None
_service: Optional[SchedulerService] = None
_wwwroot: str = "wwwroot"

COLUMNS = [
    "id", "name", "executor", "script_path", "args", "enabled",
    "cron", "interval_minutes", "fixed_time", "on_overlap", "max_threads",
    "status", "last_run", "last_exit", "last_output",
    "payload_schema", "payload_values",
    "runs_total", "runs_success", "schedule_tag", "last_run_id",
    "terminal_override", "terminal_init_cmd",
    "trigger_on_success", "trigger_on_fail",
]


def setup(pool, service: SchedulerService, wwwroot: str):
    global _pool, _service, _wwwroot
    _pool    = pool
    _service = service
    _wwwroot = wwwroot


def _json(data) -> JSONResponse:
    return JSONResponse(content=data)


# ── Page ──────────────────────────────────────────────────────────────────────

@router.get("")
@router.get("/")
async def serve_page():
    path = os.path.join(_wwwroot, "scheduler.html")
    if not os.path.exists(path):
        return Response(content=f"scheduler.html not found at {path}", status_code=404)
    with open(path, "rb") as f:
        return Response(content=f.read(), media_type="text/html; charset=utf-8")


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/list")
async def list_schedules():
    async with _pool.acquire() as conn:
        cols = await _existing_columns(conn, TABLE)
        if not cols:
            return _json([])
        rows = await conn.fetch(
            f'SELECT {_cols_sql(cols)} FROM "{TABLE}" WHERE "id" != \'\''
        )
        return _json([dict(r) for r in rows])


# ── Save ──────────────────────────────────────────────────────────────────────

@router.post("/save")
async def save_schedule(request: Request):
    body = await request.json()
    sid = body.get("id") or ""
    if not sid:
        import uuid
        sid = str(uuid.uuid4())

    writable = [c for c in COLUMNS if c not in ("id", "status", "last_run", "last_exit", "last_output")]
    record = {"id": sid}
    for col in writable:
        if col in body:
            record[col] = str(body[col]) if body[col] is not None else ""

    async with _pool.acquire() as conn:
        existing = await conn.fetchval(f'SELECT id FROM "{TABLE}" WHERE id=$1::text', sid)
        if existing:
            update_cols = [k for k in record if k != "id"]
            set_parts = ", ".join(
                f'"{col}" = ${i+1}::text' for i, col in enumerate(update_cols)
            )
            vals = [record[col] for col in update_cols] + [sid]
            await conn.execute(
                f'UPDATE "{TABLE}" SET {set_parts} WHERE "id" = ${len(vals)}::text',
                *vals
            )
        else:
            record["status"]      = "idle"
            record["last_run"]    = ""
            record["last_exit"]   = ""
            record["last_output"] = ""
            cols_q = ", ".join(f'"{k}"' for k in record)
            placeholders = ", ".join(f"${i+1}::text" for i in range(len(record)))
            await conn.execute(
                f'INSERT INTO "{TABLE}" ({cols_q}) VALUES ({placeholders})',
                *record.values()
            )

    return _json({"ok": True, "id": sid})


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/delete")
async def delete_schedule(request: Request):
    body = await request.json()
    sid = body.get("id", "")
    if not sid:
        return Response(status_code=400)
    await _service.kill(sid)
    async with _pool.acquire() as conn:
        await conn.execute(f'DELETE FROM "{TABLE}" WHERE id=$1', sid)
    return _json({"ok": True})


# ── Run now ───────────────────────────────────────────────────────────────────

@router.post("/run")
async def run_now(request: Request):
    body = await request.json()
    sid = body.get("id", "")
    if not sid:
        return Response(status_code=400)

    async with _pool.acquire() as conn:
        row = await conn.fetchrow(f'SELECT * FROM "{TABLE}" WHERE id=$1', sid)
        if not row:
            return Response(status_code=404)
        await _service.fire_now(sid, dict(row), conn)

    return _json({"ok": True, "id": sid})


# ── Stop ──────────────────────────────────────────────────────────────────────

@router.post("/stop")
async def stop_schedule(request: Request):
    body = await request.json()
    sid = body.get("id", "")
    await _service.kill(sid)
    return _json({"ok": True})


# ── Output (last from DB) ─────────────────────────────────────────────────────

@router.get("/output")
async def get_output(id: str = ""):
    if not id:
        return Response(status_code=400)
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT last_output, status, last_exit FROM "{TABLE}" WHERE id=$1', id
        )
    if not row:
        return _json({"id": id, "status": "", "isLive": False, "output": "", "result": ""})
    return _json({
        "id":     id,
        "status": row["status"],
        "isLive": _service.is_running(id),
        "output": row["last_output"] or "",
        "result": _service.get_result(id),
    })


# ── Live output ───────────────────────────────────────────────────────────────

@router.get("/live-output")
async def live_output(id: str = "", runId: str = ""):
    if not id:
        return Response(status_code=400)
    output = await _service.get_live_output_async(id, runId or None)
    return _json({
        "id":     id,
        "isLive": _service.is_running(id),
        "output": output,
        "result": _service.get_result(id),
    })


# ── Clear output ──────────────────────────────────────────────────────────────

@router.post("/clear-output")
async def clear_output(request: Request):
    body = await request.json()
    sid = body.get("id", "")
    if not sid:
        return Response(status_code=400)
    await _service.clear_live_output(sid)
    async with _pool.acquire() as conn:
        await conn.execute(f'UPDATE "{TABLE}" SET "last_output"=\'\' WHERE id=$1', sid)
    return _json({"ok": True})


# ── Payload ───────────────────────────────────────────────────────────────────

@router.get("/payload")
async def get_payload(id: str = ""):
    if not id:
        return Response(status_code=400)
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT payload_schema, payload_values FROM "{TABLE}" WHERE id=$1', id
        )
    return _json({
        "id":     id,
        "schema": row["payload_schema"] if row else "",
        "values": row["payload_values"] if row else "",
    })


@router.post("/payload")
async def save_payload(request: Request):
    body = await request.json()
    sid    = body.get("id", "")
    schema = body.get("schema", "")
    values = body.get("values", "")
    if not sid:
        return Response(status_code=400)
    async with _pool.acquire() as conn:
        await conn.execute(
            f'UPDATE "{TABLE}" SET "payload_schema"=$1, "payload_values"=$2 WHERE id=$3',
            schema, values, sid
        )
    return _json({"ok": True})


# ── Process stats ─────────────────────────────────────────────────────────────

@router.get("/process-stats")
async def process_stats(id: str = ""):
    if not id:
        return Response(status_code=400)
    return _json(_service.get_process_info(id))


# ── Instances ─────────────────────────────────────────────────────────────────

@router.get("/instances")
async def instances(id: str = ""):
    if not id:
        return Response(status_code=400)
    return _json(_service.get_instances(id))


@router.post("/kill-instance")
async def kill_instance(request: Request):
    body  = await request.json()
    sid   = body.get("id", "")
    run_id = body.get("runId", "")
    if not sid:
        return Response(status_code=400)
    if run_id:
        await _service.kill_instance(sid, run_id)
    else:
        await _service.kill(sid)
    return _json({"ok": True})


# ── Queue ─────────────────────────────────────────────────────────────────────

@router.get("/queue")
async def queue_items(id: str = ""):
    if not id:
        return Response(status_code=400)
    async with _pool.acquire() as conn:
        items = await _service.get_queue_items(conn, id)
    return _json(items)


@router.post("/clear-queue")
async def clear_queue(request: Request):
    body = await request.json()
    sid = body.get("id", "")
    if not sid:
        return Response(status_code=400)
    async with _pool.acquire() as conn:
        await _service.clear_queue(conn, sid)
    return _json({"ok": True})


# ── SSE output stream ─────────────────────────────────────────────────────────

@router.get("/output/stream")
async def output_stream(id: str = ""):
    if not id:
        return Response(status_code=400)

    queue = sse_subscribe(id)

    # send existing live output as initial lines
    snapshot = await _service.get_live_output_async(id)

    async def event_generator():
        try:
            if snapshot:
                for line in snapshot.split("\n"):
                    if line:
                        level = "ERROR" if line.startswith("[ERR]") else "INFO"
                        data = json.dumps({"line": line, "level": level})
                        yield f"event: output\ndata: {data}\n\n"

            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if "done" in msg:
                        yield f"event: done\ndata: {{}}\n\n"
                        break
                    data = json.dumps(msg)
                    yield f"event: output\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            sse_unsubscribe(id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ── Open file / folder ────────────────────────────────────────────────────────

@router.get("/open-file")
async def open_file(path: str = ""):
    if not path or not os.path.isfile(path):
        return _json({"ok": False, "error": f"File not found: {path}"})

    _open_path(path)
    return _json({"ok": True})


@router.get("/open-folder")
async def open_folder(path: str = ""):
    if not path:
        return _json({"ok": False, "error": "Path is empty"})

    target = os.path.dirname(path) if os.path.isfile(path) else path
    if not os.path.isdir(target):
        return _json({"ok": False, "error": f"Directory not found: {target}"})

    _open_path(target)
    return _json({"ok": True})


def _open_path(path: str):
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _existing_columns(conn, table: str) -> list[str]:
    try:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name=$1", table
        )
        return [r["column_name"] for r in rows]
    except Exception:
        # SQLite fallback
        rows = await conn.fetch(f'PRAGMA table_info("{table}")')
        return [r["name"] for r in rows]


def _cols_sql(cols: list[str]) -> str:
    return ", ".join(f'"{c}"' for c in cols)


# ── Config file read/write ────────────────────────────────────────────────────

@router.get("/config-file")
async def get_config_file(id: str = "", type: str = "config"):
    """type: 'config' (default) | 'package'"""
    if not id:
        return Response(status_code=400)
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT executor, script_path FROM "{TABLE}" WHERE id=$1', id
        )
    if not row:
        return _json({"ok": False, "error": "Schedule not found"})

    executor    = row["executor"] or ""
    script_path = row["script_path"] or ""

    if type == "package" and _is_js(executor):
        folder      = script_path if os.path.isdir(script_path) else os.path.dirname(script_path)
        config_path = os.path.join(folder, "package.json")
        found       = os.path.isfile(config_path)
    else:
        config_path, found = _resolve_config_path(executor, script_path)

    if not found:
        # create empty file placeholder so editor opens
        return _json({"ok": True, "missing": True, "path": config_path, "content": ""})

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
        return _json({"ok": True, "path": config_path, "content": content})
    except Exception as ex:
        return _json({"ok": False, "error": str(ex)})


@router.post("/config-file")
async def save_config_file(request: Request):
    body    = await request.json()
    path    = body.get("path", "")
    content = body.get("content", "")
    if not path:
        return Response(status_code=400)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return _json({"ok": True})
    except Exception as ex:
        return _json({"ok": False, "error": str(ex)})


# ── Install (npm install / pip install -r requirements.txt) ───────────────────

@router.get("/install/stream")
async def install_stream(id: str = ""):
    if not id:
        return Response(status_code=400)

    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT executor, script_path FROM "{TABLE}" WHERE id=$1', id
        )
    if not row:
        return Response(status_code=404)

    executor    = row["executor"] or ""
    script_path = row["script_path"] or ""
    cmd, cwd    = _resolve_install_cmd(executor, script_path)

    if cmd is None:
        async def err_gen():
            yield f"event: output\ndata: {json.dumps({'line': '[ERR] install not supported for executor: ' + executor, 'level': 'ERROR'})}\n\n"
            yield f"event: done\ndata: {{}}\n\n"
        return StreamingResponse(err_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                          "Access-Control-Allow-Origin": "*"})

    async def run_gen():
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )

            async for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    data = json.dumps({"line": line, "level": "INFO"})
                    yield f"event: output\ndata: {data}\n\n"

            await proc.wait()
            exit_line = f"[exit {proc.returncode}]"
            level     = "INFO" if proc.returncode == 0 else "ERROR"
            yield f"event: output\ndata: {json.dumps({'line': exit_line, 'level': level})}\n\n"
        except Exception as ex:
            yield f"event: output\ndata: {json.dumps({'line': '[ERR] ' + str(ex), 'level': 'ERROR'})}\n\n"
        finally:
            yield f"event: done\ndata: {{}}\n\n"

    return StreamingResponse(
        run_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Access-Control-Allow-Origin": "*"},
    )


# ── Scan script folder ────────────────────────────────────────────────────────

@router.get("/scan-folder")
async def scan_folder(id: str = ""):
    """Return presence of config.json/config.py/requirements.txt for given schedule."""
    if not id:
        return Response(status_code=400)
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT executor, script_path FROM "{TABLE}" WHERE id=$1', id
        )
    if not row:
        return _json({})

    executor    = row["executor"] or ""
    script_path = row["script_path"] or ""
    folder      = script_path if os.path.isdir(script_path) else os.path.dirname(script_path)

    result = {
        "has_config":       False,
        "config_path":      "",
        "has_requirements": False,
        "req_path":         "",
        "has_package_json":  False,
        "package_json_path": "",
    }

    if _is_js(executor):
        cfg = os.path.join(folder, "config.json")
        result["has_config"]  = os.path.isfile(cfg)
        result["config_path"] = cfg
        pkg = os.path.join(folder, "package.json")
        result["has_package_json"]  = os.path.isfile(pkg)
        result["package_json_path"] = pkg
    elif _is_py(executor):
        cfg = os.path.join(folder, "config.py")
        result["has_config"]  = os.path.isfile(cfg)
        result["config_path"] = cfg
        req = os.path.join(folder, "requirements.txt")
        result["has_requirements"] = os.path.isfile(req)
        result["req_path"]         = req

    return _json(result)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _is_js(executor: str) -> bool:
    return executor in ("node", "ts-node")

def _is_py(executor: str) -> bool:
    return executor == "python"

def _resolve_config_path(executor: str, script_path: str) -> tuple[str, bool]:
    if _is_js(executor):
        folder = script_path if os.path.isdir(script_path) else os.path.dirname(script_path)
        cfg    = os.path.join(folder, "config.json")
    elif _is_py(executor):
        folder = os.path.dirname(script_path)
        cfg    = os.path.join(folder, "config.py")
    else:
        return "", False
    return cfg, os.path.isfile(cfg)

def _resolve_install_cmd(executor: str, script_path: str) -> tuple[list | None, str]:
    if _is_js(executor):
        folder = script_path if os.path.isdir(script_path) else os.path.dirname(script_path)
        return ["npm", "install"], folder
    if _is_py(executor):
        folder = os.path.dirname(script_path)
        req    = os.path.join(folder, "requirements.txt")
        if not os.path.isfile(req):
            return None, folder
        pip = "pip" if sys.platform != "win32" else "pip"
        return [pip, "install", "-r", "requirements.txt"], folder
    return None, ""


# ── Package.json scripts ──────────────────────────────────────────────────────

@router.get("/package-scripts")
async def package_scripts(id: str = ""):
    """Parse package.json and return scripts dict for node/ts-node tasks."""
    if not id:
        return Response(status_code=400)
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT executor, script_path FROM "{TABLE}" WHERE id=$1', id
        )
    if not row:
        return _json({"ok": False, "scripts": {}})

    executor    = row["executor"] or ""
    script_path = row["script_path"] or ""

    if not _is_js(executor):
        return _json({"ok": False, "scripts": {}})

    folder   = script_path if os.path.isdir(script_path) else os.path.dirname(script_path)
    pkg_path = os.path.join(folder, "package.json")

    if not os.path.isfile(pkg_path):
        return _json({"ok": True, "scripts": {}, "missing": True})

    try:
        with open(pkg_path, "r", encoding="utf-8") as f:
            pkg = json.load(f)
        scripts = pkg.get("scripts", {})
        return _json({"ok": True, "scripts": scripts})
    except Exception as ex:
        return _json({"ok": False, "scripts": {}, "error": str(ex)})


# ── Open terminal in script folder ───────────────────────────────────────────

@router.get("/open-terminal")
async def open_terminal(id: str = ""):
    if not id:
        return Response(status_code=400)
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            f'SELECT script_path, terminal_override, terminal_init_cmd FROM "{TABLE}" WHERE id=$1', id
        )
    if not row:
        return _json({"ok": False, "error": "Schedule not found"})

    script_path   = row["script_path"] or ""
    term_override = (row["terminal_override"] or "") if "terminal_override" in dict(row) else ""
    term_init_cmd = (row["terminal_init_cmd"] or "") if "terminal_init_cmd" in dict(row) else ""
    folder = script_path if os.path.isdir(script_path) else os.path.dirname(script_path)

    if not folder or not os.path.isdir(folder):
        return _json({"ok": False, "error": f"Folder not found: {folder}"})

    print(f"[terminal] override={term_override!r} init_cmd={term_init_cmd!r} folder={folder!r}")
    try:
        _launch_terminal(folder, term_override or "", term_init_cmd or "")
        return _json({"ok": True, "debug": {"override": term_override, "init_cmd": term_init_cmd, "folder": folder}})
    except Exception as ex:
        return _json({"ok": False, "error": str(ex)})


def _launch_terminal(cwd: str, override: str = "", init_cmd: str = ""):
    import subprocess
    # per-task override wins over global config
    terminal  = override.strip() if override.strip() else getattr(config, "TERMINAL", "cmd")
    term_path = getattr(config, "TERMINAL_PATH", "").strip()

    if terminal == "cmd":
        cd_cmd = f"cd /d {cwd}"
        full   = f"{cd_cmd} && {init_cmd}" if init_cmd.strip() else cd_cmd
        subprocess.Popen(
            ["cmd.exe", "/K", full],
            creationflags=_CREATE_NEW_CONSOLE,
        )

    elif terminal == "powershell":
        cd_cmd = f"Set-Location '{cwd}'"
        full   = f"{cd_cmd}; {init_cmd}" if init_cmd.strip() else cd_cmd
        subprocess.Popen(
            ["powershell.exe", "-NoExit", "-Command", full],
            creationflags=_CREATE_NEW_CONSOLE,
        )

    elif terminal == "gitbash":
        bash = _find_gitbash()
        if not bash:
            raise RuntimeError(
                "Git Bash not found. Install Git for Windows or set TERMINAL_PATH."
            )
        # write init commands via --rcfile approach or pass as -c "cd ... && exec bash"
        cd_expr = f"cd '{cwd}'"
        full    = f"{cd_expr} && {init_cmd} && exec bash" if init_cmd.strip() else f"{cd_expr} && exec bash"
        subprocess.Popen(
            [bash, "--login", "-c", full],
            creationflags=_CREATE_NEW_CONSOLE,
        )

    elif terminal == "third_party":
        if not term_path:
            raise RuntimeError("TERMINAL_PATH is not set in config.py")
        if not os.path.isfile(term_path):
            raise RuntimeError(f"Terminal not found: {term_path}")
        subprocess.Popen([term_path], cwd=cwd)

    else:
        raise RuntimeError(f"Unknown TERMINAL value: {terminal!r}")


# Windows CREATE_NEW_CONSOLE flag (0 on non-Windows — Popen ignores it)
_CREATE_NEW_CONSOLE = 0x00000010 if sys.platform == "win32" else 0


def _find_gitbash() -> str | None:
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\Git\bin\bash.exe",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


@router.get("/terminal-config")
async def terminal_config():
    """Return current terminal config and git bash availability."""
    term     = getattr(config, "TERMINAL", "cmd")
    path     = getattr(config, "TERMINAL_PATH", "")
    gb       = _find_gitbash()
    return _json({
        "terminal":      term,
        "terminal_path": path,
        "gitbash_found": gb or "",
    })
