# PROTO.md — agent grounding for agent-room-plugin

Read this before changing anything. This repository is the durable backend owner for protoAgent's shared Agent Room. It deliberately ships no React view: the product reuses and evolves protoAgent's existing native Fleet Room UI.

## Current accepted scope

Version 0.5.x owns migration-safe subject rooms and model-free operations for:

- `room.list` / `room.create` / `room.rename` — discover and manage subject rooms;
- `room.archive` / `room.restore` / `room.reset` — reversible read-only archive and non-destructive Start fresh;
- `room.search` — bounded FTS5 search across current, earlier, active, or archived history;
- `room.post` — canonically order one message and deduplicate retries by bound principal + stable client message id;
- `room.sync` — return recent, older, incremental, or bounded-around-message windows plus sanitized mention state;
- `room.ack` — persist a monotonic member cursor;
- `room.members` — return room-visible membership.

`room.members` adds a derived `mentionable` boolean. It is true only when the member principal is bound to a configured local dispatch target. It does not claim runtime presence or expose the delegate route name.

The plugin exposes a bearer-gated local router at `/api/plugins/agent-room` and, only when `peer_principal` is configured as a member, advertises/handles deterministic A2A skill `agent-room-v1`.

When `dispatch_targets` maps Room member principals to existing protoAgent named delegates, exact member tokens create durable mention records. One source message may address multiple explicit targets in token order; one source/target pair remains unique. The worker invokes only through `PluginHost.invoke_delegate`; delegate identity plus the Room/thread conversation key isolates ACP state, returned reply text is persisted before posting, and local delegate routes are never exposed over HTTP/A2A.

Authorized agent replies may create child mentions. Every mention persists its root source message, parent mention, principal chain, hop count, and source-token position. Configured cycle, hop, and per-room/per-target rate controls create visible `blocked` records and never invoke the target.

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
- Only the canonical Room owner assigns per-room message sequence numbers and may change room lifecycle.
- Preserve stable client-message deduplication; conflicting content under one id must fail.
- Archive and Start fresh must fail while agent delivery is pending; archived rooms reject new posts but preserve idempotent retries of already accepted messages.
- Start fresh never deletes messages. Earlier history remains bounded, searchable, and available on explicit request.
- No cross-host routing, attachments, reactions, per-room roster administration, permanent deletion, autonomous response, execution, approval engine, or Fleet lifecycle in this slice.
- Installation-specific principal/delegate names belong only in local config; public defaults remain empty.
- `@all` is forbidden. Unmentioned agents are silent. One source/target pair creates at most one useful turn/reply.
- Agent-origin mentions require configured `can_mention`; cycles, excess hops, and rate-limit excess fail visibly without delegate work.
- A restart during a possible delegate invocation becomes `ambiguous` and is never automatically replayed; cached `reply_ready` text may be posted idempotently without another invocation.
- Host imports must stay lazy or absent so tests run without a protoAgent checkout.
- Keep manifest and pyproject versions identical.

## Local-first and optional peer ownership

The standalone, single-instance Room is the primary complete product path. `peer_principal` defaults empty; local Room storage, native UI, and local delegate mentions must remain fully usable without A2A admission or any cross-device configuration.

Keep multi-device concerns outside the local core:

| Concern | Owner |
|---|---|
| Canonical rooms/lifecycle/search, messages, membership, cursors, mention records, local dispatch | Agent Room plugin |
| Room switching/lifecycle/search UI, member click, mention picker, recipient guidance, accessible composer | Native protoAgent console |
| Private route, TLS, directional credentials, advertised URLs, offline outbox, reconciliation | Optional peer/client adapter |
| Authoritative agent identity and principal-to-runtime binding | Host-local roster adapter |

The conditional `agent-room-v1` A2A handler remains a deterministic adapter over `post`, `sync`, `ack`, and `members` only. Lifecycle and search stay local-only in this release. Do not grow it into route provisioning, credential distribution, retry scheduling, PC1-specific knowledge, or a second Room owner.

## Gate

```bash
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
pytest -q
```

An installed-host qualification against an exact accepted RR protoAgent commit is separate from host-free repository CI. Enabling/installing on stable PC1 or S1 requires explicit approval.
