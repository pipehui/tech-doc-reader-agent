from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


FRONTEND_UNAVAILABLE_DETAIL = (
    "Frontend build is unavailable. Run `npm ci && npm run build` in `frontend/`, "
    "or use the Vite development server at http://127.0.0.1:5173."
)
FRONTEND_ROUTES = ("/", "/studio", "/inspector", "/learner")


def install_frontend(
    app: FastAPI,
    *,
    dist_dir: Path,
    graphs_dir: Path | None = None,
) -> None:
    """Install production-only frontend and graph static routes on an app."""

    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=assets_dir),
            name="frontend-assets",
        )

    if graphs_dir is not None and graphs_dir.is_dir():
        app.mount(
            "/graphs",
            StaticFiles(directory=graphs_dir),
            name="graphs",
        )

    serve_index = _frontend_index_endpoint(dist_dir)
    for route in FRONTEND_ROUTES:
        app.add_api_route(
            route,
            serve_index,
            methods=["GET"],
            include_in_schema=False,
        )


def _frontend_index_endpoint(dist_dir: Path) -> Callable[[], FileResponse]:
    def frontend_index() -> FileResponse:
        index_file = dist_dir / "index.html"
        if not index_file.is_file():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=FRONTEND_UNAVAILABLE_DETAIL,
            )
        return FileResponse(index_file)

    return frontend_index


__all__ = ["FRONTEND_UNAVAILABLE_DETAIL", "install_frontend"]
