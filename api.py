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
    def list_rooms():
        rooms = [
            room
            for room in operations.store.list_rooms()
            if operations.store.is_member(room_id=room["id"], principal=principal)
        ]
        if not rooms:
            raise HTTPException(403, f"principal {principal!r} is not a room member")
        return {"contract_version": CONTRACT_VERSION, "rooms": rooms}

    @router.post("/rooms/{room_id}/post")
    def post_message(room_id: str, payload: dict = Body(...)):
        return execute("room.post", {**payload, "room_id": room_id})

    @router.get("/rooms/{room_id}/messages")
    def sync_messages(room_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
        return execute("room.sync", {"room_id": room_id, "after": after, "limit": limit})

    @router.post("/rooms/{room_id}/ack")
    def acknowledge(room_id: str, payload: dict = Body(...)):
        return execute("room.ack", {**payload, "room_id": room_id})

    @router.get("/rooms/{room_id}/members")
    def list_members(room_id: str):
        return execute("room.members", {"room_id": room_id})

    return router
