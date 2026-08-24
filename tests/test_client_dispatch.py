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

    def resolve(self, agent: str) -> dict[str, object]:
        self.calls.append(agent)
        return self.results.pop(0)


class FakePeer:
    def __init__(self):
        self.calls = []
        self.members = [
            {"principal": "pla", "kind": "agent", "mention_token": "@PLA", "mentionable": True},
            {"principal": "hermes", "kind": "agent", "mention_token": "@Hermes", "mentionable": True},
        ]
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
        if operation == "room.members":
            return {"members": self.members}
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


def test_delegate_prompt_allows_one_context_bound_room_handoff_without_relaxing_safety():
    prompt = ClientMentionWorker._prompt(AGENT, FakePeer().messages)

    assert "may repeat that exact token once" in prompt
    assert "Do not invent mention tokens" in prompt
    assert "Never emit @all" in prompt
    assert "Do not invoke tools" in prompt
    assert "Do not mutate files" in prompt
    assert "Do not emit @mention tokens" not in prompt


def test_reply_validation_allows_exact_dotted_owner_token():
    body = "Done @Team.Agent"
    context = [{"body": "@PLA hand off to @Team.Agent"}]
    members = [{"mention_token": "@Team.Agent", "mentionable": True}]

    assert ClientMentionWorker._validated_reply_body(body, context, members) == body


def test_reply_validation_allows_operator_directive_without_literal_token():
    body = "Done @Hermes"
    context = [
        {
            "id": "source",
            "author_kind": "host",
            "body": "@PLA [handoff:hermes] complete the bounded handoff",
        }
    ]
    members = [
        {
            "principal": "hermes",
            "kind": "agent",
            "mention_token": "@Hermes",
            "mentionable": True,
        }
    ]

    assert ClientMentionWorker._validated_reply_body(body, context, members, source_message_id="source") == body


@pytest.mark.parametrize(
    "author_kind,source_body,members",
    [
        (
            "agent",
            "@PLA [handoff:hermes] complete the bounded handoff",
            [{"principal": "hermes", "kind": "agent", "mention_token": "@Hermes", "mentionable": True}],
        ),
        (
            "host",
            "@PLA [handoff:hermes] [handoff:headroom] complete the bounded handoff",
            [{"principal": "hermes", "kind": "agent", "mention_token": "@Hermes", "mentionable": True}],
        ),
        (
            "host",
            "@PLA [handoff:unknown] complete the bounded handoff",
            [{"principal": "hermes", "kind": "agent", "mention_token": "@Hermes", "mentionable": True}],
        ),
        (
            "host",
            "@PLA [handoff:hermes] complete the bounded handoff",
            [{"principal": "hermes", "kind": "agent", "mention_token": "@Hermes", "mentionable": False}],
        ),
    ],
)
def test_reply_validation_blocks_unauthorized_handoff_directives(author_kind, source_body, members):
    context = [{"id": "source", "author_kind": author_kind, "body": source_body}]

    assert (
        ClientMentionWorker._validated_reply_body("Done @Hermes", context, members, source_message_id="source")
        == "Blocked: the authoritative local agent returned an unauthorized Room handoff."
    )


def test_reply_validation_blocks_ambiguous_owner_principal():
    context = [
        {
            "id": "source",
            "author_kind": "host",
            "body": "@PLA [handoff:hermes] complete the bounded handoff",
        }
    ]
    members = [
        {
            "principal": "Hermes",
            "kind": "agent",
            "mention_token": "@HermesOne",
            "mentionable": True,
        },
        {
            "principal": "hermes",
            "kind": "agent",
            "mention_token": "@HermesTwo",
            "mentionable": True,
        },
    ]

    assert (
        ClientMentionWorker._validated_reply_body("Done @HermesTwo", context, members, source_message_id="source")
        == "Blocked: the authoritative local agent returned an unauthorized Room handoff."
    )


def test_reply_validation_blocks_exact_owner_token_absent_from_context():
    body = "Unexpected @"
    context = [{"body": "@PLA do not hand off"}]
    members = [{"mention_token": "@", "mentionable": True}]

    assert ClientMentionWorker._validated_reply_body(body, context, members) == (
        "Blocked: the authoritative local agent returned an unauthorized Room handoff."
    )


@pytest.mark.parametrize(
    "source_body,reply,expected",
    [
        ("@PLA hand off to @Hermes", "Done @Hermes", "Done @Hermes"),
        (
            "@PLA do not hand off",
            "Invented @Hermes",
            "Blocked: the authoritative local agent returned an unauthorized Room handoff.",
        ),
        (
            "@PLA hand off to @Hermes",
            "Repeated @Hermes @Hermes",
            "Blocked: the authoritative local agent returned an unauthorized Room handoff.",
        ),
        (
            "@PLA status",
            "Broadcast @all",
            "Blocked: the authoritative local agent returned an unauthorized Room handoff.",
        ),
        (
            "@PLA hand off to @Observer",
            "Unknown @Observer",
            "Blocked: the authoritative local agent returned an unauthorized Room handoff.",
        ),
    ],
)
@pytest.mark.asyncio
async def test_worker_posts_only_one_known_context_bound_handoff_token(tmp_path, source_body, reply, expected):
    state = ClientState(tmp_path / "client.db")
    queue(state)
    peer = FakePeer()
    peer.messages[-1]["body"] = source_body

    async def invoke(_delegate, _prompt, _conversation_key, *, permissions):
        assert permissions == "readonly"
        return reply

    worker = ClientMentionWorker(
        state,
        peer=peer,
        resolver=FakeResolver(),
        invoke_delegate=invoke,
        targets={"pla": {"agent_code": "PLA", "delegate": "pla-room"}},
    )

    assert await worker.run_once() is True
    post = next(call for call in peer.calls if call[0] == "room.post")
    assert post[1]["body"] == expected


@pytest.mark.asyncio
async def test_worker_maps_operator_handoff_directive_to_exact_owner_token(tmp_path):
    state = ClientState(tmp_path / "client.db")
    queue(state)
    peer = FakePeer()
    peer.messages[-1]["body"] = "@PLA [handoff:hermes] complete the bounded handoff"

    async def invoke(_delegate, prompt, _conversation_key, *, permissions):
        assert permissions == "readonly"
        assert "[handoff:hermes] authorizes exactly @Hermes" in prompt
        return "Done @Hermes"

    worker = ClientMentionWorker(
        state,
        peer=peer,
        resolver=FakeResolver(),
        invoke_delegate=invoke,
        targets={"pla": {"agent_code": "PLA", "delegate": "pla-room"}},
    )

    assert await worker.run_once() is True
    post = next(call for call in peer.calls if call[0] == "room.post")
    assert post[1]["body"] == "Done @Hermes"


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
