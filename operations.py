"""Versioned model-free room operations shared by local and federation APIs."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

try:  # package load under protoAgent
    from .store import RoomStore
except ImportError:  # host-free direct module tests
    from store import RoomStore


CONTRACT_VERSION = "1"
OPERATIONS = frozenset(
    {
        "room.list",
        "room.create",
        "room.rename",
        "room.archive",
        "room.restore",
        "room.reset",
        "room.search",
        "room.post",
        "room.sync",
        "room.ack",
        "room.members",
    }
)
_IDENTITY_FIELDS = frozenset({"principal", "author", "author_principal", "source", "source_principal"})
_PUBLIC_MENTION_FIELDS = (
    "id",
    "room_id",
    "source_message_id",
    "target_principal",
    "token",
    "status",
    "parent_mention_id",
    "origin_message_id",
    "origin_chain",
    "hop_count",
    "position",
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
    def __init__(
        self,
        store: RoomStore,
        *,
        dispatch_targets: dict[str, dict] | None = None,
        mention_policy: dict | None = None,
    ):
        self.store = store
        self.dispatch_targets = {}
        for principal, raw_target in (dispatch_targets or {}).items():
            if not isinstance(raw_target, dict):
                continue
            target = dict(raw_target)
            delegate = str(target.get("delegate") or "").strip()
            remote_peer = str(target.get("remote_peer") or "").strip()
            if bool(delegate) == bool(remote_peer):
                continue
            target["delegate"] = delegate or f"remote:{remote_peer}"
            self.dispatch_targets[str(principal).casefold()] = target
        policy = mention_policy or {}
        self.max_agent_hops = max(0, min(int(policy.get("max_agent_hops", 1)), 10))
        self.max_mentions_per_target = max(1, min(int(policy.get("max_mentions_per_target", 5)), 100))
        self.rate_window_seconds = max(1, min(int(policy.get("rate_window_seconds", 60)), 86400))

    @staticmethod
    def _public_mentions(mentions: list[dict]) -> list[dict]:
        return [{key: mention[key] for key in _PUBLIC_MENTION_FIELDS} for mention in mentions]

    def resolve_mentions(
        self,
        *,
        room_id: str,
        principal: str,
        body: str,
        parent_mention: dict | None = None,
    ) -> list[dict]:
        if re.search(r"(?<!\w)@all(?!\w)", body, flags=re.IGNORECASE):
            raise ValueError("@all broadcast is not supported")
        members = self.store.members(room_id=room_id)
        source = next((member for member in members if member["principal"] == principal), None)
        candidates = []
        parent_chain = [str(item) for item in (parent_mention or {}).get("origin_chain", [])]
        parent_hop = int((parent_mention or {}).get("hop_count") or 0)
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=self.rate_window_seconds)).isoformat()
        for member in members:
            target = self.dispatch_targets.get(str(member["principal"]).casefold())
            if target is None:
                continue
            if source is not None and source["kind"] == "agent" and target.get("remote_peer"):
                continue
            token = str(member["mention_token"])
            for match in re.finditer(rf"(?<!\w){re.escape(token)}(?!\w)", body, flags=re.IGNORECASE):
                candidates.append(
                    {
                        "target_principal": str(member["principal"]),
                        "token": token,
                        "delegate_name": str(target["delegate"]).strip(),
                        "position": match.start(),
                        "_end": match.end(),
                    }
                )
        if candidates and (source is None or not source["can_mention"]):
            raise PermissionError(f"principal {principal!r} may not mention room agents")

        selected = []
        for candidate in sorted(
            candidates,
            key=lambda mention: (
                mention["position"],
                -(mention["_end"] - mention["position"]),
                mention["target_principal"],
            ),
        ):
            if any(
                candidate["position"] < existing["_end"] and candidate["_end"] > existing["position"]
                for existing in selected
            ):
                continue
            selected.append(candidate)
        selected.sort(key=lambda mention: (mention["position"], mention["target_principal"]))
        unique_targets = []
        seen_targets = set()
        for candidate in selected:
            if candidate["target_principal"] in seen_targets:
                continue
            seen_targets.add(candidate["target_principal"])
            unique_targets.append(candidate)

        resolved = []
        for candidate in unique_targets:
            target_principal = candidate["target_principal"]
            hop_count = parent_hop + 1 if parent_mention else 0
            origin_chain = [*parent_chain, target_principal] if parent_mention else [target_principal]
            status = "pending"
            error = None
            if parent_mention and target_principal in parent_chain:
                status = "blocked"
                error = "mention cycle blocked"
            elif parent_mention and hop_count > self.max_agent_hops:
                status = "blocked"
                error = "mention hop limit reached"
            resolved.append(
                {
                    "target_principal": target_principal,
                    "token": candidate["token"],
                    "delegate_name": candidate["delegate_name"],
                    "position": candidate["position"],
                    "status": status,
                    "error": error,
                    "parent_mention_id": (parent_mention or {}).get("id"),
                    "origin_message_id": (parent_mention or {}).get("origin_message_id"),
                    "origin_chain": origin_chain,
                    "hop_count": hop_count,
                    "rate_limit": self.max_mentions_per_target,
                    "rate_since": cutoff,
                }
            )
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

        bound_principal = str(principal or "").strip()
        if not bound_principal:
            raise PermissionError("bound principal is required")
        if operation == "room.list":
            result = {
                "rooms": self.store.list_rooms(
                    principal=bound_principal,
                    status=str(payload.get("status") or "all").strip().lower(),
                )
            }
            return {"contract_version": CONTRACT_VERSION, "operation": operation, "result": result}
        if operation == "room.create":
            result = {
                "room": self.store.create_room(
                    name=_string(payload, "name", max_length=120),
                    principal=bound_principal,
                )
            }
            return {"contract_version": CONTRACT_VERSION, "operation": operation, "result": result}
        if operation == "room.search":
            scope = str(payload.get("scope") or "current").strip().lower()
            search_room_id = str(payload.get("room_id") or "").strip() or None
            if scope == "current" and (
                not search_room_id or not self.store.is_member(room_id=search_room_id, principal=bound_principal)
            ):
                raise PermissionError(f"principal {bound_principal!r} is not a room member")
            result = {
                "results": self.store.search(
                    query=_string(payload, "query", max_length=500),
                    principal=bound_principal,
                    scope=scope,
                    room_id=search_room_id,
                    history=bool(payload.get("history", False)),
                    limit=int(payload.get("limit") or 50),
                )
            }
            return {"contract_version": CONTRACT_VERSION, "operation": operation, "result": result}

        room_id = _string(payload, "room_id", max_length=100)
        if not bound_principal or not self.store.is_member(room_id=room_id, principal=bound_principal):
            raise PermissionError(f"principal {bound_principal!r} is not a room member")

        if operation == "room.rename":
            result = {
                "room": self.store.rename_room(
                    room_id=room_id,
                    name=_string(payload, "name", max_length=120),
                    principal=bound_principal,
                )
            }
        elif operation == "room.archive":
            result = {"room": self.store.archive_room(room_id=room_id, principal=bound_principal)}
        elif operation == "room.restore":
            result = {"room": self.store.restore_room(room_id=room_id, principal=bound_principal)}
        elif operation == "room.reset":
            result = {"room": self.store.reset_room(room_id=room_id, principal=bound_principal)}
        elif operation == "room.post":
            body = _string(payload, "body", max_length=20000)
            completion_id = _optional_string(payload, "completes_mention_id", max_length=200)
            reply_to_message_id = _optional_string(payload, "reply_to_message_id", max_length=200)
            parent_mention = self.store.mention(completion_id) if completion_id else None
            member = next(
                member for member in self.store.members(room_id=room_id) if member["principal"] == bound_principal
            )
            result = self.store.post(
                room_id=room_id,
                principal=bound_principal,
                client_message_id=_string(payload, "client_message_id", max_length=200),
                body=body,
                author_kind=str(member["kind"]),
                thread_id=_optional_string(payload, "thread_id", max_length=200),
                reply_to_message_id=reply_to_message_id,
                mentions=self.resolve_mentions(
                    room_id=room_id,
                    principal=bound_principal,
                    body=body,
                    parent_mention=parent_mention,
                ),
                completes_remote_mention_id=completion_id,
            )
            result.setdefault("mentions", [])
            result["mentions"] = self._public_mentions(result["mentions"])
            if result.get("completed_mention"):
                result["completed_mention"] = self._public_mentions([result["completed_mention"]])[0]
        elif operation == "room.sync":
            result = self.store.sync(
                room_id=room_id,
                after=int(payload["after"]) if payload.get("after") is not None else None,
                before=int(payload["before"]) if payload.get("before") is not None else None,
                around=int(payload["around"]) if payload.get("around") is not None else None,
                limit=int(payload.get("limit") or 100),
                history=bool(payload.get("history", False)),
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
            members = self.store.members(room_id=room_id)
            for member in members:
                member["mentionable"] = str(member["principal"]).casefold() in self.dispatch_targets
            result = {"members": members}

        return {"contract_version": CONTRACT_VERSION, "operation": operation, "result": result}
