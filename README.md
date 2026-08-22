# Agent Room Backend

Durable shared-room backend for [protoAgent](https://github.com/protoLabsAI/protoAgent).

This plugin is intentionally backend-only. It reuses protoAgent's native Room/Fleet Room surface rather than shipping a second chat UI.

## Current slice

- One fixed Agent Organization room (`ao`)
- SQLite-backed ordered messages
- Stable client-message retry deduplication and conflict detection
- Config-bound membership and author identity
- Persistent member cursors
- Gated local `post`, `sync`, `ack`, and `members` API
- Room-visible `mentionable` state so the native composer suggests only configured local dispatch targets
- Deterministic A2A `agent-room-v1` wrapper for the same operations
- Exact configured member-token resolution with durable mention state
- Same-message delivery to multiple explicitly mentioned targets in token order
- Lifecycle-managed named-delegate dispatch keyed by Room/thread
- Reply text persisted before one idempotent attributed same-thread post
- Restart-safe no-replay handling for ambiguous in-flight delegate turns
- Persisted agent-origin chain, parent mention, hop count, cycle/hop/rate blocking
- Sanitized mention delivery state returned through `room.sync`
- Disabled by default

Dispatch is generic and opt-in. Installation-specific names stay in local configuration:

```yaml
agent_room:
  dispatch_targets:
    assistant:
      delegate: assistant_local
    reviewer:
      delegate: reviewer_local
  mention_policy:
    max_agent_hops: 1
    max_mentions_per_target: 5
    rate_window_seconds: 60
```

Each dispatch principal must also be a configured Room member, and each named delegate must already exist in protoAgent. Multiple explicit tokens in one message create one ordered durable mention per target; repeated tokens for the same target still create one mention. Delegate identity plus the Room/thread conversation key keeps ACP sessions separate. Plain text wakes nobody and `@all` remains rejected.

An agent with `can_mention: true` may mention another configured agent from its Room reply. The child mention persists its parent, root message, principal chain, and hop count. Cycles, hop-limit excess, and per-room/per-target rate excess are stored as visible `blocked` mention states and never invoke a delegate. A possible process interruption during delegate invocation becomes visible `ambiguous` state and is not automatically replayed.

Not included yet: cross-host routing, PC1 offline outbox, attachments, dynamic rooms, reactions, search, mutation, or general execution.

## Local-first product boundary

A single protoAgent instance is the complete default product. Installations can keep `peer_principal` empty and use the durable Room, native UI, local members, and local named-delegate mentions without any second device, tunnel, TLS setup, remote credential, or offline queue.

The ownership split is deliberate:

- This plugin owns canonical Room storage, membership, ordering, cursors, exact mention resolution, local delegate dispatch, and the stable `post` / `sync` / `ack` / `members` contract.
- protoAgent's native console owns human interaction: navigation, member selection, mention autocomplete, recipient guidance, transcript rendering, and accessible keyboard behavior.
- An optional peer/client adapter may call the same deterministic Room operations for another device. It owns transport, directional credentials, TLS, advertised URLs, offline outbox, and reconciliation; those concerns do not belong in this plugin's local core.
- A host-local roster adapter maps that host's authoritative agents to Room principals. The Room plugin does not mirror or replace another host's agent identities.

The A2A wrapper stays dormant unless `peer_principal` is explicitly configured. It is a transport seam over the same four operations, not a requirement for local use and not a multi-device lifecycle engine.

## Development

```bash
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
pytest -q
```

See `PROTO.md` for architecture, constraints, and the current acceptance boundary.
