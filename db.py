"""
Unified async database abstraction.
Supports PostgreSQL (asyncpg) and SQLite (aiosqlite).
API is identical for both — callers use pool.acquire() context manager
and conn.execute / conn.fetch / conn.fetchrow / conn.fetchval.

PostgreSQL params:  $1, $2, ...
SQLite params:      ? (auto-converted from $N)
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, Optional


# ── Param conversion ──────────────────────────────────────────────────────────

def _pg_to_sqlite(sql: str) -> str:
    """Replace $1, $2 ... with ? for SQLite."""
    return re.sub(r'\$\d+(?:::\w+)?', '?', sql)


def _strip_pg_casts(sql: str) -> str:
    """Remove ::text, ::int casts not supported by SQLite."""
    return re.sub(r'::\w+', '', sql)


def _adapt_sql(sql: str) -> str:
    return _strip_pg_casts(_pg_to_sqlite(sql))


# ── SQLite row wrapper (mimics asyncpg Record) ────────────────────────────────

class _Row(dict):
    """Dict-like row that also supports r["col"] and r[0] access."""

    def __init__(self, cursor: sqlite3.Cursor, row: tuple):
        cols = [d[0] for d in cursor.description]
        super().__init__(zip(cols, row))
        self._row = row

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._row[key]
        return super().__getitem__(key)

    def get(self, key, default=None):
        return super().get(key, default)


# ── SQLite connection wrapper ─────────────────────────────────────────────────

class _SqliteConn:
    def __init__(self, raw: Any):  # aiosqlite.Connection
        self._c = raw

    async def execute(self, sql: str, *args) -> None:
        flat = args[0] if len(args) == 1 and isinstance(args[0], (list, tuple)) else args
        await self._c.execute(_adapt_sql(sql), flat)
        await self._c.commit()

    async def fetch(self, sql: str, *args) -> list:
        flat = args[0] if len(args) == 1 and isinstance(args[0], (list, tuple)) else args
        async with self._c.execute(_adapt_sql(sql), flat) as cur:
            rows = await cur.fetchall()
            return [_Row(cur, r) for r in rows]

    async def fetchrow(self, sql: str, *args) -> Optional[_Row]:
        flat = args[0] if len(args) == 1 and isinstance(args[0], (list, tuple)) else args
        async with self._c.execute(_adapt_sql(sql), flat) as cur:
            row = await cur.fetchone()
            return _Row(cur, row) if row else None

    async def fetchval(self, sql: str, *args) -> Any:
        row = await self.fetchrow(sql, *args)
        if row is None:
            return None
        return row[0]


# ── SQLite pool (single persistent connection, serialised via asyncio.Lock) ───

class _SqlitePool:
    def __init__(self, path: str):
        self._path = path
        self._conn: Any = None  # aiosqlite.Connection

    async def _open(self):
        import aiosqlite
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = None          # we build _Row manually
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")

    @asynccontextmanager
    async def acquire(self):
        if self._conn is None:
            await self._open()
        yield _SqliteConn(self._conn)

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None


# ── Postgres thin wrapper (asyncpg already matches our API) ───────────────────

class _PgConn:
    """Wraps asyncpg connection to normalise positional *args calling."""

    def __init__(self, raw):
        self._c = raw

    async def execute(self, sql: str, *args) -> None:
        flat = list(args[0]) if len(args) == 1 and isinstance(args[0], (list, tuple)) else list(args)
        await self._c.execute(sql, *flat)

    async def fetch(self, sql: str, *args) -> list:
        flat = list(args[0]) if len(args) == 1 and isinstance(args[0], (list, tuple)) else list(args)
        return await self._c.fetch(sql, *flat)

    async def fetchrow(self, sql: str, *args):
        flat = list(args[0]) if len(args) == 1 and isinstance(args[0], (list, tuple)) else list(args)
        return await self._c.fetchrow(sql, *flat)

    async def fetchval(self, sql: str, *args):
        flat = list(args[0]) if len(args) == 1 and isinstance(args[0], (list, tuple)) else list(args)
        return await self._c.fetchval(sql, *flat)


class _PgPool:
    def __init__(self, raw):
        self._raw = raw

    @asynccontextmanager
    async def acquire(self):
        async with self._raw.acquire() as conn:
            yield _PgConn(conn)

    async def close(self):
        await self._raw.close()


# ── Factory ───────────────────────────────────────────────────────────────────

async def create_pool(mode: str, dsn: str = "", path: str = "data.db"):
    """
    mode: 'postgres' | 'sqlite'
    dsn:  postgres DSN (only for postgres mode)
    path: sqlite file path (only for sqlite mode)
    """
    if mode == "sqlite":
        pool = _SqlitePool(path)
        await pool._open()
        return pool

    if mode == "postgres":
        import asyncpg
        raw = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
        return _PgPool(raw)

    raise ValueError(f"Unknown DB_MODE: {mode!r}. Use 'sqlite' or 'postgres'.")
