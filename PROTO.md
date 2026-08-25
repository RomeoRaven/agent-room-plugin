# PROTO.md — agent grounding for agent-room-plugin

Read this before changing anything. This repository owns the complete self-contained Agent Room product for protoAgent: durable backend, browser console view, policy, tests, and release compatibility. protoAgent core owns only generic plugin-host seams.

## Current accepted scope

Version 0.8.x owns migration-safe subject rooms, deterministic federation peer-client state, roster-bound client dispatch, and model-free operations for:

- `room.list` / `room.create` / `room.rename` — discover and manage subject rooms;
- `room.archive` / `room.restore` / `room.reset` — reversible read-only archive and non-destructive Start fresh;
- `room.search` — bounded FTS5 search across current, earlier, active, or archived history;
- `room.post` — canonically order one message and deduplicate retries by bound principal + stable client message id;
- `room.sync` — return recent, older, incremental, or bounded-around-message windows plus sanitized mention state;
- `room.ack` — persist a monotonic member cursor;
- `room.members` — return room-visible membership.

`room.members` adds a derived `mentionable` boolean. It is true only when the member principal is bound to a configured local dispatch target. It does not claim runtime presence or expose the delegate route name.

The plugin exposes an operator-gated local router at `/api/plugins/agent-room` and, only when `peer_principal` is configured as a member, a federation-authenticated `POST /api/plugins/agent-room/v1/execute` route for `post`, `sync`, `ack`, and `members`.

When `dispatch_targets` maps Room member principals to existing protoAgent named delegates, exact member tokens create durable mention records. One source message may address multiple explicit targets in token order; one source/target pair remains unique. The worker invokes only through `PluginHost.invoke_delegate(..., permissions="readonly")`; delegate identity plus the Room/thread conversation key isolates ACP state, returned reply text is persisted before posting, and local delegate routes are never exposed over HTTP.

Authorized agent replies may create child mentions. For a client-side roster agent, one human/host source may authorize a new child wake without directly mentioning that target by including exactly one `[handoff:<principal>]` directive. The client resolves the principal against owner-provided membership, requires an agent-kind currently mentionable target, maps it to the exact configured token, and permits that token once; unknown, non-wakeable, repeated/ambiguous, agent-authored, and `@all` directives remain blocked. Every mention persists its root source message, parent mention, principal chain, hop count, and source-token position. Configured cycle, hop, and per-room/per-target rate controls create visible `blocked` records and never invoke the target.

## Architecture

| Path | Owner |
|---|---|
| `__init__.py` | Plugin registration, trusted config binding, instance data path, conditional federation route admission. |
| `store.py` | Stdlib SQLite schema and durable ordering/dedup/member/cursor state. No host imports. |
| `operations.py` | Single versioned operation dispatcher shared by local and federation HTTP; caller identity is never accepted from payload. |
| `dispatch.py` | Lifecycle-managed pending mention worker and bounded `room_reply` prompt. |
| `api.py` | Gated plugin API; room comes from URL and principal from plugin config. |
| `federation.py` | Trust-tier binding, exact envelope validation, transport operation ceiling, and canonical mention-derived remote agent attribution. |
| `client.py` | Optional bounded HTTPS federation client, pending-post/cursor/local-dispatch SQLite state, and reconciliation surface. |
| `client_dispatch.py` | Live roster-bound client mention claim, bounded context, ACP wake, source recheck, and exact attributed reply. |
| `resolver.py` | Fixed-argv bounded stdin/JSON bridge to the host-owned roster resolver. |
| `client_api.py` | Fixed-room local API adapter for the plugin UI; no lifecycle/search or canonical storage. |
| `view.py`, `web/` | First-class plugin view and assets for transcript, roster, profiles, mentions, lifecycle, and search. |
| `tests/` | Host-free store, operation, local/federation API, client, registration, manifest, and UI contract proof. |

## Rules

- Keep Agent Room product behavior self-contained here; do not add Agent Room-specific UI, policy, API types, or navigation to protoAgent core.
- Keep `enabled: false` by default.
- Never trust `principal`, `author`, `source`, or similar identity fields from request payloads or envelopes. Operator/federation identity comes from `request.state.trust_tier`; a remote agent author is derived only from an existing canonical remote mention and the configured peer-agent allowlist.
- Only the canonical Room owner assigns per-room message sequence numbers and may change room lifecycle.
- Preserve stable client-message deduplication; conflicting content under one id must fail.
- Archive and Start fresh must fail while agent delivery is pending; archived rooms reject new posts but preserve idempotent retries of already accepted messages.
- Start fresh never deletes messages. Earlier history remains bounded, searchable, and available on explicit request.
- Client mode may consume a pre-provisioned private HTTPS federation route and directional credential; it never provisions routes/secrets or exposes lifecycle/search remotely.
- No attachments, reactions, per-room roster administration, permanent deletion, autonomous unmentioned response, general execution, approval engine, or Fleet lifecycle in this slice.
- Installation-specific principal/delegate names belong only in local config; public defaults remain empty.
- Operator-authenticated `human` and `host` members with `can_mention` may use `@all`; it expands once to every configured dispatch-target agent in deterministic Room-member order. Agent-authored `@all` is forbidden. Unmentioned agents are silent. One source/target pair creates at most one useful turn/reply.
- Agent-origin mentions require configured `can_mention`; cycles, excess hops, and rate-limit excess fail visibly without delegate work.
- A restart during a possible delegate invocation becomes `ambiguous` and is never automatically replayed; cached `reply_ready` text may be posted idempotently without another invocation.
- Host imports must stay lazy or absent so tests run without a protoAgent checkout.
- Keep manifest and pyproject versions identical.

## Local-first and optional peer ownership

The standalone, single-instance Room is the primary complete product path. `peer_principal` defaults empty; local Room storage, plugin UI, and local delegate mentions must remain fully usable without federation route admission or any cross-device configuration.

Keep multi-device concerns outside the local core:

| Concern | Owner |
|---|---|
| Canonical rooms/lifecycle/search, messages, membership, cursors, mention records, local dispatch | Agent Room plugin |
| Room switching/lifecycle/search UI, member click, profiles, mention insertion, recipient guidance, accessible composer | Agent Room plugin view |
| Plugin installation, authenticated routing, sandboxed rail hosting, theme/kit integration | Generic protoAgent core seams |
| Private route, TLS, directional credentials, advertised URLs | Host deployment owner |
| Pending-post outbox, cursor state, deterministic reconciliation, local dispatch claim | Plugin client mode |
| Authoritative agent identity and principal-to-runtime binding | Host-local roster adapter |

The conditional owner-side federation route and client-side HTTPS caller remain deterministic adapters over `post`, `sync`, `ack`, and `members` only. Lifecycle and search stay owner-local. Client state contains pending posts, cursors, and local dispatch claims only; it never caches canonical messages, assigns sequence numbers, provisions routes, distributes credentials, or becomes a second Room owner. Roster identity is resolved live from the host adapter and is not copied into client state.

## Gate

```bash
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
pytest -q
```

An installed-host qualification against an exact accepted RR protoAgent commit is separate from host-free repository CI. Enabling/installing on stable PC1 or S1 requires explicit approval.
