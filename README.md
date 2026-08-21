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
- Disabled by default

Not included yet: UI integration, roster `@mention` parsing, agent wake/replies, cross-host routing, attachments, dynamic rooms, reactions, search, or execution.

## Development

```bash
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
pytest -q
```

See `PROTO.md` for architecture, constraints, and the current acceptance boundary.
