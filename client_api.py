"""Local native-Room API surface for deterministic client mode."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

try:
    from .client import ClientConflict, ClientRoomService, PeerRejected, PeerUnavailable
except ImportError:
    from client import ClientConflict, ClientRoomService, PeerRejected, PeerUnavailable


def build_client_router(service: ClientRoomService) -> APIRouter:
    router = APIRouter()

    def execute(work):
        try:
            return work()
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ClientConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except PeerRejected as exc:
            raise HTTPException(502, "Room owner rejected the configured peer credential") from exc
        except PeerUnavailable as exc:
            raise HTTPException(503, "Room owner unavailable") from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/rooms")
    def list_rooms(status: str = Query("active")):
        if status not in {"active", "all"}:
            return {"contract_version": "1", "rooms": []}
        return service.list_rooms()

    @router.post("/rooms/{room_id}/post")
    def post(room_id: str, payload: dict = Body(...)):
        return execute(lambda: service.post(room_id, payload))

    @router.get("/rooms/{room_id}/messages")
    def sync(room_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
        return execute(lambda: service.sync(room_id, after=after, limit=limit))

    @router.post("/rooms/{room_id}/ack")
    def ack(room_id: str, payload: dict = Body(...)):
        return execute(lambda: service.ack(room_id, int(payload.get("sequence") or 0)))

    @router.get("/rooms/{room_id}/members")
    def members(room_id: str):
        return execute(lambda: service.members(room_id))

    return router
