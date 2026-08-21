from __future__ import annotations

import pytest

from dispatch import MentionWorker
from operations import RoomOperations
from store import RoomStore


OWNER = {
    "principal": "dennis",
    "kind": "human",
    "display_name": "Dennis",
    "role": "owner",
    "mention_token": "@Dennis",
    "host": "operator",
    "can_post": True,
    "can_mention": True,
}
HERMES = {
    "principal": "hermes",
    "kind": "agent",
    "display_name": "Hermes",
    "role": "member",
    "mention_token": "@Hermes",
    "host": "s1",
    "can_post": True,
    "can_mention": False,
}
HEADROOM = {
    "principal": "headroom",
    "kind": "agent",
    "display_name": "Headroom",
    "role": "member",
    "mention_token": "@Headroom",
    "host": "s1",
    "can_post": True,
    "can_mention": False,
}


def test_configured_mention_is_durable_and_retry_idempotent(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(
        store,
        dispatch_targets={"hermes": {"delegate": "hermes_s1"}},
    )
    payload = {
        "room_id": "ao",
        "client_message_id": "human-mention-1",
        "body": "Please reply @Hermes",
    }

    first = operations.execute("room.post", payload, principal="dennis")
    retry = operations.execute("room.post", payload, principal="dennis")

    assert first["result"]["created"] is True
    assert retry["result"]["created"] is False
    assert first["result"]["mentions"] == retry["result"]["mentions"]
    assert len(first["result"]["mentions"]) == 1
    mention = first["result"]["mentions"][0]
    assert mention["target_principal"] == "hermes"
    assert mention["token"] == "@Hermes"
    assert mention["status"] == "pending"
    assert mention["source_message_id"] == first["result"]["message"]["id"]
    assert "delegate_name" not in mention

    plain = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "plain-1", "body": "ordinary room text"},
        principal="dennis",
    )
    assert plain["result"]["mentions"] == []
    pending = store.pending_mentions(limit=10)
    assert len(pending) == 1
    assert pending[0]["id"] == mention["id"]
    assert pending[0]["delegate_name"] == "hermes_s1"

    synced = operations.execute(
        "room.sync",
        {"room_id": "ao", "after": 0, "limit": 10},
        principal="dennis",
    )["result"]
    assert synced["mentions"] == [mention]
    assert "delegate_name" not in synced["mentions"][0]


@pytest.mark.asyncio
async def test_worker_invokes_once_and_posts_one_same_thread_reply(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(
        store,
        dispatch_targets={"hermes": {"delegate": "hermes_s1"}},
    )
    source = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "wake-1", "body": "Status please @Hermes"},
        principal="dennis",
    )["result"]
    calls = []

    async def invoke(delegate_name: str, prompt: str, conversation_key: str) -> str:
        calls.append((delegate_name, prompt, conversation_key))
        return "Hermes is present."

    worker = MentionWorker(store, invoke_delegate=invoke)
    assert await worker.run_once() is True
    assert await worker.run_once() is False

    assert len(calls) == 1
    assert calls[0][0] == "hermes_s1"
    assert calls[0][2] == f"ao:{source['message']['thread_id']}"
    assert "Status please @Hermes" in calls[0][1]

    messages = store.sync(room_id="ao", after=0, limit=10)["messages"]
    assert len(messages) == 2
    reply = messages[1]
    assert reply["author_principal"] == "hermes"
    assert reply["author_kind"] == "agent"
    assert reply["body"] == "Hermes is present."
    assert reply["thread_id"] == source["message"]["thread_id"]
    assert reply["reply_to_message_id"] == source["message"]["id"]

    mention = store.mention(source["mentions"][0]["id"])
    assert mention["status"] == "completed"
    assert mention["reply_message_id"] == reply["id"]


@pytest.mark.asyncio
async def test_two_configured_targets_wake_independently_with_distinct_routes_and_attribution(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES, HEADROOM])
    operations = RoomOperations(
        store,
        dispatch_targets={
            "hermes": {"delegate": "hermes_s1"},
            "headroom": {"delegate": "headroom_s1"},
        },
    )
    hermes_source = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "wake-hermes", "body": "Status @Hermes"},
        principal="dennis",
    )["result"]
    headroom_source = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "wake-headroom", "body": "Status @Headroom"},
        principal="dennis",
    )["result"]
    calls = []

    async def invoke(delegate_name: str, prompt: str, conversation_key: str) -> str:
        calls.append((delegate_name, prompt, conversation_key))
        return f"reply from {delegate_name}"

    worker = MentionWorker(store, invoke_delegate=invoke)
    assert await worker.run_once() is True
    assert await worker.run_once() is True
    assert await worker.run_once() is False

    assert [(call[0], call[2]) for call in calls] == [
        ("hermes_s1", f"ao:{hermes_source['message']['thread_id']}"),
        ("headroom_s1", f"ao:{headroom_source['message']['thread_id']}"),
    ]
    messages = store.sync(room_id="ao", after=0, limit=10)["messages"]
    replies = [message for message in messages if message["author_kind"] == "agent"]
    assert [(reply["author_principal"], reply["body"]) for reply in replies] == [
        ("hermes", "reply from hermes_s1"),
        ("headroom", "reply from headroom_s1"),
    ]
    assert replies[0]["thread_id"] == hermes_source["message"]["thread_id"]
    assert replies[0]["reply_to_message_id"] == hermes_source["message"]["id"]
    assert replies[1]["thread_id"] == headroom_source["message"]["thread_id"]
    assert replies[1]["reply_to_message_id"] == headroom_source["message"]["id"]

    mentions = store.mentions_for_messages([hermes_source["message"]["id"], headroom_source["message"]["id"]])
    assert [(mention["target_principal"], mention["delegate_name"], mention["status"]) for mention in mentions] == [
        ("hermes", "hermes_s1", "completed"),
        ("headroom", "headroom_s1", "completed"),
    ]


@pytest.mark.asyncio
async def test_delegate_failure_is_sanitized_in_public_mention_state(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(
        store,
        dispatch_targets={"hermes": {"delegate": "hermes_s1"}},
    )
    posted = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "failure-1", "body": "Check @Hermes"},
        principal="dennis",
    )["result"]

    async def invoke(*_args) -> str:
        raise RuntimeError("delegate hermes_s1 failed at /private/local")

    worker = MentionWorker(store, invoke_delegate=invoke)
    assert await worker.run_once() is True

    synced = operations.execute(
        "room.sync",
        {"room_id": "ao", "after": 0, "limit": 10},
        principal="dennis",
    )["result"]
    mention = next(item for item in synced["mentions"] if item["id"] == posted["mentions"][0]["id"])
    assert mention["status"] == "failed"
    assert mention["error"] == "mention dispatch failed"
    assert "hermes_s1" not in mention["error"]
    assert "/private/local" not in mention["error"]


@pytest.mark.asyncio
async def test_reply_post_failure_marks_failed_and_worker_can_continue(tmp_path):
    blocked_hermes = {**HERMES, "can_post": False}
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[blocked_hermes])
    operations = RoomOperations(
        store,
        dispatch_targets={"hermes": {"delegate": "hermes_s1"}},
    )
    posted = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "post-failure-1", "body": "Check @Hermes"},
        principal="dennis",
    )["result"]
    calls = 0

    async def invoke(*_args) -> str:
        nonlocal calls
        calls += 1
        return "reply"

    worker = MentionWorker(store, invoke_delegate=invoke)
    assert await worker.run_once() is True
    assert await worker.run_once() is False
    assert calls == 1

    mention = store.mention(posted["mentions"][0]["id"])
    assert mention["status"] == "failed"
    assert mention["error"] == "mention reply posting failed"
    assert mention["reply_body"] == "reply"
    assert store.sync(room_id="ao", after=0, limit=10)["messages"] == [posted["message"]]


@pytest.mark.asyncio
async def test_restart_marks_interrupted_invocation_ambiguous_without_replay(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(
        store,
        dispatch_targets={"hermes": {"delegate": "hermes_s1"}},
    )
    posted = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "ambiguous-1", "body": "Check @Hermes"},
        principal="dennis",
    )["result"]
    claimed = store.claim_mention_work()
    assert claimed["status"] == "invoking"

    assert store.mark_interrupted_mentions_ambiguous() == 1
    mention = store.mention(posted["mentions"][0]["id"])
    assert mention["status"] == "ambiguous"
    assert "restart" in mention["error"]

    calls = 0

    async def invoke(*_args) -> str:
        nonlocal calls
        calls += 1
        return "must not run"

    worker = MentionWorker(store, invoke_delegate=invoke)
    assert await worker.run_once() is False
    assert calls == 0


def test_all_broadcast_is_rejected_at_room_admission(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(
        store,
        dispatch_targets={"hermes": {"delegate": "hermes_s1"}},
    )

    with pytest.raises(ValueError, match="@all"):
        operations.execute(
            "room.post",
            {"room_id": "ao", "client_message_id": "all-1", "body": "Wake @all"},
            principal="dennis",
        )

    assert store.sync(room_id="ao", after=0, limit=10)["messages"] == []
