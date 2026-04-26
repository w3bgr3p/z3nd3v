"""
Port of SqliteViewerHandler.cs

Routes (all POST, body = raw sqlite bytes):
  POST /sqlite-viewer/tables          → {"tables": [...]}
  POST /sqlite-viewer/query?sql=...   → {"columns": [...], "rows": [[...]], "rowids": [...], "table": "..."}
  POST /sqlite-viewer/update?table=T&rowid=N&col=C&value=V  → updated db bytes
  POST /sqlite-viewer/delete?table=T&rowid=N                → updated db bytes
"""

import os
import re
import sqlite3
import tempfile
import uuid
from typing import Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter(prefix="/sqlite-viewer")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _json(data) -> JSONResponse:
    return JSONResponse(content=data)


def _error(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse(content={"error": msg}, status_code=status)


def _is_identifier(s: str) -> bool:
    return bool(s and re.match(r'^\w+$', s))


def _extract_table_name(sql: str) -> str:
    try:
        m = re.search(r'\bFROM\s+[`"\[]?(\w+)[`"\]]?', sql, re.IGNORECASE)
        return m.group(1) if m else ""
    except Exception:
        return ""


async def _write_temp(body: bytes) -> Optional[str]:
    if not body:
        return None
    path = os.path.join(tempfile.gettempdir(), f"z3n_sqlite_{uuid.uuid4().hex}.tmp")
    with open(path, "wb") as f:
        f.write(body)
    return path


def _try_delete(path: str):
    try:
        os.remove(path)
    except Exception:
        pass


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/tables")
async def handle_tables(request: Request):
    body = await request.body()
    tmp  = await _write_temp(body)
    if not tmp:
        return _error("empty body")
    try:
        conn = sqlite3.connect(tmp)
        try:
            cur    = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [r[0] for r in cur.fetchall()]
            return _json({"tables": tables})
        finally:
            conn.close()
    except Exception as ex:
        return _error(str(ex))
    finally:
        _try_delete(tmp)


@router.post("/query")
async def handle_query(request: Request, sql: str = ""):
    if not sql.strip():
        return _error("missing sql")

    trimmed = sql.lstrip()
    if not re.match(r'^(SELECT|WITH)\b', trimmed, re.IGNORECASE):
        return _error("only SELECT allowed")

    body = await request.body()
    tmp  = await _write_temp(body)
    if not tmp:
        return _error("empty body")

    table = _extract_table_name(sql)

    try:
        conn = sqlite3.connect(tmp)
        try:
            columns: list[str]        = []
            rows:    list[list]       = []
            rowids:  list[int | None] = []

            # try injecting rowid
            has_rowid  = False
            exec_sql   = sql
            if table:
                exec_sql  = re.sub(r'(?i)^\s*SELECT\s+', 'SELECT rowid, ', sql, count=1)
                has_rowid = True

            try:
                cur = conn.execute(exec_sql)
            except Exception:
                # rowid injection failed — fallback
                has_rowid = False
                cur = conn.execute(sql)

            desc      = cur.description or []
            start_col = 1 if has_rowid else 0
            columns   = [d[0] for d in desc[start_col:]]

            for r in cur.fetchall():
                if has_rowid:
                    rowids.append(r[0] if r[0] is not None else None)
                row = [str(v) if v is not None else None for v in r[start_col:]]
                rows.append(row)

            return _json({"columns": columns, "rows": rows, "rowids": rowids, "table": table})
        finally:
            conn.close()
    except Exception as ex:
        return _error(str(ex))
    finally:
        _try_delete(tmp)


@router.post("/update")
async def handle_update(
    request: Request,
    table: str = "",
    rowid: str = "",
    col:   str = "",
    value: Optional[str] = None,
):
    if not table or not col or not rowid:
        return _error("missing table/col/rowid")
    if not _is_identifier(table) or not _is_identifier(col):
        return _error("invalid identifier")

    try:
        rowid_val = int(rowid)
    except ValueError:
        return _error("invalid rowid")

    body = await request.body()
    tmp  = await _write_temp(body)
    if not tmp:
        return _error("empty body")

    try:
        conn = sqlite3.connect(tmp)
        try:
            conn.execute(
                f'UPDATE "{table}" SET "{col}" = ? WHERE rowid = ?',
                (value, rowid_val),
            )
            conn.commit()
        finally:
            conn.close()

        with open(tmp, "rb") as f:
            data = f.read()

        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Length": str(len(data))},
        )
    except Exception as ex:
        return _error(str(ex))
    finally:
        _try_delete(tmp)


@router.post("/delete")
async def handle_delete(
    request: Request,
    table: str = "",
    rowid: str = "",
):
    if not table or not rowid:
        return _error("missing table/rowid")
    if not _is_identifier(table):
        return _error("invalid identifier")

    try:
        rowid_val = int(rowid)
    except ValueError:
        return _error("invalid rowid")

    body = await request.body()
    tmp  = await _write_temp(body)
    if not tmp:
        return _error("empty body")

    try:
        conn = sqlite3.connect(tmp)
        try:
            conn.execute(f'DELETE FROM "{table}" WHERE rowid = ?', (rowid_val,))
            conn.commit()
        finally:
            conn.close()

        with open(tmp, "rb") as f:
            data = f.read()

        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Length": str(len(data))},
        )
    except Exception as ex:
        return _error(str(ex))
    finally:
        _try_delete(tmp)
