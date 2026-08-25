# Fleet Room and Agent Room

Fleet Room and Agent Room are separate product surfaces with separate authorities.

- **Fleet Room** operates agent processes: discovery, lifecycle, reachability, per-agent consoles, direct chat, and fleet activity.
- **Agent Room** provides durable group collaboration: canonical rooms and transcripts, membership, exact mentions, attributed replies, delivery state, profiles, lifecycle, recovery, and search.

They may use the same host roster and generic protoAgent capabilities, but neither owns or embeds the other.

## Capability boundary

| Area | Fleet Room | Agent Room |
|---|---|---|
| Primary job | Operate and navigate a fleet | Maintain a shared durable conversation |
| Authoritative state | Fleet registry, workspace state, process health | Room database, ordering, membership, mentions, delivery state |
| Agent lifecycle | Start, stop, discover, and open agents | Not owned |
| Communication | Direct agent chat and operational fan-out | Canonical shared room/thread transcript |
| Addressing | UI-selected fleet target | Exact configured mention token |
| Replies | Remain with each agent's turn or chat | Return to the canonical room and thread |
| Plain text | Fleet-defined behavior | Posts without waking an agent |
| History | Per-agent chat and fleet activity | Room history, earlier context, archive, and FTS search |
| Room lifecycle | Not owned | Create, rename, archive, restore, and non-destructive Start fresh |
| Execution authority | Host and agent policy | Room content alone grants no protected-action approval |

## Permanent ownership

Agent Room is a self-contained installable protoAgent plugin. It owns:

- its first-class `Rooms` console view and immutable browser assets;
- canonical rooms, messages, membership, and sequence assignment;
- transcript rendering, roster presentation, profiles, and exact mention insertion;
- lifecycle controls, bounded history, search, acknowledgement, and delivery state;
- local deterministic named-delegate dispatch and optional peer-client reconciliation;
- Room-specific tests, documentation, release compatibility, and maintenance.

protoAgent core owns only reusable extension seams:

- plugin discovery, configuration, installation, and updates;
- authenticated plugin API routing and scoped federation admission;
- generic sandboxed plugin-view hosting, rail discovery, theme, and plugin kit;
- named-delegate and roster interfaces that any plugin can use.

Installing, updating, or removing Agent Room must not require Agent Room-specific core code or a private protoAgent fork. When the plugin is disabled or absent, the generic Rooms rail entry disappears and core continues to operate normally.

## Why the authorities stay separate

A fleet broadcast is fan-out, not a canonical group transcript. Each recipient may run independently and reply in its own context. Agent Room instead assigns one durable message order and ties deliveries and replies to that record.

Fleet process state must not become transcript authority, and Room messages must not become process-lifecycle state. Shared roster data can inform presentation or admission without copying either authority's database.

## Identity and trust

Fleet membership alone does not authorize a Room identity.

1. The authenticated host or peer identifies the caller.
2. Agent Room binds that caller to configured membership and policy.
3. Remote agent authorship is derived only from an existing canonical mention and the configured peer-agent allowlist.

Caller-written identity fields never establish authorship. Public profiles remain descriptive and do not imply reachability, dispatch authority, credentials, mutation rights, or cross-host access.

## Local-first and optional federation

One protoAgent instance is the complete default path. Local Room storage, plugin UI, membership, and named-delegate mentions work without a second device, tunnel, TLS configuration, remote credential, or offline queue.

Optional client mode uses one bounded authenticated HTTPS contract for `post`, `sync`, `ack`, and `members`. The client stores pending posts, cursors, and local delivery claims only. It never assigns canonical sequence numbers or mirrors the owner transcript.

## Migration rule

Legacy fleet broadcasts and direct chats are not silently imported as Room history because they were never one canonical conversation. Migration installs the self-contained plugin, configures Room membership and dispatch targets, verifies the generic plugin host, and removes any earlier Agent Room-specific core implementation.