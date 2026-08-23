from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

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
OBSERVER = {
    "principal": "observer",
    "kind": "agent",
    "display_name": "Observer",
    "role": "member",
    "mention_token": "@Observer",
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


def test_prefix_overlapping_tokens_wake_only_the_longest_exact_configured_token(tmp_path):
    short = {**HERMES, "principal": "short", "display_name": "Short", "mention_token": "@Agent"}
    long = {**HEADROOM, "principal": "long", "display_name": "Long", "mention_token": "@Agent-Pro"}
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[short, long])
    operations = RoomOperations(
        store,
        dispatch_targets={"short": {"delegate": "short_s1"}, "long": {"delegate": "long_s1"}},
    )

    posted = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "overlap-1", "body": "Only @Agent-Pro"},
        principal="dennis",
    )["result"]

    assert [(mention["target_principal"], mention["token"]) for mention in posted["mentions"]] == [
        ("long", "@Agent-Pro")
    ]

    both = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "overlap-2", "body": "@Agent-Pro then @Agent"},
        principal="dennis",
    )["result"]
    assert [(mention["target_principal"], mention["token"]) for mention in both["mentions"]] == [
        ("long", "@Agent-Pro"),
        ("short", "@Agent"),
    ]


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

    async def invoke(delegate_name: str, prompt: str, conversation_key: str, *, permissions: str) -> str:
        calls.append((delegate_name, prompt, conversation_key, permissions))
        return "Hermes is present."

    worker = MentionWorker(store, invoke_delegate=invoke)
    assert await worker.run_once() is True
    assert await worker.run_once() is False

    assert len(calls) == 1
    assert calls[0][0] == "hermes_s1"
    assert calls[0][2] == f"ao:1:{source['message']['thread_id']}"
    assert calls[0][3] == "readonly"
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
async def test_start_fresh_changes_delegate_conversation_generation_even_when_thread_id_is_reused(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(store, dispatch_targets={"hermes": {"delegate": "hermes_s1"}})
    keys = []

    async def invoke(delegate_name: str, prompt: str, conversation_key: str, **_kwargs) -> str:
        keys.append(conversation_key)
        return "done"

    operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "old", "body": "old @Hermes", "thread_id": "same"},
        principal="dennis",
    )
    worker = MentionWorker(store, invoke_delegate=invoke)
    assert await worker.run_once() is True
    operations.execute("room.reset", {"room_id": "ao"}, principal="dennis")
    operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "new", "body": "new @Hermes", "thread_id": "same"},
        principal="dennis",
    )
    assert await worker.run_once() is True

    assert keys == ["ao:1:same", "ao:3:same"]


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

    async def invoke(delegate_name: str, prompt: str, conversation_key: str, **_kwargs) -> str:
        calls.append((delegate_name, prompt, conversation_key))
        return f"reply from {delegate_name}"

    worker = MentionWorker(store, invoke_delegate=invoke)
    assert await worker.run_once() is True
    assert await worker.run_once() is True
    assert await worker.run_once() is False

    assert [(call[0], call[2]) for call in calls] == [
        ("hermes_s1", f"ao:1:{hermes_source['message']['thread_id']}"),
        ("headroom_s1", f"ao:1:{headroom_source['message']['thread_id']}"),
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
async def test_one_message_wakes_mentioned_targets_once_in_token_order_and_leaves_unmentioned_silent(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES, HEADROOM, OBSERVER])
    operations = RoomOperations(
        store,
        dispatch_targets={
            "hermes": {"delegate": "hermes_s1"},
            "headroom": {"delegate": "headroom_s1"},
            "observer": {"delegate": "observer_s1"},
        },
    )
    source = operations.execute(
        "room.post",
        {
            "room_id": "ao",
            "client_message_id": "multi-mention-1",
            "body": "Please answer in order @Hermes then @Headroom. @Hermes appears twice.",
        },
        principal="dennis",
    )["result"]
    calls = []

    async def invoke(delegate_name: str, _prompt: str, conversation_key: str, **_kwargs) -> str:
        calls.append((delegate_name, conversation_key))
        return f"reply from {delegate_name}"

    worker = MentionWorker(store, invoke_delegate=invoke)
    assert await worker.run_once() is True
    assert await worker.run_once() is True
    assert await worker.run_once() is False

    assert [mention["target_principal"] for mention in source["mentions"]] == ["hermes", "headroom"]
    assert [call[0] for call in calls] == ["hermes_s1", "headroom_s1"]
    assert all(call[1] == f"ao:1:{source['message']['thread_id']}" for call in calls)
    messages = store.sync(room_id="ao", after=0, limit=10)["messages"]
    replies = [message for message in messages if message["author_kind"] == "agent"]
    assert [reply["author_principal"] for reply in replies] == ["hermes", "headroom"]
    assert all(reply["thread_id"] == source["message"]["thread_id"] for reply in replies)
    assert all(reply["reply_to_message_id"] == source["message"]["id"] for reply in replies)
    assert "observer_s1" not in {call[0] for call in calls}
    assert not any(message["author_principal"] == "observer" for message in messages)


@pytest.mark.asyncio
async def test_agent_reply_mentions_other_agent_once_and_persists_origin_chain_while_cycle_is_blocked(tmp_path):
    hermes = {**HERMES, "can_mention": True}
    headroom = {**HEADROOM, "can_mention": True}
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[hermes, headroom])
    operations = RoomOperations(
        store,
        dispatch_targets={
            "hermes": {"delegate": "hermes_s1"},
            "headroom": {"delegate": "headroom_s1"},
        },
        mention_policy={"max_agent_hops": 1, "max_mentions_per_target": 10, "rate_window_seconds": 60},
    )
    source = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "agent-chain-1", "body": "Start @Hermes"},
        principal="dennis",
    )["result"]
    calls = []

    async def invoke(delegate_name: str, _prompt: str, _conversation_key: str, **_kwargs) -> str:
        calls.append(delegate_name)
        if delegate_name == "hermes_s1":
            return "Passing this once to @Headroom"
        if delegate_name == "headroom_s1":
            return "Handled. Attempting cycle to @Hermes"
        raise AssertionError(delegate_name)

    worker = MentionWorker(store, invoke_delegate=invoke, resolve_mentions=operations.resolve_mentions)
    assert await worker.run_once() is True
    assert await worker.run_once() is True
    assert await worker.run_once() is False

    assert calls == ["hermes_s1", "headroom_s1"]
    messages = store.sync(room_id="ao", after=0, limit=10)["messages"]
    assert [(message["author_principal"], message["body"]) for message in messages] == [
        ("dennis", "Start @Hermes"),
        ("hermes", "Passing this once to @Headroom"),
        ("headroom", "Handled. Attempting cycle to @Hermes"),
    ]
    mentions = store.mentions_for_messages([message["id"] for message in messages])
    assert [(mention["target_principal"], mention["status"], mention["hop_count"]) for mention in mentions] == [
        ("hermes", "completed", 0),
        ("headroom", "completed", 1),
        ("hermes", "blocked", 2),
    ]
    root, child, cycle = mentions
    assert root["origin_message_id"] == source["message"]["id"]
    assert root["parent_mention_id"] is None
    assert root["origin_chain"] == ["hermes"]
    assert child["origin_message_id"] == root["origin_message_id"]
    assert child["parent_mention_id"] == root["id"]
    assert child["origin_chain"] == ["hermes", "headroom"]
    assert cycle["parent_mention_id"] == child["id"]
    assert cycle["origin_chain"] == ["hermes", "headroom", "hermes"]
    assert cycle["error"] == "mention cycle blocked"


@pytest.mark.asyncio
async def test_agent_hop_limit_blocks_a_third_agent_without_invoking_it(tmp_path):
    hermes = {**HERMES, "can_mention": True}
    headroom = {**HEADROOM, "can_mention": True}
    observer = {**OBSERVER, "can_mention": True}
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[hermes, headroom, observer])
    operations = RoomOperations(
        store,
        dispatch_targets={
            "hermes": {"delegate": "hermes_s1"},
            "headroom": {"delegate": "headroom_s1"},
            "observer": {"delegate": "observer_s1"},
        },
        mention_policy={"max_agent_hops": 1, "max_mentions_per_target": 10, "rate_window_seconds": 60},
    )
    operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "hop-1", "body": "Start @Hermes"},
        principal="dennis",
    )
    calls = []

    async def invoke(delegate_name: str, _prompt: str, _conversation_key: str, **_kwargs) -> str:
        calls.append(delegate_name)
        return {
            "hermes_s1": "Continue to @Headroom",
            "headroom_s1": "Try to continue to @Observer",
        }[delegate_name]

    worker = MentionWorker(store, invoke_delegate=invoke, resolve_mentions=operations.resolve_mentions)
    assert await worker.run_once() is True
    assert await worker.run_once() is True
    assert await worker.run_once() is False

    assert calls == ["hermes_s1", "headroom_s1"]
    messages = store.sync(room_id="ao", after=0, limit=10)["messages"]
    mentions = store.mentions_for_messages([message["id"] for message in messages])
    blocked = next(mention for mention in mentions if mention["target_principal"] == "observer")
    assert blocked["status"] == "blocked"
    assert blocked["hop_count"] == 2
    assert blocked["error"] == "mention hop limit reached"


@pytest.mark.asyncio
async def test_per_room_target_rate_limit_blocks_excess_without_extra_delegate_turn(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(
        store,
        dispatch_targets={"hermes": {"delegate": "hermes_s1"}},
        mention_policy={"max_agent_hops": 1, "max_mentions_per_target": 1, "rate_window_seconds": 60},
    )
    first = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "rate-1", "body": "First @Hermes"},
        principal="dennis",
    )["result"]
    second = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "rate-2", "body": "Second @Hermes"},
        principal="dennis",
    )["result"]
    assert first["mentions"][0]["status"] == "pending"
    assert second["mentions"][0]["status"] == "blocked"
    assert second["mentions"][0]["error"] == "mention rate limit reached"
    calls = 0

    async def invoke(*_args, **_kwargs) -> str:
        nonlocal calls
        calls += 1
        return "one reply"

    worker = MentionWorker(store, invoke_delegate=invoke, resolve_mentions=operations.resolve_mentions)
    assert await worker.run_once() is True
    assert await worker.run_once() is False
    assert calls == 1


def test_per_room_target_rate_limit_is_atomic_across_concurrent_posts(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(
        store,
        dispatch_targets={"hermes": {"delegate": "hermes_s1"}},
        mention_policy={"max_agent_hops": 1, "max_mentions_per_target": 1, "rate_window_seconds": 60},
    )
    barrier = threading.Barrier(2)
    original_count = store.recent_mention_count

    def synchronized_count(**kwargs) -> int:
        value = original_count(**kwargs)
        barrier.wait(timeout=5)
        return value

    store.recent_mention_count = synchronized_count

    def post(index: int) -> str:
        result = operations.execute(
            "room.post",
            {"room_id": "ao", "client_message_id": f"concurrent-{index}", "body": "Wake @Hermes"},
            principal="dennis",
        )["result"]
        return result["mentions"][0]["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(post, [1, 2]))

    assert sorted(statuses) == ["blocked", "pending"]
    assert len(store.pending_mentions(limit=10)) == 1


@pytest.mark.asyncio
async def test_reply_ready_restart_posts_child_mention_without_reinvoking_parent_or_duplicating(tmp_path):
    path = tmp_path / "room.db"
    hermes = {**HERMES, "can_mention": True}
    headroom = {**HEADROOM, "can_mention": True}
    store = RoomStore(path, owner=OWNER, members=[hermes, headroom])
    operations = RoomOperations(
        store,
        dispatch_targets={"hermes": {"delegate": "hermes_s1"}, "headroom": {"delegate": "headroom_s1"}},
    )
    source = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "restart-chain-1", "body": "Start @Hermes"},
        principal="dennis",
    )["result"]
    parent = store.claim_mention_work()
    assert parent["status"] == "invoking"
    store.save_mention_reply(parent["id"], "Recovered reply to @Headroom")

    reopened = RoomStore(path, owner=OWNER, members=[hermes, headroom])
    reopened_operations = RoomOperations(
        reopened,
        dispatch_targets={"hermes": {"delegate": "hermes_s1"}, "headroom": {"delegate": "headroom_s1"}},
    )
    calls = []

    async def invoke(delegate_name: str, _prompt: str, _conversation_key: str, **_kwargs) -> str:
        calls.append(delegate_name)
        return "Headroom recovered"

    worker = MentionWorker(
        reopened,
        invoke_delegate=invoke,
        resolve_mentions=reopened_operations.resolve_mentions,
    )
    assert await worker.run_once() is True
    assert await worker.run_once() is True
    assert await worker.run_once() is False

    assert calls == ["headroom_s1"]
    messages = reopened.sync(room_id="ao", after=0, limit=10)["messages"]
    assert len(messages) == 3
    assert len([message for message in messages if message["author_principal"] == "hermes"]) == 1
    assert len([message for message in messages if message["author_principal"] == "headroom"]) == 1
    mentions = reopened.mentions_for_messages([message["id"] for message in messages])
    assert [(mention["target_principal"], mention["status"]) for mention in mentions] == [
        ("hermes", "completed"),
        ("headroom", "completed"),
    ]
    assert mentions[1]["parent_mention_id"] == mentions[0]["id"]
    assert mentions[1]["origin_message_id"] == source["message"]["id"]


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

    async def invoke(*_args, **_kwargs) -> str:
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

    async def invoke(*_args, **_kwargs) -> str:
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

    async def invoke(*_args, **_kwargs) -> str:
        nonlocal calls
        calls += 1
        return "must not run"

    worker = MentionWorker(store, invoke_delegate=invoke)
    assert await worker.run_once() is False
    assert calls == 0


def test_operator_all_expands_once_to_every_wakeable_agent_in_roster_order(tmp_path):
    pc1 = {
        **OWNER,
        "principal": "pc1",
        "kind": "host",
        "display_name": "PC1",
        "mention_token": "@PC1",
        "host": "pc1",
    }
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HEADROOM, pc1, HERMES, OBSERVER])
    operations = RoomOperations(
        store,
        dispatch_targets={
            "hermes": {"delegate": "hermes_s1"},
            "headroom": {"delegate": "headroom_s1"},
        },
    )

    posted = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "all-1", "body": "Wake @all and @Hermes"},
        principal="pc1",
    )["result"]

    assert [(mention["target_principal"], mention["token"]) for mention in posted["mentions"]] == [
        ("headroom", "@all"),
        ("hermes", "@all"),
    ]


def test_agent_authored_all_is_rejected_before_room_admission(tmp_path):
    source = {**HERMES, "can_mention": True}
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[source, HEADROOM])
    operations = RoomOperations(
        store,
        dispatch_targets={
            "hermes": {"delegate": "hermes_s1"},
            "headroom": {"delegate": "headroom_s1"},
        },
    )

    with pytest.raises(PermissionError, match="@all"):
        operations.execute(
            "room.post",
            {"room_id": "ao", "client_message_id": "all-agent-1", "body": "Wake @all"},
            principal="hermes",
        )

    assert store.sync(room_id="ao", after=0, limit=10)["messages"] == []
