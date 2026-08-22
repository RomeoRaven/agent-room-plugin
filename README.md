# Agent Room Backend

Durable shared-room backend for [protoAgent](https://github.com/protoLabsAI/protoAgent).

This plugin is intentionally backend-only. It reuses protoAgent's native Room/Fleet Room surface rather than shipping a second chat UI.

## Why this exists

Fleet runs and operates many agents. Agent Room supplies the durable conversation layer that Fleet Room lacked: one canonical transcript, exact mentions, attributed replies, search, and recovery. The permanent direction is one Fleet Room chat product backed by separate Fleet process authority and Agent Room transcript authority.

- [Fleet Room and the Agent Room backend](docs/fleet-room-vs-agent-room.md) explains the unified product surface and separate internal responsibilities.
- [Agent Room plan](docs/agent-room-plan.md) shows the architecture, completed work, federation-route migration, remaining acceptance, and definition of done.

## Current slice

- Multiple subject rooms with migration-safe Agent Organization default (`ao`)
- Owner-gated create, rename, archive, restore, and non-destructive Start fresh lifecycle
- Bounded recent/older transcript windows and bounded search-result context
- SQLite FTS5 search across current, earlier, active, and archived history
- SQLite-backed ordered messages
- Stable client-message retry deduplication and conflict detection
- Config-bound membership and author identity
- Persistent member cursors
- Gated model-free Room list/lifecycle/search/post/sync/ack/members API
- Room-visible `mentionable` state so the native composer suggests only configured local dispatch targets
- Deterministic A2A `agent-room-v1` wrapper for the same operations
- Exact configured member-token resolution with durable mention state
- Same-message delivery to multiple explicitly mentioned targets in token order
- Lifecycle-managed named-delegate dispatch keyed by Room/thread
- Reply text persisted before one idempotent attributed same-thread post
- Restart-safe no-replay handling for ambiguous in-flight delegate turns
- Persisted agent-origin chain, parent mention, hop count, cycle/hop/rate blocking
- Sanitized mention delivery state returned through `room.sync`
- Optional deterministic client mode for one fixed remote-owned AO room
- Directional TLS A2A proxy for post/sync/ack/members with no canonical-message cache
- SQLite-backed pending posts, acknowledgement/delivery cursors, and local mention-dispatch claims only
- Stable-id offline post reconciliation and explicit owner-offline/pending UI state
- Live host-roster admission for configured client-side agent targets
- Durable hash-keyed local mention claims with no replay after ambiguous ACP interruption
- Host-attested, owner-allowlisted agent attribution for one canonical same-thread reply
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

Each owner-side dispatch principal must also be a configured Room member. A target binds either one local named delegate or one remote peer, never both. A configured client-side target resolves its exact code from a fixed host-owned stdin/JSON resolver before and after one readonly named-delegate ACP turn. Multiple explicit tokens in one message create one ordered durable mention per target; repeated tokens for the same target still create one mention. Delegate identity plus the Room/thread conversation key keeps ACP sessions separate. Plain text wakes nobody and `@all` remains rejected.

An agent with `can_mention: true` may mention another configured agent from its Room reply. The child mention persists its parent, root message, principal chain, and hop count. Cycles, hop-limit excess, and per-room/per-target rate excess are stored as visible `blocked` mention states and never invoke a delegate. A possible process interruption during delegate invocation becomes visible `ambiguous` state and is not automatically replayed.

New rooms inherit the installation's configured owner/member roster. Archive is read-only but reversible. Start fresh advances the default visible-history boundary without deleting messages; earlier history remains paged, searchable, and restart-safe. Archive and Start fresh refuse rooms with pending agent delivery. Permanent message/room deletion is deliberately absent.

Not included yet: client-side lifecycle/search, attachments, reactions, per-room roster administration, permanent deletion, or general execution.

## Local-first product boundary

A single protoAgent instance is the complete default product. Installations can keep `peer_principal` empty and use the durable Room, native UI, local members, and local named-delegate mentions without any second device, tunnel, TLS setup, remote credential, or offline queue.

The ownership split is deliberate:

- This plugin owns canonical Room storage, lifecycle, indexed search, membership, ordering, cursors, exact mention resolution, local delegate dispatch, and the stable model-free Room operations.
- protoAgent's native console owns human interaction: room switching and lifecycle controls, search presentation, navigation, member selection, mention autocomplete, recipient guidance, transcript rendering, and accessible keyboard behavior.
- Optional `mode: client` calls the same deterministic Room operations for another device. It owns its directional peer URL/credential/CA reference plus `agent-room-client.db`, which stores pending posts, acknowledgement/delivery cursors, and local mention-dispatch claims only. It never stores canonical messages or assigns sequence numbers.
- A host-local roster adapter maps that host's authoritative agents to Room principals. The Room plugin does not mirror or replace another host's agent identities.

In owner mode, the A2A wrapper stays dormant unless `peer_principal` is explicitly configured. In client mode, the local API proxies only the established `post`, `sync`, `ack`, and `members` operations to the configured HTTPS owner and exposes a fixed AO room without lifecycle/search controls. Owner-mode lifecycle and search remain local-only.

## Development

```bash
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
pytest -q
```

## Platform validation

| Platform | Status | Evidence / follow-up |
|---|---|---|
| Linux | Tested | v0.7 candidate: owner/client/roster-dispatch plugin suite |
| Windows | Tested | v0.7 candidate: native Ruff/format and 87-test suite plus live roster resolution, ACP session resume, exact attributed reply, dedupe, agent-origin silence, interrupted-invocation no-replay, process cleanup, and native Room UI acceptance |
| macOS | Not tested | Intended; native validation has not been run for this release |

See `PROTO.md` for architecture, constraints, and the current acceptance boundary.
