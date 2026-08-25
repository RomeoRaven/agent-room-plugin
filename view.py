"""Public chrome and immutable assets for the Agent Room console view."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

WEB = Path(__file__).with_name("web")
_CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}


def build_view_router() -> APIRouter:
    router = APIRouter()

    @router.get("/view", include_in_schema=False)
    def view() -> FileResponse:
        return FileResponse(WEB / "room.html", media_type="text/html", headers={"Cache-Control": "no-cache"})

    @router.get("/assets/room.css", include_in_schema=False)
    def css() -> FileResponse:
        return FileResponse(WEB / "room.css", media_type="text/css", headers=_CACHE)

    @router.get("/assets/room.js", include_in_schema=False)
    def javascript() -> FileResponse:
        return FileResponse(WEB / "room.js", media_type="text/javascript", headers=_CACHE)

    return router
