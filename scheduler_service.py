import asyncio
import base64
import json
import os
import shlex
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import psutil
from croniter import croniter

TABLE = "_schedules"
QUEUE_TABLE = "_schedule_queue"

SCHEMA = {
    "id":               "TEXT PRIMARY KEY",
    "name":             "TEXT DEFAULT ''",
    "executor":         "TEXT DEFAULT 'python'",
    "script_path":      "TEXT DEFAULT ''",
    "args":             "TEXT DEFAULT ''",
    "enabled":          "TEXT DEFAULT 'true'",
    "cron":             "TEXT DEFAULT ''",
    "interval_minutes": "TEXT DEFAULT '0'",
    "fixed_time":       "TEXT DEFAULT ''",
    "on_overlap":       "TEXT DEFAULT 'skip'",
    "max_threads":      "TEXT DEFAULT '1'",
    "status":           "TEXT DEFAULT 'idle'",
    "last_run":         "TEXT DEFAULT ''",
    "last_exit":        "TEXT DEFAULT ''",
    "last_output":      "TEXT DEFAULT ''",
    "payload_schema":   "TEXT DEFAULT ''",
    "payload_values":   "TEXT DEFAULT ''",
    "runs_total":       "TEXT DEFAULT '0'",
    "runs_success":     "TEXT DEFAULT '0'",
    "schedule_tag":      "TEXT DEFAULT ''",
    "last_run_id":       "TEXT DEFAULT ''",
    "terminal_override": "TEXT DEFAULT ''",
    "terminal_init_cmd": "TEXT DEFAULT ''",
    "trigger_on_success": "TEXT DEFAULT ''",
    "trigger_on_fail":    "TEXT DEFAULT ''",
    "venv_path":         "TEXT DEFAULT ''",
}

QUEUE_SCHEMA = {
    "uuid":        "TEXT PRIMARY KEY",
    "schedule_id": "TEXT DEFAULT ''",
    "queued_at":   "TEXT DEFAULT ''",
    "status":      "TEXT DEFAULT 'pending'",
    "priority":    "TEXT DEFAULT '10'",
    "run_id":      "TEXT DEFAULT ''",
    "args_b64":    "TEXT DEFAULT ''",
}


class RunningProcess:
    def __init__(self, proc: Optional[asyncio.subprocess.Process], started_at: datetime):
        self._proc = proc
        self._lines: list[str] = []
        self._lock = asyncio.Lock()
        self.started_at = started_at
        self.result: str = ""
        self._cancelled = False

    @property
    def pid(self) -> int:
        if self._proc is None:
            return -1
        try:
            return self._proc.pid
        except Exception:
            return -1

    @property
    def has_exited(self) -> bool:
        if self._proc is None:
            return self._cancelled
        return self._proc.returncode is not None

    @property
    def uptime_sec(self) -> int:
        return int((datetime.now(timezone.utc) - self.started_at).total_seconds())

    @property
    def memory_mb(self) -> int:
        try:
            if self._proc and self._proc.returncode is None:
                p = psutil.Process(self._proc.pid)
                return p.memory_info().rss // 1024 // 1024
        except Exception:
            pass
        return 0

    async def add_line(self, line: str):
        async with self._lock:
            self._lines.append(line)
            if len(self._lines) > 2000:
                self._lines.pop(0)

    async def snapshot(self) -> str:
        async with self._lock:
            return "\n".join(self._lines)

    async def clear(self):
        async with self._lock:
            self._lines.clear()

    async def replace_last(self, line: str):
        async with self._lock:
            if self._lines:
                self._lines[-1] = line
            else:
                self._lines.append(line)

    def kill(self):
        self._cancelled = True
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.kill()
            except Exception:
                pass


# SSE broadcast: schedule_id -> list of queues
_sse_queues: dict[str, list[asyncio.Queue]] = defaultdict(list)


def sse_subscribe(schedule_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _sse_queues[schedule_id].append(q)
    return q


def sse_unsubscribe(schedule_id: str, q: asyncio.Queue):
    try:
        _sse_queues[schedule_id].remove(q)
    except ValueError:
        pass


def sse_broadcast(schedule_id: str, data: dict):
    # Add timestamp to output lines
    if "line" in data and "timestamp" not in data:
        data["timestamp"] = datetime.now(timezone.utc).isoformat()

    for q in list(_sse_queues.get(schedule_id, [])):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass


class SchedulerService:
    def __init__(self, pool):
        self._pool = pool
        self._running: dict[str, RunningProcess] = {}  # key = "id:runId"
        self._lock = asyncio.Lock()
        self._tick_task: Optional[asyncio.Task] = None

    async def init(self):
        await self._prepare_tables()
        self._tick_task = asyncio.create_task(self._tick_loop())

    async def dispose(self):
        if self._tick_task:
            self._tick_task.cancel()
        async with self._lock:
            for rp in self._running.values():
                rp.kill()

    # ── Table setup ───────────────────────────────────────────────────────────

    async def _prepare_tables(self):
        async with self._pool.acquire() as conn:
            await self._ensure_table(conn, TABLE, SCHEMA)
            await self._ensure_table(conn, QUEUE_TABLE, QUEUE_SCHEMA)

    async def _ensure_table(self, conn, table: str, schema: dict):
        cols_def = ", ".join(f'"{k}" {v}' for k, v in schema.items())
        await conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({cols_def})')
        existing = await self._get_columns(conn, table)
        for col, col_type in schema.items():
            if col not in existing:
                default = col_type.split("DEFAULT")[-1].strip() if "DEFAULT" in col_type else "''"
                await conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" TEXT DEFAULT {default}')

    async def _get_columns(self, conn, table: str) -> set:
        # SQLite: PRAGMA table_info; Postgres: information_schema
        try:
            rows = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name=$1", table
            )
            return {r["column_name"] for r in rows}
        except Exception:
            # SQLite fallback
            rows = await conn.fetch(f'PRAGMA table_info("{table}")')
            return {r["name"] for r in rows}

    # ── Tick loop ─────────────────────────────────────────────────────────────

    async def _tick_loop(self):
        while True:
            try:
                await self._tick()
            except Exception as e:
                print(f"[tick error] {e}")
            await asyncio.sleep(60)

    async def _tick(self):
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(f'SELECT * FROM "{TABLE}" WHERE "enabled" = \'true\'')
            now = datetime.now(timezone.utc)
            for row in rows:
                rec = dict(row)
                if not self._should_fire(rec, now):
                    continue

                sid = rec["id"]
                overlap = rec.get("on_overlap", "skip")
                max_threads = int(rec.get("max_threads", "1") or "1")
                active = self._count_active(sid)

                if overlap == "skip":
                    if active > 0:
                        continue
                elif overlap == "kill_restart":
                    if active > 0:
                        await self._kill_all(sid)
                elif overlap == "parallel":
                    if active >= max_threads:
                        await self._enqueue(conn, sid, rec, priority=10)
                        continue

                asyncio.create_task(self._launch(rec))

            await self._drain_queue(conn, now)

    # ── Should fire ───────────────────────────────────────────────────────────

    def _should_fire(self, rec: dict, now: datetime) -> bool:
        cron_expr = rec.get("cron", "")
        if cron_expr and cron_expr.strip():
            try:
                cron = croniter(cron_expr.strip(), now)
                prev = cron.get_prev(datetime)
                if (now - prev).total_seconds() < 60:
                    return True
            except Exception:
                pass

        interval = int(rec.get("interval_minutes", "0") or "0")
        if interval > 0:
            last_run = rec.get("last_run", "")
            if not last_run:
                return True
            try:
                last = datetime.fromisoformat(last_run.replace(" ", "T"))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last).total_seconds() / 60 >= interval:
                    return True
            except Exception:
                return True

        fixed = rec.get("fixed_time", "")
        if fixed and fixed.strip():
            try:
                ft = datetime.strptime(fixed.strip(), "%H:%M").time()
                today_fire = datetime.combine(now.date(), ft, tzinfo=timezone.utc)
                last_run = rec.get("last_run", "")
                last_date = None
                if last_run:
                    try:
                        last_date = datetime.fromisoformat(last_run.replace(" ", "T")).date()
                    except Exception:
                        pass
                if now >= today_fire and (now - today_fire).total_seconds() < 60:
                    if last_date is None or last_date < now.date():
                        return True
            except Exception:
                pass

        return False

    # ── Queue ─────────────────────────────────────────────────────────────────

    async def _enqueue(self, conn, schedule_id: str, rec: dict, priority: int):
        await conn.execute(
            f'INSERT INTO "{QUEUE_TABLE}" (uuid, schedule_id, queued_at, status, priority, run_id, args_b64) '
            f"VALUES ($1,$2,$3,'pending',$4,'',$5)",
            str(uuid.uuid4()), schedule_id,
            datetime.now(timezone.utc).isoformat(),
            str(priority), rec.get("args", "")
        )

    async def _drain_queue(self, conn, now: datetime):
        rows = await conn.fetch(
            f'SELECT * FROM "{QUEUE_TABLE}" WHERE status=\'pending\' ORDER BY priority ASC, queued_at ASC'
        )
        for qrow in rows:
            sid = qrow["schedule_id"]
            sched = await conn.fetchrow(f'SELECT * FROM "{TABLE}" WHERE id=$1', sid)
            if not sched:
                continue
            rec = dict(sched)
            max_threads = int(rec.get("max_threads", "1") or "1")
            if self._count_active(sid) >= max_threads:
                continue
            q_uuid = qrow["uuid"]
            await conn.execute(f'UPDATE "{QUEUE_TABLE}" SET status=\'running\' WHERE uuid=$1', q_uuid)
            if qrow["args_b64"]:
                rec["args"] = qrow["args_b64"]
            asyncio.create_task(self._launch_from_queue(rec, q_uuid))

    async def _try_drain_one(self, schedule_id: str):
        async with self._pool.acquire() as conn:
            sched = await conn.fetchrow(f'SELECT * FROM "{TABLE}" WHERE id=$1', schedule_id)
            if not sched:
                return
            rec = dict(sched)
            max_threads = int(rec.get("max_threads", "1") or "1")
            if self._count_active(schedule_id) >= max_threads:
                return
            qrow = await conn.fetchrow(
                f'SELECT * FROM "{QUEUE_TABLE}" WHERE status=\'pending\' AND schedule_id=$1 '
                f'ORDER BY priority ASC, queued_at ASC LIMIT 1', schedule_id
            )
            if not qrow:
                return
            await conn.execute(f'UPDATE "{QUEUE_TABLE}" SET status=\'running\' WHERE uuid=$1', qrow["uuid"])
            if qrow["args_b64"]:
                rec["args"] = qrow["args_b64"]
            asyncio.create_task(self._launch_from_queue(rec, qrow["uuid"]))

    async def _finish_queue_entry(self, conn, q_uuid: Optional[str], status: str, run_id: str):
        if not q_uuid:
            return
        await conn.execute(
            f'UPDATE "{QUEUE_TABLE}" SET status=$1, run_id=$2 WHERE uuid=$3',
            status, run_id, q_uuid
        )

    # ── Launch ────────────────────────────────────────────────────────────────

    async def _launch(self, rec: dict):
        await self._launch_from_queue(rec, None)

    async def _launch_from_queue(self, rec: dict, queue_uuid: Optional[str]):
        sid         = rec["id"]
        name        = rec.get("name", sid)
        executor    = rec.get("executor", "python")
        script_path = rec.get("script_path", "")
        args        = rec.get("args", "")
        venv_path   = rec.get("venv_path", "")
        run_id      = uuid.uuid4().hex[:12]
        instance_key = f"{sid}:{run_id}"
        fired_at    = datetime.now(timezone.utc)

        rp = RunningProcess(None, fired_at)

        if not os.path.exists(script_path):
            err = f"[ERR] script not found: {script_path}"
            await rp.add_line(err)
            sse_broadcast(sid, {"line": err, "level": "ERROR"})
            sse_broadcast(sid, {"done": True})
            async with self._pool.acquire() as conn:
                await self._update_status(conn, sid, "error", fired_at, "-1", err, run_id)
                await self._finish_queue_entry(conn, queue_uuid, "error", run_id)
            return

        async with self._pool.acquire() as conn:
            await self._update_status(conn, sid, "running", fired_at, "", "", run_id)

        async with self._lock:
            self._running[instance_key] = rp

        def broadcast(line: str):
            level = "ERROR" if line.startswith("[ERR]") else "INFO"
            sse_broadcast(sid, {"line": line, "level": level})

        def broadcast_replace(line: str):
            level = "ERROR" if line.startswith("[ERR]") else "INFO"
            sse_broadcast(sid, {"line": line, "level": level, "replace_last": True})

        try:
            cmd = self._build_command(executor, script_path, args, venv_path)
            workdir = script_path if os.path.isdir(script_path) else (os.path.dirname(script_path) if os.path.isfile(script_path) else os.getcwd())

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
                env=env,
            )
            rp._proc = proc

            async def read_stream(stream, prefix=""):
                buf = b""
                while True:
                    chunk = await stream.read(1024)
                    if not chunk:
                        break
                    buf += chunk
                    # split on \n and \r but treat \r differently:
                    # \r without following \n = carriage return (progress bar) → replace last line
                    # \n = new line
                    while b"\n" in buf or b"\r" in buf:
                        nl = buf.find(b"\n")
                        cr = buf.find(b"\r")

                        if nl == -1:
                            # only \r present
                            idx, is_cr = cr, True
                        elif cr == -1:
                            idx, is_cr = nl, False
                        elif cr < nl:
                            # check \r\n pair
                            if cr + 1 == nl:
                                # \r\n → treat as newline
                                line = buf[:cr].decode("utf-8", errors="replace")
                                buf = buf[nl + 1:]
                                if line.strip():
                                    full = prefix + line.strip()
                                    await rp.add_line(full)
                                    broadcast(full)
                                continue
                            idx, is_cr = cr, True
                        else:
                            idx, is_cr = nl, False

                        line = buf[:idx].decode("utf-8", errors="replace")
                        buf = buf[idx + 1:]
                        if not line.strip():
                            continue
                        full = prefix + line.strip()
                        if is_cr:
                            # carriage return: replace last line in buffer
                            await rp.replace_last(full)
                            broadcast_replace(full)
                        else:
                            await rp.add_line(full)
                            broadcast(full)

                if buf:
                    line = buf.decode("utf-8", errors="replace").strip()
                    if line:
                        full = prefix + line
                        await rp.add_line(full)
                        broadcast(full)

            await asyncio.gather(
                read_stream(proc.stdout),
                read_stream(proc.stderr, "[ERR] "),
                proc.wait(),
            )

            exit_code = proc.returncode
            sse_broadcast(sid, {"done": True})
            output = await rp.snapshot()

            async with self._pool.acquire() as conn:
                status = "running" if self._count_active(sid) > 0 else "idle"
                await self._update_status(conn, sid, status, datetime.now(timezone.utc), str(exit_code), output, run_id)
                await self._finish_queue_entry(conn, queue_uuid, "done", run_id)

                # Trigger dependent tasks on success
                if exit_code == 0:
                    await self._fire_triggers(conn, rec, "trigger_on_success")
                else:
                    await self._fire_triggers(conn, rec, "trigger_on_fail")

        except Exception as ex:
            await rp.add_line(f"[ERR] {ex}")
            sse_broadcast(sid, {"line": f"[ERR] {ex}", "level": "ERROR"})
            sse_broadcast(sid, {"done": True})
            output = await rp.snapshot()
            async with self._pool.acquire() as conn:
                status = "running" if self._count_active(sid) > 0 else "error"
                await self._update_status(conn, sid, status, datetime.now(timezone.utc), "-1", output, run_id)
                await self._finish_queue_entry(conn, queue_uuid, "error", run_id)

                # Trigger dependent tasks on failure
                await self._fire_triggers(conn, rec, "trigger_on_fail")
        finally:
            async with self._lock:
                self._running.pop(instance_key, None)
            await self._try_drain_one(sid)

    # ── Command builder ───────────────────────────────────────────────────────

    NPM_RUN_PREFIX = "__npm_run__"

    def _build_command(self, executor: str, script_path: str, args: str, venv_path: str = "") -> list[str]:
        # npm run <script> shortcut stored in args as __npm_run__<script_name>
        if args.startswith(self.NPM_RUN_PREFIX):
            script_name = args[len(self.NPM_RUN_PREFIX):]
            if sys.platform == "win32":
                return ["cmd.exe", "/c", "npm", "run", script_name]
            return ["npm", "run", script_name]

        try:
            parts = shlex.split(args, posix=False) if args.strip() else []
            # Remove surrounding quotes from each part if present
            parts = [p.strip('"').strip("'") for p in parts]
        except ValueError:
            # Fallback if shlex fails (unclosed quotes, etc.)
            parts = args.split() if args.strip() else []

        if executor == "python":
            python_exe = self._resolve_python_executable(script_path, venv_path)
            return [python_exe, script_path] + parts
        if executor == "node":
            if sys.platform == "win32":
                return ["cmd.exe", "/c", "node", script_path] + parts
            return ["node", script_path] + parts
        if executor == "bash":
            return self._bash_cmd(script_path, parts)
        if executor == "bat":
            if sys.platform == "win32":
                return ["cmd.exe", "/c", script_path] + parts
            return ["bash", script_path] + parts
        if executor == "ps1":
            if sys.platform == "win32":
                return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path] + parts
            return ["pwsh", "-NoProfile", "-File", script_path] + parts
        if executor == "exe":
            return [script_path] + parts

        return ["python", script_path] + parts

    def _bash_cmd(self, script_path: str, parts: list[str]) -> list[str]:
        if sys.platform == "win32":
            candidates = [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files (x86)\Git\bin\bash.exe",
                r"C:\Git\bin\bash.exe",
            ]
            bash = next((p for p in candidates if os.path.exists(p)), "bash")
            return [bash, script_path] + parts
        return ["bash", script_path] + parts

    def _resolve_python_executable(self, script_path: str, venv_path: str) -> str:
        """Resolve Python executable: venv if specified, auto-detect, or system python."""
        # 1. Explicit venv_path provided
        if venv_path and venv_path.strip():
            venv_path = venv_path.strip()
            if sys.platform == "win32":
                python_exe = os.path.join(venv_path, "Scripts", "python.exe")
            else:
                python_exe = os.path.join(venv_path, "bin", "python")

            if os.path.isfile(python_exe):
                return python_exe

        # 2. Auto-detect venv in script folder
        folder = script_path if os.path.isdir(script_path) else os.path.dirname(script_path)
        venv_candidates = [".venv", "venv", "env"]

        for venv_name in venv_candidates:
            venv_dir = os.path.join(folder, venv_name)
            if not os.path.isdir(venv_dir):
                continue

            if sys.platform == "win32":
                python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
            else:
                python_exe = os.path.join(venv_dir, "bin", "python")

            if os.path.isfile(python_exe):
                return python_exe

        # 3. Fallback to system python
        return "python"

    # ── Status update ─────────────────────────────────────────────────────────

    async def _update_status(self, conn, sid: str, status: str, last_run: datetime,
                              exit_code: str, output: str, run_id: str = ""):
        safe_output = output.replace("'", "''").replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "")
        run_id_part = f', "last_run_id" = \'{run_id}\'' if run_id else ""

        incr = ""
        if exit_code != "":
            incr = ', "runs_total" = (COALESCE(NULLIF("runs_total",\'\'),\'0\')::int + 1)::text'
            if exit_code == "0":
                incr += ', "runs_success" = (COALESCE(NULLIF("runs_success",\'\'),\'0\')::int + 1)::text'

        await conn.execute(
            f'UPDATE "{TABLE}" SET '
            f'"status"=\'{status}\', "last_run"=\'{last_run.strftime("%Y-%m-%d %H:%M:%S")}\', '
            f'"last_exit"=\'{exit_code}\', "last_output"=\'{safe_output}\'{incr}{run_id_part} '
            f'WHERE "id"=\'{sid}\''
        )

    # ── Instance helpers ──────────────────────────────────────────────────────

    def _count_active(self, sid: str) -> int:
        return sum(
            1 for k, rp in self._running.items()
            if k.startswith(sid + ":") and not rp.has_exited
        )

    async def _kill_all(self, sid: str):
        async with self._lock:
            for k, rp in list(self._running.items()):
                if k.startswith(sid + ":"):
                    rp.kill()
                    del self._running[k]

    # ── Public API ────────────────────────────────────────────────────────────

    def is_running(self, sid: str) -> bool:
        return self._count_active(sid) > 0

    def get_live_output(self, sid: str, run_id: Optional[str] = None) -> str:
        keys = [k for k in self._running if k.startswith(sid + ":")]
        if run_id:
            keys = [k for k in keys if k == f"{sid}:{run_id}"]
        if not keys:
            return ""
        if len(keys) == 1:
            rp = self._running.get(keys[0])
            return asyncio.get_event_loop().run_until_complete(rp.snapshot()) if rp else ""
        return ""

    async def get_live_output_async(self, sid: str, run_id: Optional[str] = None) -> str:
        keys = [k for k in self._running if k.startswith(sid + ":")]
        if run_id:
            keys = [k for k in keys if k == f"{sid}:{run_id}"]
        if not keys:
            return ""
        if len(keys) == 1:
            rp = self._running.get(keys[0])
            return await rp.snapshot() if rp else ""
        parts = []
        for k in keys:
            rp = self._running.get(k)
            if rp:
                run_part = k[len(sid) + 1:]
                snap = await rp.snapshot()
                parts.append(f"── {run_part} ──\n{snap}")
        return "\n\n".join(parts)

    def get_result(self, sid: str) -> str:
        for k, rp in self._running.items():
            if k.startswith(sid + ":"):
                return rp.result
        return ""

    async def clear_live_output(self, sid: str):
        for k, rp in self._running.items():
            if k.startswith(sid + ":"):
                await rp.clear()

    def get_instances(self, sid: str) -> list[dict]:
        result = []
        for k, rp in self._running.items():
            if k.startswith(sid + ":") and not rp.has_exited:
                run_id = k[len(sid) + 1:]
                result.append({
                    "runId":     run_id,
                    "uptimeSec": str(rp.uptime_sec),
                    "pid":       str(rp.pid),
                    "memoryMB":  str(rp.memory_mb),
                })
        return result

    def get_process_info(self, sid: str) -> dict:
        for k, rp in self._running.items():
            if k.startswith(sid + ":") and not rp.has_exited:
                return {"pid": rp.pid, "uptimeSec": rp.uptime_sec, "memoryMB": rp.memory_mb, "running": True}
        return {"pid": -1, "uptimeSec": 0, "memoryMB": 0, "running": False}

    async def kill(self, sid: str):
        await self._kill_all(sid)

    async def kill_instance(self, sid: str, run_id: str):
        key = f"{sid}:{run_id}"
        async with self._lock:
            rp = self._running.get(key)
            if rp:
                rp.kill()
                del self._running[key]

    async def fire_now(self, sid: str, rec: dict, conn):
        overlap    = rec.get("on_overlap", "skip")
        max_threads = int(rec.get("max_threads", "1") or "1")
        active     = self._count_active(sid)

        if overlap == "skip":
            if active > 0:
                return
        elif overlap == "kill_restart":
            if active > 0:
                await self._kill_all(sid)
        elif overlap == "parallel":
            if active >= max_threads:
                await self._enqueue(conn, sid, rec, priority=0)
                return

        asyncio.create_task(self._launch(rec))

    async def get_queue_items(self, conn, sid: str) -> list[dict]:
        rows = await conn.fetch(
            f'SELECT * FROM "{QUEUE_TABLE}" WHERE schedule_id=$1 ORDER BY priority ASC, queued_at ASC',
            sid
        )
        return [dict(r) for r in rows]

    async def clear_queue(self, conn, sid: str):
        await conn.execute(
            f'DELETE FROM "{QUEUE_TABLE}" WHERE schedule_id=$1 AND status=\'pending\'', sid
        )

    async def _fire_triggers(self, conn, rec: dict, trigger_field: str):
        """Fire dependent tasks based on trigger configuration."""
        trigger_ids = rec.get(trigger_field, "")
        if not trigger_ids or not trigger_ids.strip():
            return

        # Support comma-separated list of task IDs
        task_ids = [tid.strip() for tid in trigger_ids.split(",") if tid.strip()]

        for task_id in task_ids:
            # Fetch the dependent task
            dep_task = await conn.fetchrow(f'SELECT * FROM "{TABLE}" WHERE id=$1', task_id)
            if not dep_task:
                continue

            dep_rec = dict(dep_task)

            # Check if task is enabled
            if dep_rec.get("enabled", "true") != "true":
                continue

            # Fire the dependent task
            asyncio.create_task(self._launch(dep_rec))

