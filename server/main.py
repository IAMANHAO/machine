"""FastAPI 应用入口。

    python -m server            # 开发模式，配合 web/ 的 Vite dev server
    python -m server --build    # 直接伺服 web/dist 的构建产物（单进程，离线可用）

只监听 127.0.0.1：这是一个本地工具，不是一台服务器。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import DEV_ORIGINS, web_dist
from .routers import (
    account, ai, catalog, export, guided, health, knowledge, projects,
    selection,
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="机械设计物料选型引擎",
        description="本地选型服务。所有数值计算由 mds 确定性引擎完成，AI 不参与计算。",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for mod in (health, catalog, selection, projects, knowledge, account, ai,
                guided, export):
        app.include_router(mod.router, prefix="/api")

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        # 不把堆栈吐给前端，但也不假装没事：给一句可读的话 + 类型名
        return JSONResponse(status_code=500, content={
            "detail": {"error": type(exc).__name__,
                       "message": f"服务端异常：{exc}"}})

    _mount_web(app)
    return app


def _mount_web(app: FastAPI) -> None:
    dist = web_dist()
    if not dist:
        @app.get("/")
        def dev_hint() -> dict:
            return {
                "message": "前端尚未构建。开发期请另起 Vite dev server：cd web && npm run dev",
                "api_docs": "/docs",
            }
        return

    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # index.html 必须不缓存：它引用的是带哈希的 bundle 文件名，
        # 缓存住它等于把用户永久钉在旧版本上——升级后界面不变，极难排查
        return FileResponse(dist / "index.html", headers={
            "Cache-Control": "no-cache, no-store, must-revalidate"})


app = create_app()


def main() -> None:
    import uvicorn

    p = argparse.ArgumentParser(prog="server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8756)
    p.add_argument("--reload", action="store_true")
    args = p.parse_args()

    uvicorn.run("server.main:app" if args.reload else app,
                host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
