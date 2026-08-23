"""agent-room-plugin entrypoint: durable backend only, no duplicate UI."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

try:  # package load under protoAgent
    from .api import build_router
    from .client import ClientRoomService, ClientState, FederationPeer, PeerReconciler
    from .client_dispatch import ClientMentionSurface, ClientMentionWorker
    from .client_api import build_client_router
    from .dispatch import MentionSurface, MentionWorker
    from .federation import build_federation_router
    from .operations import RoomOperations
    from .resolver import RosterResolver
    from .store import RoomStore
except ImportError:  # host-free pytest imports root __init__ directly
    from api import build_router
    from client import ClientRoomService, ClientState, FederationPeer, PeerReconciler
    from client_dispatch import ClientMentionSurface, ClientMentionWorker
    from client_api import build_client_router
    from dispatch import MentionSurface, MentionWorker
    from federation import build_federation_router
    from operations import RoomOperations
    from resolver import RosterResolver
    from store import RoomStore


def _data_dir(config: dict) -> Path:
    override = str(config.get("data_dir") or os.environ.get("AGENT_ROOM_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from infra.paths import instance_paths

        return instance_paths().store("agent-room")
    except ImportError:
        base = Path(os.environ.get("PROTOAGENT_HOME") or "~/.protoagent/default").expanduser()
        return base / "agent-room"


def register(registry) -> None:
    config = dict(getattr(registry, "config", None) or {})
    mode = str(config.get("mode") or "owner").strip().casefold()
    if mode not in {"owner", "client"}:
        raise ValueError("agent_room mode must be owner or client")
    if mode == "client":
        data_dir = _data_dir(config)
        state = ClientState(data_dir / "agent-room-client.db")
        dispatch_targets = config.get("dispatch_targets") if isinstance(config.get("dispatch_targets"), dict) else {}
        peer = FederationPeer(
            str(config.get("peer_url") or ""),
            Path(str(config.get("peer_token_file") or "")).expanduser(),
            timeout=float(config.get("peer_timeout_seconds") or 30),
            ca_file=Path(str(config.get("peer_ca_file"))).expanduser() if config.get("peer_ca_file") else None,
        )
        service = ClientRoomService(state, peer, local_mention_targets=set(dispatch_targets))
        reconciler = PeerReconciler(service, interval=float(config.get("reconcile_interval_seconds") or 5))
        registry.register_router(build_client_router(service), prefix="/api/plugins/agent-room")
        registry.register_surface(reconciler.start, reconciler.stop, name="peer-reconciliation")
        if dispatch_targets:
            resolver_configs = []
            for principal, target in dispatch_targets.items():
                if not isinstance(target, dict):
                    raise ValueError("each client dispatch target must be an object")
                agent_code = str(target.get("agent_code") or "").strip()
                delegate = str(target.get("delegate") or "").strip()
                resolver_config = target.get("resolver") if isinstance(target.get("resolver"), dict) else None
                if (
                    not agent_code
                    or agent_code.casefold() != str(principal).casefold()
                    or not delegate
                    or resolver_config is None
                ):
                    raise ValueError(
                        "each client dispatch target must bind its exact agent code, delegate, and resolver"
                    )
                resolver_configs.append(resolver_config)
            if any(candidate != resolver_configs[0] for candidate in resolver_configs[1:]):
                raise ValueError("all client dispatch targets must share one live roster resolver")
            resolver_config = resolver_configs[0]
            command = str(resolver_config.get("command") or "").strip()
            args = resolver_config.get("args") if isinstance(resolver_config.get("args"), list) else []
            if not command:
                raise ValueError("client roster resolver command is required")
            host = getattr(registry, "host", None)
            invoke_delegate = getattr(host, "invoke_delegate", None)
            if not callable(invoke_delegate):
                raise RuntimeError("configured client Room dispatch requires the named-delegate host service")
            resolver = RosterResolver(
                command,
                args,
                timeout=float(resolver_config.get("timeout_seconds") or 5),
                max_output_bytes=int(resolver_config.get("max_output_bytes") or 65536),
                env=resolver_config.get("env") if isinstance(resolver_config.get("env"), dict) else None,
            )
            typed_invoke = cast(Callable[..., Awaitable[str]], invoke_delegate)
            client_surface = ClientMentionSurface(
                ClientMentionWorker(
                    state,
                    peer=peer,
                    resolver=resolver,
                    invoke_delegate=typed_invoke,
                    targets=dispatch_targets,
                )
            )
            registry.register_surface(client_surface.start, client_surface.stop, name="client-mention-delivery")
        return
    owner = config.get("owner") if isinstance(config.get("owner"), dict) else None
    members = config.get("members") if isinstance(config.get("members"), list) else []
    local_principal = str(config.get("local_principal") or (owner or {}).get("principal") or "operator").strip()
    peer_principal = str(config.get("peer_principal") or "").strip()
    dispatch_targets = config.get("dispatch_targets") if isinstance(config.get("dispatch_targets"), dict) else {}
    raw_peer_agents = config.get("peer_agent_principals")
    peer_agent_principals = raw_peer_agents if isinstance(raw_peer_agents, list) else []
    mention_policy = config.get("mention_policy") if isinstance(config.get("mention_policy"), dict) else {}

    store = RoomStore(_data_dir(config) / "agent-room.db", owner=owner, members=members)
    operations = RoomOperations(store, dispatch_targets=dispatch_targets, mention_policy=mention_policy)
    registry.register_router(
        build_router(operations, local_principal=local_principal),
        prefix="/api/plugins/agent-room",
    )

    if dispatch_targets:
        local_targets = {}
        for principal, target in dispatch_targets.items():
            delegate_name = str(target.get("delegate") or "").strip() if isinstance(target, dict) else ""
            remote_peer = str(target.get("remote_peer") or "").strip() if isinstance(target, dict) else ""
            if not store.is_member(room_id="ao", principal=str(principal)) or bool(delegate_name) == bool(remote_peer):
                raise ValueError(
                    "each dispatch target must bind a configured room member to exactly one named delegate or remote peer"
                )
            if delegate_name:
                local_targets[str(principal)] = target
        if local_targets:
            host = getattr(registry, "host", None)
            invoke_delegate = getattr(host, "invoke_delegate", None)
            if not callable(invoke_delegate):
                raise RuntimeError("configured local Room dispatch requires the named-delegate host service")
            typed_invoke = cast(Callable[..., Awaitable[str]], invoke_delegate)
            surface = MentionSurface(
                MentionWorker(store, invoke_delegate=typed_invoke, resolve_mentions=operations.resolve_mentions)
            )
            registry.register_surface(surface.start, surface.stop, name="mention-delivery")

    # No configured peer means local owner mode only. Do not mount a federation
    # route whose server-bound identity would otherwise be undefined.
    if not peer_principal:
        if dispatch_targets and any(
            isinstance(target, dict) and str(target.get("remote_peer") or "").strip()
            for target in dispatch_targets.values()
        ):
            raise ValueError("remote dispatch targets require a configured peer_principal")
        return
    if not store.is_member(room_id="ao", principal=peer_principal):
        raise ValueError(f"peer_principal {peer_principal!r} must be a configured room member")
    room_members = {member["principal"]: member for member in store.members(room_id="ao")}
    peer_member = room_members[peer_principal]
    allowed_peer_agents = {str(value).strip() for value in peer_agent_principals if str(value).strip()}
    for agent_principal in allowed_peer_agents:
        agent_member = room_members.get(str(agent_principal))
        if (
            agent_member is None
            or agent_member["kind"] != "agent"
            or not agent_member["can_post"]
            or agent_member["host"] != peer_member["host"]
        ):
            raise ValueError("each peer agent principal must be a configured agent member on the peer host")
    for principal, target in dispatch_targets.items():
        remote_peer = str(target.get("remote_peer") or "").strip() if isinstance(target, dict) else ""
        if not remote_peer:
            continue
        if remote_peer != peer_principal:
            raise ValueError("each remote dispatch target must belong to the configured peer")
        if str(principal) not in allowed_peer_agents:
            raise ValueError("each remote dispatch target must be allowlisted as a peer agent principal")
    registry.register_router(
        build_federation_router(
            operations,
            local_principal=local_principal,
            peer_principal=peer_principal,
            peer_agent_principals=allowed_peer_agents,
        ),
        prefix="/api/plugins/agent-room",
    )
