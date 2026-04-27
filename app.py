import os

import uvicorn
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
import db as db_module
from ai_handler import router as ai_router
from ai_handler import start_agent_service, stop_agent_service
from config_handler import router as config_router
from docs_graph_handler import router as docs_graph_router  # импорт
from scheduler_handler import router as scheduler_router
from scheduler_handler import setup as scheduler_setup
from scheduler_service import SchedulerService
from sqlite_viewer_handler import router as sqlite_viewer_router

PORT = config.DASHBOARD_PORT
WWWROOT = config.WWWROOT

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scheduler_router)
app.include_router(sqlite_viewer_router)
app.include_router(config_router)
app.include_router(ai_router)
app.include_router(docs_graph_router)  # регистрация
# ── Startup / shutdown ────────────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    pool = await db_module.create_pool(
        mode=config.DB_MODE,
        dsn=getattr(config, "DB_DSN", ""),
        path=getattr(config, "DB_PATH", "data.db"),
    )

    service = SchedulerService(pool)
    await service.init()
    await start_agent_service()

    scheduler_setup(pool, service, WWWROOT)
    app.state.pool = pool
    app.state.service = service

    label = (
        config.DB_PATH
        if config.DB_MODE == "sqlite"
        else getattr(config, "DB_DSN", "").split("@")[-1]
    )
    print(f"DB [{config.DB_MODE}]: {label}")
    print(f"Wwwroot: {WWWROOT}")
    print(f"Listening: http://localhost:{PORT}")


@app.on_event("shutdown")
async def shutdown():
    stop_agent_service()
    await app.state.service.dispose()
    await app.state.pool.close()


# ── Static files ──────────────────────────────────────────────────────────────

if os.path.isdir(WWWROOT):
    app.mount("/static", StaticFiles(directory=WWWROOT), name="static")


@app.get("/")
async def index(page: str = "scheduler"):
    path = os.path.join(WWWROOT, f"{page}.html")
    if os.path.isfile(path):
        return FileResponse(path, media_type="text/html")
    return Response(content=f"Page not found: {page}", status_code=404)


@app.get("/{path:path}")
async def serve_static(path: str):
    full = os.path.join(WWWROOT, path.lstrip("/"))
    if os.path.isfile(full):
        return FileResponse(full)
    if os.path.isfile(full + ".html"):
        return FileResponse(full + ".html", media_type="text/html")
    return Response(content=f"Not found: {path}", status_code=404)


# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)
