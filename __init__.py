"""agent-room-plugin entrypoint: durable backend only, no duplicate UI."""

from __future__ import annotations

import os
from pathlib import Path

try:  # package load under protoAgent
    from .api import build_router
    from .operations import RoomOperations
    from .store import RoomStore
    from .transport import SKILL_ID, build_handler
except ImportError:  # host-free pytest imports root __init__ directly
    from api import build_router
    from operations import RoomOperations
    from store import RoomStore
    from transport import SKILL_ID, build_handler


def _data_dir(config: dict) -> Path:
    override = str(config.get("data_dir") or os.environ.get("AGENT_ROOM_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from infra.paths import instance_paths

        return instance_paths().store("agent-room")
    except Exception:
        base = Path(os.environ.get("PROTOAGENT_HOME") or "~/.protoagent/default").expanduser()
        return base / "agent-room"


def register(registry) -> None:
    config = dict(getattr(registry, "config", None) or {})
    owner = config.get("owner") if isinstance(config.get("owner"), dict) else None
    members = config.get("members") if isinstance(config.get("members"), list) else []
    local_principal = str(config.get("local_principal") or (owner or {}).get("principal") or "operator").strip()
    peer_principal = str(config.get("peer_principal") or "").strip()

    store = RoomStore(_data_dir(config) / "agent-room.db", owner=owner, members=members)
    operations = RoomOperations(store)
    registry.register_router(
        build_router(operations, local_principal=local_principal),
        prefix="/api/plugins/agent-room",
    )

    # No configured peer means local owner mode only. Do not advertise a skill
    # whose request would otherwise fall through to the normal model loop.
    if not peer_principal:
        return
    if not store.is_member(room_id="ao", principal=peer_principal):
        raise ValueError(f"peer_principal {peer_principal!r} must be a configured room member")
    if not callable(getattr(registry, "register_a2a_handler", None)):
        raise RuntimeError("protoAgent host lacks deterministic A2A handler support")

    registry.register_a2a_skill(
        {
            "id": SKILL_ID,
            "name": "Agent Room",
            "description": "Deterministic durable room post, sync, acknowledgement, and membership operations.",
            "tags": ["room", "collaboration", "deterministic"],
        }
    )
    registry.register_a2a_handler(SKILL_ID, build_handler(operations, peer_principal=peer_principal))
