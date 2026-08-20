from __future__ import annotations

import pytest

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


def _store(path, *, members=None):
    return RoomStore(path, owner=OWNER, members=members)


def test_fixed_room_post_is_ordered_idempotent_and_restart_persistent(tmp_path):
    path = tmp_path / "agent-room.db"
    store = _store(path)

    assert store.list_rooms() == [
        {"id": "ao", "name": "Agent Organization", "created_at": store.list_rooms()[0]["created_at"]}
    ]

    first = store.post(
        room_id="ao",
        principal="dennis",
        client_message_id="pc1-1",
        body="Hello room",
    )
    retry = store.post(
        room_id="ao",
        principal="dennis",
        client_message_id="pc1-1",
        body="Hello room",
    )

    assert first["created"] is True
    assert retry == {"created": False, "message": first["message"]}
    assert first["message"]["sequence"] == 1
    assert first["message"]["thread_id"] == first["message"]["id"]

    reopened = _store(path)
    synced = reopened.sync(room_id="ao", after=0, limit=50)
    assert synced["messages"] == [first["message"]]
    assert synced["next_sequence"] == 1
    assert synced["has_more"] is False


def test_reused_client_message_id_with_different_content_is_conflict(tmp_path):
    store = _store(tmp_path / "agent-room.db")
    store.post(room_id="ao", principal="dennis", client_message_id="pc1-1", body="Original")

    with pytest.raises(RoomConflict, match="client_message_id already exists with different content"):
        store.post(room_id="ao", principal="dennis", client_message_id="pc1-1", body="Changed")

    assert [message["body"] for message in store.sync(room_id="ao")["messages"]] == ["Original"]


def test_fixed_member_and_monotonic_ack_persist_across_restart(tmp_path):
    path = tmp_path / "agent-room.db"
    store = _store(path)
    first = store.post(room_id="ao", principal="dennis", client_message_id="pc1-1", body="One")["message"]
    second = store.post(room_id="ao", principal="dennis", client_message_id="pc1-2", body="Two")["message"]

    assert store.members(room_id="ao") == [
        {
            "principal": "dennis",
            "kind": "human",
            "display_name": "Dennis",
            "role": "owner",
            "mention_token": "@Dennis",
            "host": "operator",
            "can_post": True,
            "can_mention": True,
        }
    ]
    assert store.ack(room_id="ao", principal="dennis", sequence=first["sequence"])["last_sequence"] == 1
    assert store.ack(room_id="ao", principal="dennis", sequence=0)["last_sequence"] == 1

    reopened = _store(path)
    assert reopened.cursor(room_id="ao", principal="dennis") == 1
    assert reopened.ack(room_id="ao", principal="dennis", sequence=second["sequence"])["last_sequence"] == 2
    with pytest.raises(ValueError, match="cannot acknowledge beyond current room sequence"):
        reopened.ack(room_id="ao", principal="dennis", sequence=3)


def test_only_configured_posting_members_can_author_messages(tmp_path):
    store = _store(
        tmp_path / "agent-room.db",
        members=[
            {
                "principal": "pc1",
                "kind": "host",
                "display_name": "PC1",
                "role": "member",
                "mention_token": "@PC1",
                "host": "pc1",
                "can_post": True,
                "can_mention": False,
            },
            {
                "principal": "observer",
                "kind": "host",
                "display_name": "Observer",
                "role": "member",
                "mention_token": "@Observer",
                "host": "s1",
                "can_post": False,
                "can_mention": False,
            },
        ],
    )

    assert store.post(room_id="ao", principal="pc1", client_message_id="pc1-1", body="Hello")["created"] is True
    with pytest.raises(PermissionError, match="not a posting member"):
        store.post(room_id="ao", principal="intruder", client_message_id="x-1", body="No")
    with pytest.raises(PermissionError, match="not a posting member"):
        store.post(room_id="ao", principal="observer", client_message_id="o-1", body="No")
