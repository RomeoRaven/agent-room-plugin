# Fleet Room and the Agent Room backend

Fleet Room and Agent Room look similar because the Agent Room work began inside the native Fleet Room surface. The lasting distinction is not two competing products. It is two internal authorities behind one chat product.

Fleet authority answers:

> Which agents exist, which are running, and how do I operate them?

Agent Room authority answers:

> What did the group say, who was addressed, and what happened next?

Fleet Room should present both answers in one place.

## Short version

Fleet Room is the operator-facing chat console for a fleet. Agent Room is its durable conversation backend.

Fleet authority owns agent processes, workspaces, presence, individual consoles, and lifecycle. Agent Room authority owns rooms, membership, ordered messages, exact mentions, threads, delivery state, attributed replies, search, and offline reconciliation.

One surface does not require one database or one authority. Fleet process state must not become transcript authority, and Room messages must not become process lifecycle state.

## Why Fleet Room was not enough

The original Fleet Room was built around a live roster:

- show the host and fleet members;
- report online, stopped, remote, or unreachable state;
- start and stop local members;
- open a member's full console;
- enter a direct chat with one member;
- broadcast a message to every other online member;
- show live fleet activity.

That works well for operations. It does not create one shared conversation.

A Fleet broadcast is a fan-out. Each recipient runs its own turn on its own instance. Replies do not return to one canonical transcript. A direct message opens one member's chat, which belongs to that member. If a message reaches three agents, there is no single durable record that ties the original message, all three deliveries, and all three replies together.

For a real organization room, those details are the product:

- one canonical message order;
- one durable room and thread identity;
- exact recipient resolution;
- visible delivery state;
- one attributed reply per accepted mention;
- restart and offline recovery without duplicate work;
- plain text that wakes nobody;
- bounded agent-to-agent handoffs;
- history that remains searchable after the current working context is reset.

Putting this durability in the Agent Room backend lets Fleet Room become the shared chat console without turning the Fleet registry into a transcript store.

## Capability comparison

| Area | Fleet Room | Agent Room |
|---|---|---|
| Primary job | Operate and navigate a fleet of agents | Maintain a shared durable conversation |
| Authoritative state | Fleet registry, workspace state, process health | Room database, message order, membership, mentions, delivery state |
| Agent lifecycle | Start, stop, and open members; Fleet settings create, rename, and remove them | Not owned |
| Presence | Live running and reachability state | May display member state, but presence is not transcript authority |
| Direct communication | Opens a member's individual chat | Posts to a shared room or thread |
| Broadcast | Fire-and-forget fan-out to online members | No implicit broadcast; `@all` is rejected |
| Plain text | Broadcasts when no target is selected in the legacy composer | Creates a message and wakes no agent |
| Addressing | UI-selected or typed member target | Exact configured mention token |
| Multiple recipients | Independent sends | Ordered durable mentions attached to one canonical message |
| Replies | Stay with each member's turn or chat | Return to the same canonical room and thread |
| Offline behavior | Target must generally be reachable for the send | Client posts and acknowledgements can reconcile after the owner returns |
| Restart behavior | Restarts agent processes and resumes their own checkpoints | Preserves message IDs, delivery claims, cursors, and no-replay boundaries |
| Search and history | Per-agent chat and fleet activity surfaces | Room history, earlier context, archived rooms, and full-text search |
| Room lifecycle | Not owned | Create, rename, archive, restore, and non-destructive Start fresh |
| Execution authority | Fleet and delegated agents may perform their configured work | Room content is not execution approval |

## How the surface converges

Agent Room did not need another chat application. Fleet Room already existed to show agents and let the operator talk to them. Its broadcast bar was a fan-out because no durable shared transcript sat underneath it.

The current console is already close to the intended shape:

1. The Fleet Room palette entry renders the canonical Agent Room when the backend is available.
2. Loading, error, and empty-backend states fail closed so text is never broadcast by mistake.
3. The legacy broadcast view remains only as a compatibility fallback when the Agent Room API is absent.
4. The first-class Rooms rail exposes the same backend in the main console, but whether that extra navigation entry remains is a separate UX decision.

The long-term product direction is Fleet Room backed by Agent Room, not two chat products beside each other. The shared UI can continue evolving while the process and transcript authorities remain separate internally.

## What remains separate

Some overlap is visual, not architectural.

### Fleet authority continues to own

- workspace creation and isolation;
- archetypes and bundles;
- process supervision;
- local and remote fleet registration;
- online and reachability probes;
- opening individual agent consoles;
- per-agent chat and background work;
- fleet settings and lifecycle controls.

### Agent Room backend owns

- canonical rooms and message sequences;
- room membership and posting policy;
- exact mention parsing;
- durable mention and reply linkage;
- thread identity;
- cycle, hop, and rate limits;
- unread and acknowledgement cursors;
- archive, restore, Start fresh, pagination, and search;
- client pending-post and delivery reconciliation;
- canonical-mention-derived agent reply attribution.

Neither system should copy the other's authoritative database.

## Why Agent Room is a plugin

The durable Room backend is optional collaboration behavior, not a requirement for every protoAgent installation.

Keeping it in an external plugin provides a cleaner product boundary:

- a single protoAgent instance still works without federation or a second device;
- the backend can evolve without creating a second frontend;
- installation-specific members, routes, delegates, and credentials stay in local configuration;
- shared behavior remains generic enough for other protoAgent users;
- the protoAgent core only needs reusable host seams such as federation-scoped plugin routes, named delegates, and ACP sessions.

## Identity and trust

Fleet membership is not enough to authorize an Agent Room identity.

Agent Room uses two separate trust decisions:

1. The authenticated peer identifies the sending host.
2. The canonical Room owner allowlists which agent principals that peer may attest.

An attested agent reply is accepted only when it completes that same agent's existing canonical mention. Caller-written identity fields in a message payload remain forbidden.

On the client host, the configured principal is resolved against the live local roster before and after the agent turn. If the roster record changes, attribution is refused. The local client stores delivery claims and cursors, not a copy of the canonical transcript.

## Recommendation

Use one Fleet Room chat product backed by two explicit authorities.

- Fleet authority creates, starts, stops, discovers, and opens agents.
- Agent Room authority stores the shared transcript, exact mentions, threads, delivery state, and attributed replies.
- Fleet Room presents both without copying either authority's database.
- Shared roster cards, presence indicators, composer pieces, and responsive styles belong in the unified surface.
- The legacy fire-and-forget broadcast view remains only as a compatibility fallback while the durable backend is unavailable.
- The separate Rooms rail is an independent navigation decision. It may remain useful, but it should not define a second chat product.

## Migration implications

There is no Fleet Room transcript to migrate into Agent Room. Legacy broadcasts were independent sends, and direct messages belong to individual agent chats.

The product migration is behavioral:

1. Install and configure the Agent Room backend.
2. Let Fleet Room render the canonical shared transcript when that backend is available.
3. Use exact mentions for agent wake-up and keep lifecycle controls bound to Fleet authority.
4. Preserve the legacy broadcast view only during compatibility transition.
5. Decide the long-term Rooms rail exposure separately from the backend and transport architecture.

This avoids pretending that unrelated per-agent histories were once a single conversation while preserving Fleet Room as the place people already expect to talk to agents.

## Federation transport direction

Version 0.8 uses the `federation_paths` seam released in protoAgent v0.146.0. The plugin lowers only `/api/plugins/agent-room/v1/` from operator trust to federation trust; the endpoint remains authenticated and every other operator API stays unavailable to the federation credential.

Client mode uses one bounded authenticated HTTPS POST. The host supplies the verified trust tier, the plugin binds the configured peer principal, and remote agent attribution is derived from canonical pending mention state plus the peer-agent allowlist. No caller sends its own principal.

The Agent Room A2A handler, JSON-RPC wrapper, task polling, and client attestation fields are removed from active v0.8 source. Broader protoAgent A2A remains available for agentic collaboration outside deterministic Room synchronization.

## Current status

The v0.8 candidate includes durable subject rooms, exact local and roster-backed remote mention delivery, bounded agent chains, lifecycle and search, federation client mode, host-enforced read-only delegate invocation, persistent ACP session resume, and restart-safe reply behavior.

Source migration, host-free tests, and isolated exact-host v0.146 loader/auth qualification are complete, but installed development deployment and final acceptance have not run. Prior bridge parity and mention results remain regression evidence only. The complete Room acceptance must restart from its first criterion after exact migrated host/plugin deployment.
