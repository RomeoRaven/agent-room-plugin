from __future__ import annotations

import pytest

from operations import RoomOperations
from store import RoomConflict, RoomStore

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
    "host": "local",
    "can_post": True,
    "can_mention": True,
}


def test_create_room_copies_configured_members_and_lists_room_state(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(store, dispatch_targets={"hermes": {"delegate": "hermes_local"}})

    created = operations.execute("room.create", {"name": "Release planning"}, principal="dennis")["result"]["room"]
    listed = operations.execute("room.list", {"status": "all"}, principal="dennis")["result"]["rooms"]

    assert created["id"] != "ao"
    assert created["name"] == "Release planning"
    assert created["status"] == "active"
    assert created["active_from_sequence"] == 1
    assert created["latest_sequence"] == 0
    assert created["unread_count"] == 0
    assert created["current_message_count"] == 0
    assert {member["principal"] for member in store.members(room_id=created["id"])} == {"dennis", "hermes"}
    assert {room["id"] for room in listed} == {"ao", created["id"]}


def test_owner_can_rename_archive_and_restore_while_archived_room_is_read_only(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(store)
    room = operations.execute("room.create", {"name": "Release planning"}, principal="dennis")["result"]["room"]

    renamed = operations.execute(
        "room.rename",
        {"room_id": room["id"], "name": "Launch planning"},
        principal="dennis",
    )["result"]["room"]
    archived = operations.execute("room.archive", {"room_id": room["id"]}, principal="dennis")["result"]["room"]

    assert renamed["name"] == "Launch planning"
    assert archived["status"] == "archived"
    assert archived["archived_at"] is not None
    with pytest.raises(RoomConflict, match="archived room is read-only"):
        operations.execute(
            "room.post",
            {"room_id": room["id"], "client_message_id": "blocked", "body": "No"},
            principal="dennis",
        )

    restored = operations.execute("room.restore", {"room_id": room["id"]}, principal="dennis")["result"]["room"]
    posted = operations.execute(
        "room.post",
        {"room_id": room["id"], "client_message_id": "restored", "body": "Back"},
        principal="dennis",
    )
    assert restored["status"] == "active"
    assert restored["archived_at"] is None
    assert posted["result"]["created"] is True


def test_start_fresh_preserves_earlier_history_but_hides_it_from_current_sync(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(store)
    room = operations.execute("room.create", {"name": "Long subject"}, principal="dennis")["result"]["room"]
    for index in range(1, 4):
        operations.execute(
            "room.post",
            {"room_id": room["id"], "client_message_id": f"old-{index}", "body": f"old message {index}"},
            principal="dennis",
        )

    reset = operations.execute("room.reset", {"room_id": room["id"]}, principal="dennis")["result"]
    current = operations.execute("room.sync", {"room_id": room["id"], "limit": 20}, principal="dennis")["result"]

    assert reset["room"]["active_from_sequence"] == 4
    assert reset["room"]["message_count"] == 3
    assert reset["room"]["current_message_count"] == 0
    assert reset["room"]["history_available"] is True
    assert current["messages"] == []
    assert current["history_available"] is True

    operations.execute(
        "room.post",
        {"room_id": room["id"], "client_message_id": "new-1", "body": "current message"},
        principal="dennis",
    )
    current = operations.execute("room.sync", {"room_id": room["id"], "limit": 20}, principal="dennis")["result"]
    history = operations.execute(
        "room.sync",
        {"room_id": room["id"], "limit": 20, "history": True},
        principal="dennis",
    )["result"]
    assert [message["body"] for message in current["messages"]] == ["current message"]
    assert [message["body"] for message in history["messages"]] == [
        "old message 1",
        "old message 2",
        "old message 3",
        "current message",
    ]


def test_sync_loads_recent_messages_then_older_or_bounded_search_context(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(store)
    room = operations.execute("room.create", {"name": "Long room"}, principal="dennis")["result"]["room"]
    for index in range(1, 206):
        operations.execute(
            "room.post",
            {"room_id": room["id"], "client_message_id": f"m-{index}", "body": f"message {index}"},
            principal="dennis",
        )

    recent = operations.execute("room.sync", {"room_id": room["id"], "limit": 3}, principal="dennis")["result"]
    older = operations.execute(
        "room.sync",
        {"room_id": room["id"], "before": 203, "limit": 3},
        principal="dennis",
    )["result"]
    around = operations.execute(
        "room.sync",
        {"room_id": room["id"], "around": 100, "limit": 5, "history": True},
        principal="dennis",
    )["result"]

    assert [message["sequence"] for message in recent["messages"]] == [203, 204, 205]
    assert recent["has_older"] is True
    assert [message["sequence"] for message in older["messages"]] == [200, 201, 202]
    assert older["has_older"] is True
    assert [message["sequence"] for message in around["messages"]] == [98, 99, 100, 101, 102]
    assert around["window_sequence"] == 100


def test_search_scopes_current_earlier_active_and_archived_rooms(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(store)
    active = operations.execute("room.create", {"name": "Active subject"}, principal="dennis")["result"]["room"]
    archived = operations.execute("room.create", {"name": "Archived subject"}, principal="dennis")["result"]["room"]
    operations.execute(
        "room.post",
        {"room_id": active["id"], "client_message_id": "old-alpha", "body": "alpha rocket plan"},
        principal="dennis",
    )
    operations.execute("room.reset", {"room_id": active["id"]}, principal="dennis")
    operations.execute(
        "room.post",
        {"room_id": active["id"], "client_message_id": "current", "body": "current beta note"},
        principal="dennis",
    )
    operations.execute(
        "room.post",
        {"room_id": archived["id"], "client_message_id": "archived-alpha", "body": "alpha design archive"},
        principal="dennis",
    )
    operations.execute("room.archive", {"room_id": archived["id"]}, principal="dennis")

    current = operations.execute(
        "room.search",
        {"query": "alpha", "scope": "current", "room_id": active["id"]},
        principal="dennis",
    )["result"]["results"]
    earlier = operations.execute(
        "room.search",
        {"query": "alpha", "scope": "current", "room_id": active["id"], "history": True},
        principal="dennis",
    )["result"]["results"]
    all_active = operations.execute(
        "room.search",
        {"query": "alpha", "scope": "all", "history": True},
        principal="dennis",
    )["result"]["results"]
    archived_results = operations.execute(
        "room.search",
        {"query": "alpha", "scope": "archived", "history": True},
        principal="dennis",
    )["result"]["results"]

    assert current == []
    assert [(item["room_name"], item["sequence"], item["earlier"]) for item in earlier] == [("Active subject", 1, True)]
    assert {item["room_name"] for item in all_active} == {"Active subject"}
    assert {item["room_name"] for item in archived_results} == {"Archived subject"}
    assert "alpha" in archived_results[0]["snippet"].lower()


def test_room_list_unread_and_mention_badges_follow_member_cursor(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(store, dispatch_targets={"hermes": {"delegate": "hermes_local"}})
    room = operations.execute("room.create", {"name": "Unread room"}, principal="dennis")["result"]["room"]
    operations.execute(
        "room.post",
        {"room_id": room["id"], "client_message_id": "wake", "body": "@Hermes check this"},
        principal="dennis",
    )

    unread = operations.execute("room.list", {"status": "active"}, principal="hermes")["result"]["rooms"]
    target = next(item for item in unread if item["id"] == room["id"])
    assert target["unread_count"] == 1
    assert target["unread_mentions"] == 1

    operations.execute("room.ack", {"room_id": room["id"], "sequence": 1}, principal="hermes")
    read = operations.execute("room.list", {"status": "active"}, principal="hermes")["result"]["rooms"]
    target = next(item for item in read if item["id"] == room["id"])
    assert target["unread_count"] == 0
    assert target["unread_mentions"] == 0


def test_archive_and_start_fresh_refuse_pending_agent_delivery(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(store, dispatch_targets={"hermes": {"delegate": "hermes_local"}})
    room = operations.execute("room.create", {"name": "Pending room"}, principal="dennis")["result"]["room"]
    operations.execute(
        "room.post",
        {"room_id": room["id"], "client_message_id": "wake", "body": "@Hermes pending"},
        principal="dennis",
    )

    with pytest.raises(RoomConflict, match="pending agent delivery"):
        operations.execute("room.archive", {"room_id": room["id"]}, principal="dennis")
    with pytest.raises(RoomConflict, match="pending agent delivery"):
        operations.execute("room.reset", {"room_id": room["id"]}, principal="dennis")


def test_archived_room_allows_only_an_idempotent_retry_of_an_accepted_post(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(store)
    room = operations.execute("room.create", {"name": "Retry room"}, principal="dennis")["result"]["room"]
    payload = {"room_id": room["id"], "client_message_id": "stable", "body": "accepted before archive"}
    first = operations.execute("room.post", payload, principal="dennis")
    operations.execute("room.archive", {"room_id": room["id"]}, principal="dennis")

    retry = operations.execute("room.post", payload, principal="dennis")
    assert retry["result"]["created"] is False
    assert retry["result"]["message"]["id"] == first["result"]["message"]["id"]
    with pytest.raises(RoomConflict, match="archived room is read-only"):
        operations.execute(
            "room.post",
            {"room_id": room["id"], "client_message_id": "new", "body": "new post"},
            principal="dennis",
        )


def test_active_room_names_are_unique_and_only_owner_can_create(tmp_path):
    store = RoomStore(tmp_path / "room.db", owner=OWNER, members=[HERMES])
    operations = RoomOperations(store)
    first = operations.execute("room.create", {"name": "Topic"}, principal="dennis")["result"]["room"]
    with pytest.raises(RoomConflict, match="already uses that name"):
        operations.execute("room.create", {"name": "topic"}, principal="dennis")
    with pytest.raises(PermissionError, match="only a room owner"):
        operations.execute("room.create", {"name": "Unauthorized"}, principal="hermes")

    operations.execute("room.archive", {"room_id": first["id"]}, principal="dennis")
    replacement = operations.execute("room.create", {"name": "topic"}, principal="dennis")["result"]["room"]
    assert replacement["status"] == "active"
    with pytest.raises(RoomConflict, match="already uses that name"):
        operations.execute("room.restore", {"room_id": first["id"]}, principal="dennis")
