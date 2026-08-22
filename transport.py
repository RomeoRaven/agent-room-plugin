"""Deterministic A2A transport wrapper for agent-room-v1 operations."""

from __future__ import annotations

from a2a.types import Part
from google.protobuf import json_format, struct_pb2

try:  # package load under protoAgent
    from .operations import CONTRACT_VERSION, RoomOperations
except ImportError:  # host-free direct module tests
    from operations import CONTRACT_VERSION, RoomOperations


SKILL_ID = "agent-room-v1"
MIME_TYPE = "application/vnd.romeoraven.agent-room-v1+json"
TRANSPORT_OPERATIONS = frozenset({"room.post", "room.sync", "room.ack", "room.members"})


def _metadata(context) -> dict:
    raw = getattr(context, "metadata", None)
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    try:
        return json_format.MessageToDict(raw)
    except Exception:
        return {}


def _data_part(payload: dict) -> Part:
    part = Part()
    value = struct_pb2.Value()
    json_format.ParseDict(payload, value.struct_value)
    part.data.CopyFrom(value)
    part.metadata.update({"mimeType": MIME_TYPE})
    part.media_type = "application/json"
    return part


def build_handler(operations: RoomOperations, *, peer_principal: str):
    principal = str(peer_principal or "").strip()
    if not principal:
        raise ValueError("peer_principal is required")

    async def handle(context) -> list[Part]:
        envelope = _metadata(context).get("agent_room")
        if not isinstance(envelope, dict):
            raise ValueError("agent_room metadata is required")
        version = str(envelope.get("contract_version") or "").strip()
        if version != CONTRACT_VERSION:
            raise ValueError(f"unsupported agent-room contract version {version!r}")
        operation = str(envelope.get("operation") or "").strip()
        if operation not in TRANSPORT_OPERATIONS:
            raise ValueError(f"unsupported agent-room transport operation {operation!r}")
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("agent_room payload must be an object")
        if operation == "room.sync":
            local_fields = {field for field in ("before", "around", "history") if field in payload}
            if local_fields:
                field = sorted(local_fields)[0]
                raise ValueError(f"unsupported agent-room transport payload field {field!r}")
            payload = {**payload, "after": int(payload.get("after") or 0)}
        result = operations.execute(operation, payload, principal=principal)
        return [_data_part(result)]

    return handle
