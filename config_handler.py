"""
Config UI handler.

Routes:
  GET  /config/ui  → returns current user_prefs.json contents
  POST /config/ui  → merges body into user_prefs.json

Stores: {theme, dockPosition} and any other keys sent by the client.
File:   user_prefs.json in the project root (next to app.py).
"""

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

_PREFS_FILE = Path(__file__).parent / "user_prefs.json"

DEFAULTS = {
    "theme":        "dark",
    "dockPosition": "bottom",
}


def _read() -> dict:
    try:
        return json.loads(_PREFS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULTS)


def _write(data: dict):
    _PREFS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


@router.get("/config/ui")
async def get_ui_prefs():
    return JSONResponse(_read())


@router.post("/config/ui")
async def post_ui_prefs(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    prefs = _read()
    prefs.update({k: v for k, v in body.items() if isinstance(k, str)})
    _write(prefs)
    return JSONResponse(prefs)
