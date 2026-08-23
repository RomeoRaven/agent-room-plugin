from __future__ import annotations

import pytest

from client import ClientState
from client_dispatch import ClientMentionWorker


AGENT = {
    "name": "pla",
    "display_name": "protoLabs Agent",
    "code": "PLA",
    "type": "scoped-agent",
    "status": "active",
    "owner_surface": "fixtures/owners/pla",
    "start_here": "fixtures/owners/pla/START_HERE.md",
    "source_of_truth": "agents/scoped/pla.md",
    "record_path": "fixtures/agents/pla.md",
    "record_sha256": "a" * 64,
    "startup_context": "PRELOADED PLA OWNER CONTEXT",
    "startup_sources": [
        {"path": "fixtures/guard.md", "sha256": "b" * 64},
        {"path": "fixtures/AGENTS.md", "sha256": "c" * 64},
        {"path": "fixtures/owners/pla/START_HERE.md", "sha256": "d" * 64},
    ],
    "startup_context_sha256": "e" * 64,
}


class FakeResolver:
    def __init__(self, results=None):
        self.results = list(results or [AGENT, AGENT])
        self.calls = []

    def resolve(self, code):
        self.calls.append(code)
        return self.results.pop(0)


class FakePeer:
    def __init__(self):
        self.calls = []
        self.messages = [
            {
                "id": "earlier",
                "room_id": "ao",
                "sequence": 20,
                "thread_id": "thread-1",
                "author_principal": "pc1",
                "author_kind": "host",
                "body": "Earlier context",
            },
            {
                "id": "source",
                "room_id": "ao",
                "sequence": 21,
                "thread_id": "thread-1",
                "author_principal": "pc1",
                "author_kind": "host",
                "body": "@PLA return STEP7B_MARKER",
            },
        ]

    def execute(self, operation, payload):
        self.calls.append((operation, payload))
        if operation == "room.sync":
            return {"messages": self.messages, "mentions": [], "next_sequence": 21, "has_more": False}
        assert operation == "room.post"
        return {
            "message": {
                "id": "reply-1",
                "room_id": "ao",
                "sequence": 22,
                "client_message_id": payload["client_message_id"],
                "body": payload["body"],
                "thread_id": payload["thread_id"],
                "reply_to_message_id": payload["reply_to_message_id"],
                "author_principal": "pla",
                "author_kind": "agent",
            },
            "completed_mention": {"id": payload["completes_mention_id"], "status": "completed"},
        }


def queue(state):
    state.import_local_mentions(
        [
            {
                "id": "mention-1",
                "room_id": "ao",
                "source_message_id": "source",
                "target_principal": "pla",
                "status": "pending",
            }
        ],
        [{"id": "source", "room_id": "ao", "sequence": 21, "thread_id": "thread-1", "author_kind": "host"}],
        {"pla"},
    )


@pytest.mark.asyncio
async def test_worker_resolves_live_wakes_exact_acp_session_once_and_posts_attributed_same_thread_reply(tmp_path):
    state = ClientState(tmp_path / "client.db")
    queue(state)
    peer = FakePeer()
    resolver = FakeResolver()
    calls = []

    async def invoke(delegate, prompt, conversation_key, *, permissions):
        calls.append((delegate, prompt, conversation_key, permissions))
        return "STEP7B_MARKER"

    worker = ClientMentionWorker(
        state,
        peer=peer,
        resolver=resolver,
        invoke_delegate=invoke,
        targets={"pla": {"agent_code": "PLA", "delegate": "pla-room"}},
    )

    assert await worker.run_once() is True
    assert await worker.run_once() is False
    assert resolver.calls == ["PLA", "PLA"]
    assert len(calls) == 1
    delegate, prompt, conversation_key, permissions = calls[0]
    assert delegate == "pla-room" and conversation_key == "ao:thread-1:pla"
    assert permissions == "readonly"
    assert AGENT["start_here"] in prompt and "Earlier context" in prompt and "@PLA return STEP7B_MARKER" in prompt
    assert "PRELOADED PLA OWNER CONTEXT" in prompt
    assert "Do not invoke tools" in prompt
    post = peer.calls[-1]
    assert post[0] == "room.post" and len(post) == 2
    assert post[1]["client_message_id"] == "mention-reply:mention-1"
    assert post[1]["thread_id"] == "thread-1" and post[1]["reply_to_message_id"] == "source"
    assert post[1]["completes_mention_id"] == "mention-1"


@pytest.mark.asyncio
async def test_worker_refuses_attribution_when_roster_record_changes_during_dispatch(tmp_path):
    state = ClientState(tmp_path / "client.db")
    queue(state)
    peer = FakePeer()
    changed = {**AGENT, "record_sha256": "b" * 64}
    resolver = FakeResolver([AGENT, changed])

    async def invoke(_delegate, _prompt, _conversation_key, *, permissions):
        return "must not be attributed"

    worker = ClientMentionWorker(
        state,
        peer=peer,
        resolver=resolver,
        invoke_delegate=invoke,
        targets={"pla": {"agent_code": "PLA", "delegate": "pla-room"}},
    )

    assert await worker.run_once() is True
    assert [call for call in peer.calls if call[0] == "room.post"] == []
    assert state.claim_local_mention() is None
