# PROTO.md — agent grounding for agent-room-plugin

Read this before changing anything. This repository is the durable backend owner for protoAgent's shared Agent Room. It deliberately ships no React view: the product reuses and evolves protoAgent's existing native Fleet Room UI.

## Current accepted scope

Version 0.2.x owns one fixed `ao` room and four model-free operations:

- `room.post` — canonically order one message and deduplicate retries by bound principal + stable client message id;
- `room.sync` — return a bounded ascending page and sanitized mention state after a canonical sequence;
- `room.ack` — persist a monotonic member cursor;
- `room.members` — return room-visible membership.

The plugin exposes a bearer-gated local router at `/api/plugins/agent-room` and, only when `peer_principal` is configured as a member, advertises/handles deterministic A2A skill `agent-room-v1`.

When `dispatch_targets` maps a Room member principal to an existing protoAgent named delegate, exact member tokens create durable mention records. The worker invokes only through `PluginHost.invoke_delegate`, keys ACP conversation state by Room/thread, persists returned reply text before posting, and never exposes the local delegate route over HTTP/A2A.

## Architecture

| Path | Owner |
|---|---|
| `__init__.py` | Plugin registration, trusted config binding, instance data path, conditional A2A admission. |
| `store.py` | Stdlib SQLite schema and durable ordering/dedup/member/cursor state. No host imports. |
| `operations.py` | Single versioned operation dispatcher shared by HTTP and A2A; caller identity is never accepted from payload. |
| `dispatch.py` | Lifecycle-managed pending mention worker and bounded `room_reply` prompt. |
| `api.py` | Gated plugin API; room comes from URL and principal from plugin config. |
| `transport.py` | A2A metadata validation and structured DataPart response; peer principal is host-configured. |
| `tests/` | Host-free store, operation, API, A2A, registration, and manifest proof. |

## Rules

- Keep the repository backend-only; do not add a second Room UI.
- Keep `enabled: false` by default.
- Never trust `principal`, `author`, `source`, or similar identity fields from request payloads.
- Only the canonical S1 owner assigns message sequence numbers.
- Preserve stable client-message deduplication; conflicting content under one id must fail.
- No dynamic rooms, cross-host routing, multi/agent-to-agent mention, attachments, reactions, search, autonomous response, execution, approval engine, or Fleet lifecycle in this slice.
- Installation-specific principal/delegate names belong only in local config; public defaults remain empty.
- `@all` is forbidden. Unmentioned text is silent. One source/target pair creates at most one useful turn/reply.
- A restart during a possible delegate invocation becomes `ambiguous` and is never automatically replayed; cached `reply_ready` text may be posted idempotently without another invocation.
- Host imports must stay lazy or absent so tests run without a protoAgent checkout.
- Keep manifest and pyproject versions identical.

## Gate

```bash
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
pytest -q
```

An installed-host qualification against an exact accepted RR protoAgent commit is separate from host-free repository CI. Enabling/installing on stable PC1 or S1 requires explicit approval.
