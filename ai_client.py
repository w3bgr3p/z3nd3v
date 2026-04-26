"""
AI client — port of AiClient.cs.

Providers:
  aiio       — https://api.intelligence.io.solutions  (key from __aiio table)
  omniroute  — local LLM router, default http://localhost:20128/ (no key)

Key selection: random pick from __aiio rows where expire is empty or not yet expired.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Optional

import httpx

import config

# ── Constants ─────────────────────────────────────────────────────────────────

_AIIO_COMPLETIONS = "https://api.intelligence.io.solutions/api/v1/chat/completions"
_AIIO_MODELS      = "https://api.intelligence.io.solutions/api/v1/models?page=1&page_size=200"

_models_cache: Optional[list[str]] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _omni_url(path: str) -> str:
    return config.OMNI_ROUTE_HOST.rstrip("/") + path


async def _get_aiio_key(pool) -> str:
    """Random non-expired key from __aiio table."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT api FROM \"__aiio\" "
            "WHERE (\"expire\" = '' OR \"expire\" IS NULL OR \"expire\" > $1)",
            now,
        )
    keys = [r["api"] for r in rows if r["api"] and r["api"].strip()]
    if not keys:
        raise RuntimeError("No valid aiio key in __aiio table")
    return random.choice(keys).strip()


def _resolve_endpoint(provider: str) -> tuple[str, str]:
    """Return (completions_url, models_url) for provider."""
    if provider == "aiio":
        return _AIIO_COMPLETIONS, _AIIO_MODELS
    if provider == "omniroute":
        return _omni_url("/v1/chat/completions"), _omni_url("/v1/models")
    raise ValueError(f"Unknown AI provider: {provider!r}")


# ── Client ────────────────────────────────────────────────────────────────────

class AiClient:
    def __init__(self, pool):
        self._pool = pool

    @property
    def is_enabled(self) -> bool:
        return getattr(config, "AI_PROVIDER", "") in ("aiio", "omniroute")

    # ── complete ──────────────────────────────────────────────────────────────

    async def complete(
        self,
        model:       str,
        system:      str,
        user:        str,
        temp:        float = 0.3,
        max_tokens:  int   = 800,
        timeout_sec: int   = 90,
    ) -> str:
        provider = config.AI_PROVIDER
        completions_url, _ = _resolve_endpoint(provider)

        api_key = ""
        if provider == "aiio":
            api_key = await _get_aiio_key(self._pool)

        payload = {
            "model":       model,
            "messages":    [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "temperature": temp,
            "top_p":       0.9,
            "stream":      False,
            "max_tokens":  max_tokens,
        }

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=timeout_sec) as http:
            resp = await http.post(completions_url, json=payload, headers=headers)

        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}\n{resp.text}")

        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"] or "No response"
        except Exception as ex:
            raise RuntimeError(f"{ex}\nRAW:\n{resp.text}")

    # ── models ────────────────────────────────────────────────────────────────

    async def get_models(self, force: bool = False) -> list[str]:
        global _models_cache
        if _models_cache is not None and not force:
            return _models_cache

        provider = config.AI_PROVIDER
        _, models_url = _resolve_endpoint(provider)

        api_key = ""
        if provider == "aiio":
            api_key = await _get_aiio_key(self._pool)

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.get(models_url, headers=headers)

        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}\n{resp.text}")

        data   = resp.json()
        models = sorted(
            m["id"] for m in data.get("data", []) if m.get("id")
        )
        _models_cache = models
        return models

    @staticmethod
    def invalidate_models_cache():
        global _models_cache
        _models_cache = None

    # ── validation ────────────────────────────────────────────────────────────

    @staticmethod
    async def check_omniroute(host: str) -> bool:
        url = host.rstrip("/") + "/v1/models"
        try:
            async with httpx.AsyncClient(timeout=3) as http:
                resp = await http.get(url)
            return resp.status_code < 400
        except Exception:
            return False

    async def has_aiio_key(self) -> bool:
        try:
            key = await _get_aiio_key(self._pool)
            return bool(key)
        except Exception:
            return False
