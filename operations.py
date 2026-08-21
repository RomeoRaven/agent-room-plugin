"""Versioned model-free room operations shared by local API and A2A ingress."""

from __future__ import annotations

try:  # package load under protoAgent
    from .store import RoomStore
except ImportError:  # host-free direct module tests
    from store import RoomStore


CONTRACT_VERSION = "1"
OPERATIONS = frozenset({"room.post", "room.sync", "room.ack", "room.members"})
_IDENTITY_FIELDS = frozenset({"principal", "author", "author_principal", "source"})


def _string(payload: dict, key: str, *, max_length: int | None = None) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    if max_length is not None and len(value) > max_length:
        raise ValueError(f"{key} exceeds {max_length} characters")
    return value


def _optional_string(payload: dict, key: str, *, max_length: int) -> str | None:
    value = str(payload.get(key) or "").strip()
    if not value:
        return None
    if len(value) > max_length:
        raise ValueError(f"{key} exceeds {max_length} characters")
    return value


class RoomOperations:
    def __init__(self, store: RoomStore):
        self.store = store

    def execute(self, operation: str, payload: dict, *, principal: str) -> dict:
        operation = str(operation or "").strip()
        if operation not in OPERATIONS:
            raise ValueError(f"unsupported room operation {operation!r}")
        if not isinstance(payload, dict):
            raise ValueError("room operation payload must be an object")
        forbidden = sorted(_IDENTITY_FIELDS.intersection(payload))
        if forbidden:
            raise ValueError(f"identity fields are host-bound and forbidden in payload: {', '.join(forbidden)}")

        room_id = _string(payload, "room_id", max_length=100)
        bound_principal = str(principal or "").strip()
        if not bound_principal or not self.store.is_member(room_id=room_id, principal=bound_principal):
            raise PermissionError(f"principal {bound_principal!r} is not a room member")

        if operation == "room.post":
            result = self.store.post(
                room_id=room_id,
                principal=bound_principal,
                client_message_id=_string(payload, "client_message_id", max_length=200),
                body=_string(payload, "body", max_length=20000),
                thread_id=_optional_string(payload, "thread_id", max_length=200),
                reply_to_message_id=_optional_string(payload, "reply_to_message_id", max_length=200),
            )
        elif operation == "room.sync":
            result = self.store.sync(
                room_id=room_id,
                after=int(payload.get("after") or 0),
                limit=int(payload.get("limit") or 100),
            )
        elif operation == "room.ack":
            result = self.store.ack(
                room_id=room_id,
                principal=bound_principal,
                sequence=int(payload.get("sequence") or 0),
            )
        else:
            result = {"members": self.store.members(room_id=room_id)}

        return {"contract_version": CONTRACT_VERSION, "operation": operation, "result": result}
