"""Versioned model-free room operations shared by local API and A2A ingress."""

from __future__ import annotations

import re

try:  # package load under protoAgent
    from .store import RoomStore
except ImportError:  # host-free direct module tests
    from store import RoomStore


CONTRACT_VERSION = "1"
OPERATIONS = frozenset({"room.post", "room.sync", "room.ack", "room.members"})
_IDENTITY_FIELDS = frozenset({"principal", "author", "author_principal", "source"})
_PUBLIC_MENTION_FIELDS = (
    "id",
    "room_id",
    "source_message_id",
    "target_principal",
    "token",
    "status",
    "reply_message_id",
    "error",
    "created_at",
    "updated_at",
)


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
    def __init__(self, store: RoomStore, *, dispatch_targets: dict[str, dict] | None = None):
        self.store = store
        self.dispatch_targets = {
            str(principal).casefold(): dict(target)
            for principal, target in (dispatch_targets or {}).items()
            if isinstance(target, dict) and str(target.get("delegate") or "").strip()
        }

    @staticmethod
    def _public_mentions(mentions: list[dict]) -> list[dict]:
        return [{key: mention[key] for key in _PUBLIC_MENTION_FIELDS} for mention in mentions]

    def _resolve_mentions(self, *, room_id: str, principal: str, body: str) -> list[dict]:
        if re.search(r"(?<!\w)@all(?!\w)", body, flags=re.IGNORECASE):
            raise ValueError("@all broadcast is not supported")
        members = self.store.members(room_id=room_id)
        source = next((member for member in members if member["principal"] == principal), None)
        resolved = []
        for member in members:
            target = self.dispatch_targets.get(str(member["principal"]).casefold())
            if target is None:
                continue
            token = str(member["mention_token"])
            if re.search(rf"(?<!\w){re.escape(token)}(?!\w)", body, flags=re.IGNORECASE):
                resolved.append(
                    {
                        "target_principal": member["principal"],
                        "token": token,
                        "delegate_name": str(target["delegate"]).strip(),
                    }
                )
        if resolved and (source is None or not source["can_mention"]):
            raise PermissionError(f"principal {principal!r} may not mention room agents")
        if len(resolved) > 1:
            raise ValueError("multiple agent mentions are deferred")
        return resolved

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
            body = _string(payload, "body", max_length=20000)
            result = self.store.post(
                room_id=room_id,
                principal=bound_principal,
                client_message_id=_string(payload, "client_message_id", max_length=200),
                body=body,
                thread_id=_optional_string(payload, "thread_id", max_length=200),
                reply_to_message_id=_optional_string(payload, "reply_to_message_id", max_length=200),
                mentions=self._resolve_mentions(room_id=room_id, principal=bound_principal, body=body),
            )
            result.setdefault("mentions", [])
            result["mentions"] = self._public_mentions(result["mentions"])
        elif operation == "room.sync":
            result = self.store.sync(
                room_id=room_id,
                after=int(payload.get("after") or 0),
                limit=int(payload.get("limit") or 100),
            )
            result["mentions"] = self._public_mentions(
                self.store.mentions_for_messages([message["id"] for message in result["messages"]])
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
