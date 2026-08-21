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
- Deterministic A2A `agent-room-v1` wrapper for the same operations
- Exact configured member-token resolution with durable mention state
- Lifecycle-managed named-delegate dispatch keyed by Room/thread
- Reply text persisted before one idempotent attributed same-thread post
- Restart-safe no-replay handling for ambiguous in-flight delegate turns
- Sanitized mention delivery state returned through `room.sync`
- Disabled by default

Dispatch is generic and opt-in. Installation-specific names stay in local configuration:

```yaml
agent_room:
  dispatch_targets:
    hermes:
      delegate: hermes_s1
```

The dispatch principal must also be a configured Room member, and the named delegate must already exist in protoAgent. Plain text wakes nobody. `@all` and multiple automatic targets are rejected in this slice. A possible process interruption during delegate invocation becomes visible `ambiguous` state and is not automatically replayed.

Not included yet: cross-host routing, multiple or agent-to-agent mentions, PC1 offline outbox, attachments, dynamic rooms, reactions, search, mutation, or general execution.

## Development

```bash
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
pytest -q
```

See `PROTO.md` for architecture, constraints, and the current acceptance boundary.
