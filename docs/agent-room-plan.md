# Agent Room plan

Status: active development

Last updated: 2026-08-22

## The goal

Agent Room is a shared conversation for people and authorized agents.

A person should be able to post one message, mention one or more agents, and see each accepted reply return to the same durable room and thread. The room should survive browser refreshes, process restarts, temporary network loss, and agent restarts without losing history or silently running the same useful turn twice.

The target experience is simple:

1. Open Rooms in the native protoAgent console.
2. Write a normal message or select an exact `@mention`.
3. Plain text becomes part of the conversation and wakes nobody.
4. An exact mention creates one durable delivery for that agent.
5. The intended agent wakes in its own owner context.
6. Its reply appears in the same room and thread with clear attribution.
7. The full exchange remains searchable and readable later.

The difficult work sits behind those seven steps: identity, ordering, recovery, trust, session continuity, loop prevention, and ownership across devices.

## Product principles

### One canonical transcript

One owner instance assigns room IDs, message IDs, sequence numbers, thread IDs, mention IDs, and reply links.

Client devices never create a competing canonical Room database. They may store pending posts, cursors, and delivery claims needed for recovery.

### Exact mentions only

Agent wake-up is opt-in and visible.

- Plain text wakes no agent.
- `@all` is rejected.
- A configured exact token may create one delivery for its target.
- Repeating the same token in one message does not create duplicate work.
- Multiple targets are delivered in source order.

### Replies return to the conversation

Agent output is not complete until the canonical owner has stored one attributed reply linked to the source message and thread.

### Identity comes from owners, not message text

A model cannot declare itself to be another agent.

The owner instance binds the authenticated peer host. The client host resolves its local agent from its authoritative roster. The owner allowlists which principals that peer may attest. An attested agent post must complete that agent's existing canonical mention.

### Conversation is not execution approval

A Room message can ask for work, but it does not grant permission to change files, deploy services, spend money, publish content, or perform other protected actions.

The current roster-reply path is consultation-only and read-only. Broader execution requires a separate approval contract.

### Recovery must be honest

The system distinguishes between work that is safe to retry and work that may already have run.

- A pending post may be retried with the same stable client message ID.
- A saved reply may be posted again with the same stable reply ID.
- A restart during a possible agent invocation becomes an explicit blocked or ambiguous state. It is not automatically invoked again.

## Architecture

Agent Room is split across clear internal owners behind one Fleet Room chat product.

| Layer | Responsibility |
|---|---|
| Native protoAgent Fleet Room | Unified roster and chat surface, transcript, composer, mention picker, lifecycle controls, search, offline and pending state |
| Optional Rooms rail exposure | A second navigation path to the same backend; retained during development and judged separately from the product and transport architecture |
| Agent Room plugin, owner mode | Canonical Room database, lifecycle, search, membership, message ordering, mention resolution, delivery state, attributed replies |
| Agent Room plugin, client mode | Deterministic owner calls, pending-post outbox, acknowledgement and delivery cursors, local mention claims |
| Proposed core `federation_paths` seam | Lets a plugin expose only its own declared route prefix to federation trust while keeping operator APIs unavailable |
| Current `agent-room-v1` A2A contract | Proven development bridge for model-free `post`, `sync`, `ack`, and `members`; removed after federation-route migration |
| Named delegate host service | Stable per-room, per-thread ACP session dispatch |
| Host roster adapter | Live principal resolution, owner context, roster hashes, admission state |
| Deployment owner | Private transport, TLS trust, credentials, service lifecycle, rollback |

## Repository boundary

This repository owns the generic durable backend.

It does not ship another React chat application. The normal UI belongs to protoAgent's native console. Installation-specific agent names, routes, credentials, local paths, and deployment policy remain outside the repository.

Generic console work lives in the protoAgent repository. Generic Room behavior lives here.

## Upstream federation direction

The v0.7 client uses a deterministic A2A skill handler because federation credentials cannot call operator-protected plugin `/api` routes. Upstream protoAgent issue [#2747](https://github.com/protoLabsAI/protoAgent/issues/2747#issuecomment-5382744975) confirmed that this was a real core auth gap and that the A2A bridge was the only safe route available.

The proposed permanent substrate is `federation_paths`, a manifest key modeled after `public_paths`. A plugin may declare which of its own route prefixes lower the auth ceiling from operator to federation trust. Agent Room can then use a normal plugin HTTPS endpoint without giving the client an operator bearer and without wrapping deterministic RPC in an A2A task envelope.

The migration contract is:

1. protoAgent lands and documents `federation_paths` with plugin-prefix validation and trust-tier propagation;
2. Agent Room declares only its deterministic owner route as federation-accessible;
3. owner mode binds its fixed configured peer identity server-side;
4. client mode replaces the A2A JSON-RPC transport with plain authenticated HTTPS;
5. the old handler and envelope are removed after parity proof;
6. the full Room acceptance restarts from the first criterion on the migrated revision.

The two transports will not remain as permanent alternatives.

## Release blockers discovered during review

Before the next release or final acceptance:

- remove the installation hostname from the public `capabilities.network` default;
- replace the misleading stock `0.142.1` compatibility floor with the first upstream revision that actually provides the required seams;
- enforce a read-only runtime policy for local Room delegates instead of relying only on a prompt that requests no mutation.

Per-peer identity lifecycle remains an adjacent federation concern rather than part of this fixed two-host migration.

## What is already built

### Durable fixed Room foundation

Complete and merged.

- SQLite-backed ordered messages.
- Stable client-message deduplication.
- Config-bound members and author identity.
- Monotonic member cursors.
- Gated local API.
- Deterministic A2A operations.
- Backend-only plugin boundary.

### Local exact-mention replies

Complete and merged.

- Exact configured member tokens.
- Mention-only named-delegate dispatch.
- Stable Room and thread conversation keys.
- Reply text persisted before canonical posting.
- Idempotent attributed same-thread replies.
- Plain text silence.

### Multi-agent safety

Complete and merged.

- Multiple named targets in one message.
- Source-order delivery.
- Target deduplication.
- Agent-origin handoffs when explicitly permitted.
- Parent mention and root-message linkage.
- Origin chains and hop counts.
- Cycle, hop, and rate blocking.
- Ambiguous restart state without automatic replay.

### Subject rooms, lifecycle, and search

Complete and merged.

- Multiple subject rooms.
- Create and rename.
- Archive and restore.
- Non-destructive Start fresh.
- Bounded recent and older history.
- SQLite FTS5 search.
- Current, earlier, active, and archived scopes.
- Unread and mention badges.
- Migration of existing fixed-room data without changing canonical IDs.

### Native Rooms UI

Complete and merged in the protoAgent fork.

- Visible Rooms rail entry.
- Full-height native layout.
- Room switcher and lifecycle controls.
- Search and result navigation.
- Pagination and cache isolation.
- Mention picker and recipient guidance.
- Accessible modal, focus, and keyboard behavior.
- Owner controls hidden on client devices.

### Deterministic client mode

Complete and merged.

- A client reads canonical history from the owner.
- The client stores no canonical message cache.
- Offline posts remain visible as pending.
- Pending posts survive restart.
- Recovery uses stable client IDs and strict canonical confirmation.
- Acknowledgement cursors are monotonic.
- Blocking network work stays off the host event loop.
- Lifecycle and search remain owner-local.

## Active development: roster-backed remote replies

The current slice connects an exact Room mention to an authoritative agent owned by another host.

### Intended flow

1. The owner stores a human-authored message containing an exact remote agent token.
2. The owner creates one durable remote mention but does not invoke a local delegate for it.
3. The client scans canonical messages using a separate delivery cursor.
4. The client stores a hash-derived local dispatch claim. It stores no canonical transcript content.
5. A fixed stdin/JSON adapter resolves the exact active, routable, communication-enabled roster code.
6. The adapter returns bounded owner context plus hashes for the exact bytes supplied.
7. protoAgent opens or resumes one read-only ACP session keyed by room, thread, and agent.
8. The agent receives the preloaded owner context and the bounded Room thread. Room reply tools are forbidden.
9. The client resolves and hashes the roster again before attribution.
10. The authenticated peer attests the allowlisted agent in the A2A envelope.
11. The owner atomically inserts the reply and completes the matching mention.

### Safety rules

- Agent-authored messages never create remote roster dispatches.
- The client cannot attest an arbitrary Room member.
- Caller-written identity fields remain forbidden.
- A remote agent post must complete that agent's exact pending mention.
- Mention completion and reply insertion use one database transaction.
- A second reply with another client ID is rejected before it can be committed.
- The roster is checked before and after the agent turn.
- A changed roster record blocks attribution.
- A restart during invocation does not start a second useful turn.
- Tool use, file mutation, shell execution, Git, credentials, and general execution are outside the Room reply contract.

### Current qualification state

The generic implementation is passing its Linux and native Windows plugin suites.

Live qualification has proven:

- authoritative roster resolution and bounded context hashing;
- package integrity and dependency locking;
- ACP initialization, model access, and persistent session creation;
- same-thread session resume after a client restart;
- one exact human mention producing one attributed canonical reply;
- duplicate source replay producing no second wake or reply;
- agent-authored remote mention text producing no dispatch;
- visible bounded failure when an agent turn exceeds its deadline;
- interrupted-invocation restart producing a visible blocked reply without replay;
- Room replies completing without tool use;
- delegate process-tree cleanup;
- native Room UI visibility, attribution, mention availability, and zero browser errors;
- owner, client, transport, and unrelated stable-runtime health after restart.

The first live model turn exposed a service-context shell-read problem. The agent eventually produced the requested answer, but its read tools stalled past the Room reply deadline. The accepted correction preloads the exact bounded owner files through the roster adapter and forbids Room reply tool use. The corrected path returned the requested reply through the same persistent ACP session in a few seconds.

The roster-backed path is merged in v0.7.0 and passed installed-host plus exact-merged replay on the current A2A bridge. That proof remains the regression baseline for the federation-route migration.

## Acceptance

### Roster reply acceptance

Development-bridge status: passed.

- one exact human `@mention` creates one canonical mention;
- only the intended authoritative agent wakes;
- the agent returns one expected marker;
- the marker appears once in the same canonical room and thread;
- an unmentioned agent remains silent;
- an agent-authored remote mention produces no dispatch;
- a second human message in the same thread resumes the same ACP session;
- duplicate delivery produces no second wake or reply;
- client restart preserves completed state;
- restart during possible invocation blocks replay;
- roster removal, inactivity, ambiguity, or hash change fails closed;
- timeout and adapter failure produce visible bounded failure behavior;
- process cleanup leaves no orphan delegate tree;
- stable unrelated runtimes remain unchanged.

These criteria must pass again after `federation_paths` replaces the A2A bridge.

### Cross-host Room acceptance

Status: paused at `ROOM_REWORK` pending transport migration.

Step 8 paused at `ROOM_REWORK` after this exact checkpoint on the bridge:

1. Shared parity passed: both clients returned the same canonical messages, mentions, members, and delivery state.
2. Client-to-owner mention passed: a client-origin exact mention of an owner-side agent returned one attributed reply.
3. Owner-to-client mention passed: an owner-origin exact mention of a client-side agent returned one attributed reply.
4. Multiple mentions passed: one source-ordered two-agent message returned distinct replies in the same order.

The next plain message was stored with zero mentions, beginning the fifth criterion, but its full no-wake observation was not completed before upstream architecture review paused the bundle. Criteria 5 through 12 were not run. The complete migrated Room path must restart from the first criterion and prove:

- human on either client can post to the same canonical transcript;
- human can mention an agent owned by either side;
- multiple exact mentions produce ordered independent replies;
- agent-to-agent mention stays within cycle and hop limits;
- temporary owner outage preserves pending client posts;
- temporary client outage preserves canonical mentions;
- owner, client, and agent restarts do not duplicate useful work;
- credentials reject the wrong peer and wrong principal;
- context remains bounded;
- every reply is visible and attributed.

The project receives `ROOM_ACCEPTED` only after the complete scenario passes on the permanent transport. Any missing criterion leaves it at `ROOM_REWORK`.

## Next milestones

### 1. Land the upstream federation substrate

Confirm the final `federation_paths` contract, compatibility floor, trust-tier propagation, and accepted named-delegate host seam.

### 2. Migrate and harden the plugin

- replace A2A JSON-RPC client transport with the federation-authenticated plugin route;
- remove the deterministic A2A handler after parity proof;
- remove the installation hostname from public defaults;
- set an honest compatibility floor;
- enforce read-only local Room delegates at runtime.

### 3. Restart complete shared Room acceptance

Use real clients and authoritative agents to repeat all criteria, including multiple mentions, offline recovery, restart, deduplication, attribution, security rejection, and process cleanup.

### 4. Document the accepted release

When the complete Room passes, the repository will update its release status, platform evidence, known limitations, and compatibility notes. Installation and rollout policy remains the responsibility of each deployment owner.

## Deferred work

These are outside the current Room goal:

- attachments and reactions;
- permanent message deletion;
- per-room roster administration;
- unrestricted remote execution;
- approval challenges for protected actions;
- a general-purpose cross-host task bus;
- merging Fleet process lifecycle into Room state;
- replacing authoritative host rosters with plugin-owned identities.

## Definition of done

Agent Room is done when a person can rely on one shared conversation instead of coordinating several agent chats by hand.

That means:

- one canonical transcript;
- exact and intentional wake-up;
- correct owner context;
- visible attributed replies;
- no silent duplicate turns;
- honest offline and restart behavior;
- bounded loops and context;
- no identity supplied by model text;
- no second Room owner;
- no hidden expansion from conversation into execution authority.

Until all of those hold in the real installed topology, this remains active development.

## Related reading

- [Fleet Room and Agent Room](fleet-room-vs-agent-room.md)
- [Repository README](../README.md)
- [Agent grounding and boundaries](../PROTO.md)
