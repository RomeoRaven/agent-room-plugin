"""Bearer-gated local API router for the native protoAgent Room UI."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

try:  # package load under protoAgent
    from .operations import CONTRACT_VERSION, RoomOperations
    from .store import RoomConflict
except ImportError:  # host-free direct module tests
    from operations import CONTRACT_VERSION, RoomOperations
    from store import RoomConflict


def build_router(operations: RoomOperations, *, local_principal: str) -> APIRouter:
    router = APIRouter()
    principal = str(local_principal or "").strip()
    if not principal:
        raise ValueError("local_principal is required")

    def execute(operation: str, payload: dict) -> dict:
        try:
            return operations.execute(operation, payload, principal=principal)
        except RoomConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/rooms")
    def list_rooms(status: str = Query("active")):
        envelope = execute("room.list", {"status": status})
        rooms = envelope["result"]["rooms"]
        if not rooms:
            if not operations.store.list_rooms(principal=principal, status="all"):
                raise HTTPException(403, f"principal {principal!r} is not a room member")
        return {"contract_version": CONTRACT_VERSION, "rooms": rooms}

    @router.post("/rooms")
    def create_room(payload: dict = Body(...)):
        return execute("room.create", payload)

    @router.patch("/rooms/{room_id}")
    def rename_room(room_id: str, payload: dict = Body(...)):
        return execute("room.rename", {**payload, "room_id": room_id})

    @router.post("/rooms/{room_id}/archive")
    def archive_room(room_id: str):
        return execute("room.archive", {"room_id": room_id})

    @router.post("/rooms/{room_id}/restore")
    def restore_room(room_id: str):
        return execute("room.restore", {"room_id": room_id})

    @router.post("/rooms/{room_id}/reset")
    def reset_room(room_id: str):
        return execute("room.reset", {"room_id": room_id})

    @router.get("/search")
    def search_rooms(
        q: str = Query(..., min_length=1, max_length=500),
        scope: str = Query("current"),
        room_id: str | None = Query(None),
        history: bool = Query(False),
        limit: int = Query(50, ge=1, le=100),
    ):
        return execute(
            "room.search",
            {"query": q, "scope": scope, "room_id": room_id, "history": history, "limit": limit},
        )

    @router.post("/rooms/{room_id}/post")
    def post_message(room_id: str, payload: dict = Body(...)):
        return execute("room.post", {**payload, "room_id": room_id})

    @router.get("/rooms/{room_id}/messages")
    def sync_messages(
        room_id: str,
        after: int | None = Query(None, ge=0),
        before: int | None = Query(None, ge=1),
        around: int | None = Query(None, ge=1),
        limit: int = Query(100, ge=1, le=200),
        history: bool = Query(False),
    ):
        return execute(
            "room.sync",
            {
                "room_id": room_id,
                "after": after,
                "before": before,
                "around": around,
                "limit": limit,
                "history": history,
            },
        )

    @router.post("/rooms/{room_id}/ack")
    def acknowledge(room_id: str, payload: dict = Body(...)):
        return execute("room.ack", {**payload, "room_id": room_id})

    @router.get("/rooms/{room_id}/members")
    def list_members(room_id: str):
        return execute("room.members", {"room_id": room_id})

    return router
