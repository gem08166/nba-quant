from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .api import router
from .config import API_TOKEN, WEB_DIR


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="NBA Quant", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def token_auth(request: Request, call_next):
    if API_TOKEN and request.url.path.startswith("/api"):
        header = request.headers.get("authorization", "")
        token = header.removeprefix("Bearer ").strip() or request.query_params.get("token", "")
        if token != API_TOKEN:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


app.include_router(router)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """静态 JS/CSS 每次协商缓存：避免服务端代码更新后浏览器仍喂旧脚本。"""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") and path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _no_cache_response(path: str, media_type: str) -> FileResponse:
    """HTML/JS/CSS/SW：每次都重新验证（ETag 协商），避免浏览器启发式缓存
    把旧脚本喂给新会话（实测静态文件更新后新 tab 仍加载旧 JS）。"""
    resp = FileResponse(WEB_DIR / path, media_type=media_type)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return _no_cache_response("index.html", "text/html")


@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return _no_cache_response("manifest.webmanifest", "application/manifest+json")


@app.get("/sw.js")
def service_worker() -> FileResponse:
    return _no_cache_response("sw.js", "application/javascript")


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(WEB_DIR / "icon.svg", media_type="image/svg+xml")
