"""Federation-authenticated deterministic Room RPC for protoAgent v0.146+."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request

try:  # package load under protoAgent
    from .operations import CONTRACT_VERSION, RoomOperations
    from .store import RoomConflict
except ImportError:  # host-free direct module tests
    from operations import CONTRACT_VERSION, RoomOperations
    from store import RoomConflict


FEDERATION_OPERATIONS = frozenset({"room.post", "room.sync", "room.ack", "room.members"})
_ENVELOPE_FIELDS = frozenset({"contract_version", "operation", "payload"})


def _validated_envelope(envelope: dict) -> tuple[str, dict]:
    if not isinstance(envelope, dict):
        raise TypeError("Agent Room envelope must be an object")
    extra = sorted(set(envelope) - _ENVELOPE_FIELDS)
    if extra:
        raise ValueError(f"unsupported Agent Room envelope field {extra[0]!r}")
    version = str(envelope.get("contract_version") or "").strip()
    if version != CONTRACT_VERSION:
        raise ValueError(f"unsupported Agent Room contract version {version!r}")
    operation = str(envelope.get("operation") or "").strip()
    if operation not in FEDERATION_OPERATIONS:
        raise ValueError(f"unsupported Agent Room federation operation {operation!r}")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise TypeError("Agent Room payload must be an object")
    payload = dict(payload)
    if operation == "room.sync":
        local_fields = {field for field in ("before", "around", "history") if field in payload}
        if local_fields:
            raise ValueError(f"unsupported Agent Room federation payload field {sorted(local_fields)[0]!r}")
        payload["after"] = int(payload.get("after") or 0)
    return operation, payload


def build_federation_router(
    operations: RoomOperations,
    *,
    local_principal: str,
    peer_principal: str,
    peer_agent_principals: set[str] | frozenset[str] | None = None,
) -> APIRouter:
    router = APIRouter()
    local = str(local_principal or "").strip()
    peer = str(peer_principal or "").strip()
    if not local or not peer:
        raise ValueError("local_principal and peer_principal are required")
    allowed_agents = {str(value).strip() for value in (peer_agent_principals or set()) if str(value).strip()}

    def execute(request: Request, envelope: dict) -> dict:
        operation, payload = _validated_envelope(envelope)
        tier = str(getattr(request.state, "trust_tier", "") or "").strip()
        if tier == "operator":
            principal = local
        elif tier == "federation":
            principal = peer
            completion_id = str(payload.get("completes_mention_id") or "").strip()
            if operation == "room.post" and completion_id:
                mention = operations.store.mention(completion_id)
                target = str(mention.get("target_principal") or "").strip()
                if target not in allowed_agents or mention.get("delegate_name") != f"remote:{peer}":
                    raise PermissionError(f"mention target {target!r} is not authorized for this federation peer")
                principal = target
        else:
            raise PermissionError("verified operator or federation trust tier is required")
        return operations.execute(operation, payload, principal=principal)

    @router.post("/v1/execute")
    def execute_route(request: Request, envelope: dict = Body(...)):
        try:
            return execute(request, envelope)
        except RoomConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    return router
