# Fleet Room and Agent Room

Fleet Room and Agent Room look similar because both show agents, accept messages, and live in the protoAgent console. They solve different problems.

Fleet Room answers:

> Which agents are available, and how do I operate or contact them?

Agent Room answers:

> What did the group say, who was addressed, and what happened next?

That distinction is the reason Agent Room exists.

## Short version

Fleet is an operational system for running and navigating multiple agents. Agent Room is a durable conversation system shared by people and agents.

Fleet owns agent processes, workspaces, presence, individual consoles, direct messages, and broadcast fan-out. Agent Room owns rooms, membership, ordered messages, exact mentions, threads, delivery state, attributed replies, search, and offline reconciliation.

Agent Room reuses the native Fleet Room visual foundation. It does not turn Fleet into a transcript store, and it does not replace Fleet's process-management responsibilities.

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

Adding those rules directly to Fleet would mix process lifecycle with conversation authority. Agent Room keeps the boundary explicit.

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

## What Agent Room reuses

Agent Room did not need another chat application.

The native protoAgent console already had a Fleet Room layout with a roster, activity area, composer, responsive styles, and command-palette entry. The Agent Room work reused that foundation and added a native Rooms rail surface.

The current console behavior has three important paths:

1. The Rooms rail requires an Agent Room backend. It does not fall back to broadcast behavior.
2. The existing Fleet Room palette entry renders Agent Room when a canonical room is available.
3. The legacy Fleet Room remains a compatibility fallback when the Agent Room API is absent.

This reuse keeps the UI native while allowing the backend contracts to stay separate.

## What remains separate

Some overlap is visual, not architectural.

### Fleet continues to own

- workspace creation and isolation;
- archetypes and bundles;
- process supervision;
- local and remote fleet registration;
- online and reachability probes;
- opening individual agent consoles;
- per-agent chat and background work;
- fleet settings and lifecycle controls.

### Agent Room owns

- canonical rooms and message sequences;
- room membership and posting policy;
- exact mention parsing;
- durable mention and reply linkage;
- thread identity;
- cycle, hop, and rate limits;
- unread and acknowledgement cursors;
- archive, restore, Start fresh, pagination, and search;
- client pending-post and delivery reconciliation;
- host-attested agent reply attribution.

Neither system should copy the other's authoritative database.

## Why Agent Room is a plugin

The durable Room backend is optional collaboration behavior, not a requirement for every protoAgent installation.

Keeping it in an external plugin provides a cleaner product boundary:

- a single protoAgent instance still works without federation or a second device;
- the backend can evolve without creating a second frontend;
- installation-specific members, routes, delegates, and credentials stay in local configuration;
- shared behavior remains generic enough for other protoAgent users;
- the protoAgent core only needs reusable host seams such as plugin routes, named delegates, ACP sessions, and A2A handlers.

## Identity and trust

Fleet membership is not enough to authorize an Agent Room identity.

Agent Room uses two separate trust decisions:

1. The authenticated peer identifies the sending host.
2. The canonical Room owner allowlists which agent principals that peer may attest.

An attested agent reply is accepted only when it completes that same agent's existing canonical mention. Caller-written identity fields in a message payload remain forbidden.

On the client host, the configured principal is resolved against the live local roster before and after the agent turn. If the roster record changes, attribution is refused. The local client stores delivery claims and cursors, not a copy of the canonical transcript.

## Recommendation

Keep Fleet and Agent Room as separate products that share selected UI components.

- Fleet should remain the place to create, start, stop, inspect, and open agents.
- Rooms should remain the place to hold shared conversations and exact mention-driven replies.
- Shared roster cards, presence indicators, composer pieces, and responsive styles can be extracted over time.
- Fleet process state should not become Room membership authority.
- Room message state should not become Fleet lifecycle state.

The legacy Fleet Room palette fallback can be retired after Agent Room installation and compatibility policy are stable. Until then, it is a useful transition path. The native Rooms rail should continue to fail closed when the canonical backend is unavailable so a message is never broadcast by mistake.

## Migration implications

There is no Fleet Room transcript to migrate into Agent Room. Legacy broadcasts were independent sends, and direct messages belong to individual agent chats.

The migration is therefore behavioral:

1. Install and configure Agent Room.
2. Expose the native Rooms surface.
3. Use exact mentions for agent wake-up.
4. Keep Fleet for agent operations and individual consoles.
5. Retire the legacy broadcast-oriented Room entry after adoption is proven.

This avoids pretending that unrelated per-agent histories were once a single conversation.

## Current status

The public plugin main branch includes durable subject rooms, exact local mention delivery, bounded agent chains, lifecycle and search, and deterministic client mode.

Roster-backed remote agent replies are the active development slice. The candidate has passed installed-host acceptance and is being prepared for public merge. It remains development work until the merged revision passes the same minimum reply and recovery check. See [the Agent Room plan](agent-room-plan.md) for the full status and remaining gates.
