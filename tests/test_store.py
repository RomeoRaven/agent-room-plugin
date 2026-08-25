from __future__ import annotations

import sqlite3

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


def test_default_room_post_is_ordered_idempotent_and_restart_persistent(tmp_path):
    path = tmp_path / "agent-room.db"
    store = _store(path)

    rooms = store.list_rooms()
    assert len(rooms) == 1
    assert rooms[0]["id"] == "ao"
    assert rooms[0]["name"] == "Agent Organization"
    assert rooms[0]["status"] == "active"
    assert rooms[0]["latest_sequence"] == 0

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


def test_member_profile_persists_and_is_returned_with_exact_public_fields(tmp_path):
    path = tmp_path / "agent-room.db"
    profile = {
        "summary": "Coordinates implementation work.",
        "capabilities": ["Python", "SQLite"],
        "best_for": ["Backend changes"],
        "boundaries": ["No production deploys"],
        "fallback": "Ask the operator for an owner decision.",
    }
    member = {
        "principal": "hermes",
        "kind": "agent",
        "display_name": "Hermes",
        "role": "member",
        "mention_token": "@Hermes",
        "host": "s1",
        "can_post": True,
        "can_mention": True,
        "profile": profile,
    }

    _store(path, members=[member])
    reopened = _store(path, members=[member])

    public = next(item for item in reopened.members(room_id="ao") if item["principal"] == "hermes")
    assert public["profile"] == profile
    assert set(public["profile"]) == {"summary", "capabilities", "best_for", "boundaries", "fallback"}


@pytest.mark.parametrize(
    "profile",
    [
        "not-an-object",
        {"summary": "Missing the remaining fields"},
        {
            "summary": "Valid",
            "capabilities": [],
            "best_for": [],
            "boundaries": [],
            "fallback": "Valid",
            "private_note": "not public contract data",
        },
        {"summary": "x" * 1001, "capabilities": [], "best_for": [], "boundaries": [], "fallback": "Valid"},
        {"summary": "Valid", "capabilities": ["x"] * 21, "best_for": [], "boundaries": [], "fallback": "Valid"},
        {"summary": "Valid", "capabilities": ["x" * 201], "best_for": [], "boundaries": [], "fallback": "Valid"},
        {"summary": "Valid", "capabilities": [3], "best_for": [], "boundaries": [], "fallback": "Valid"},
    ],
)
def test_member_profile_rejects_values_outside_the_exact_bounded_schema(tmp_path, profile):
    member = {
        "principal": "hermes",
        "kind": "agent",
        "display_name": "Hermes",
        "role": "member",
        "mention_token": "@Hermes",
        "host": "s1",
        "can_post": True,
        "can_mention": True,
        "profile": profile,
    }

    with pytest.raises(ValueError, match="member profile"):
        _store(tmp_path / "agent-room.db", members=[member])


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


def test_reopen_reconciles_trusted_member_permissions_and_revocations(tmp_path):
    path = tmp_path / "agent-room.db"
    pc1 = {
        "principal": "pc1",
        "kind": "host",
        "display_name": "PC1",
        "role": "member",
        "mention_token": "@PC1",
        "host": "pc1",
        "can_post": True,
        "can_mention": False,
    }
    observer = {**pc1, "principal": "observer", "display_name": "Observer", "mention_token": "@Observer"}
    _store(path, members=[pc1, observer])

    reopened = _store(path, members=[{**pc1, "can_post": False}])

    assert {member["principal"] for member in reopened.members(room_id="ao")} == {"dennis", "pc1"}
    with pytest.raises(PermissionError, match="not a posting member"):
        reopened.post(room_id="ao", principal="pc1", client_message_id="pc1-1", body="Revoked")
    assert reopened.is_member(room_id="ao", principal="observer") is False


def test_duplicate_configured_principal_or_mention_token_fails_closed(tmp_path):
    duplicate_owner = {**OWNER, "role": "member", "can_post": False}
    with pytest.raises(ValueError, match="duplicate configured room principal"):
        _store(tmp_path / "principal.db", members=[duplicate_owner])

    first = {
        "principal": "one",
        "kind": "agent",
        "display_name": "One",
        "role": "member",
        "mention_token": "@Agent",
        "host": "s1",
        "can_post": True,
        "can_mention": False,
    }
    with pytest.raises(ValueError, match="duplicate configured mention token"):
        _store(tmp_path / "mention.db", members=[first, {**first, "principal": "two", "display_name": "Two"}])


def test_reopen_migrates_v02_mentions_with_origin_defaults(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE rooms (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE messages (
                id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id), sequence INTEGER NOT NULL,
                client_principal TEXT NOT NULL, client_message_id TEXT NOT NULL, author_principal TEXT NOT NULL,
                author_kind TEXT NOT NULL, body TEXT NOT NULL, thread_id TEXT NOT NULL,
                reply_to_message_id TEXT, created_at TEXT NOT NULL,
                UNIQUE(room_id, sequence), UNIQUE(room_id, client_principal, client_message_id)
            );
            CREATE TABLE members (
                room_id TEXT NOT NULL REFERENCES rooms(id), principal TEXT NOT NULL, kind TEXT NOT NULL,
                display_name TEXT NOT NULL, role TEXT NOT NULL, mention_token TEXT NOT NULL, host TEXT NOT NULL,
                can_post INTEGER NOT NULL, can_mention INTEGER NOT NULL,
                PRIMARY KEY(room_id, principal), UNIQUE(room_id, mention_token)
            );
            CREATE TABLE mentions (
                id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id),
                source_message_id TEXT NOT NULL REFERENCES messages(id), target_principal TEXT NOT NULL,
                token TEXT NOT NULL, delegate_name TEXT NOT NULL, status TEXT NOT NULL,
                reply_body TEXT, reply_message_id TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(source_message_id, target_principal)
            );
            CREATE TABLE cursors (
                room_id TEXT NOT NULL REFERENCES rooms(id), principal TEXT NOT NULL,
                last_sequence INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(room_id, principal)
            );
            INSERT INTO rooms VALUES ('ao', 'Agent Organization', '2026-08-21T00:00:00+00:00');
            INSERT INTO messages VALUES (
                'message-1', 'ao', 1, 'dennis', 'legacy-1', 'dennis', 'human',
                'Hello @Hermes', 'message-1', NULL, '2026-08-21T00:00:01+00:00'
            );
            INSERT INTO members VALUES ('ao', 'dennis', 'human', 'Dennis', 'owner', '@Dennis', 'operator', 1, 1);
            INSERT INTO mentions VALUES (
                'mention-1', 'ao', 'message-1', 'hermes', '@Hermes', 'hermes_s1', 'completed',
                'reply', 'reply-1', NULL, '2026-08-21T00:00:02+00:00', '2026-08-21T00:00:03+00:00'
            );
            """
        )

    reopened = _store(path)
    mention = reopened.mention("mention-1")

    assert mention["origin_message_id"] == "message-1"
    assert mention["parent_mention_id"] is None
    assert mention["origin_chain"] == ["hermes"]
    assert mention["hop_count"] == 0
    assert mention["position"] == 0
    assert mention["status"] == "completed"
