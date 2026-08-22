from __future__ import annotations

import json
import sqlite3

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
    "host": "local",
    "can_post": True,
    "can_mention": True,
}


def test_v04_database_migrates_without_losing_room_messages_mentions_or_cursor(tmp_path):
    path = tmp_path / "agent-room.db"
    conn = sqlite3.connect(path)
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
          parent_mention_id TEXT REFERENCES mentions(id), origin_message_id TEXT,
          origin_chain TEXT NOT NULL DEFAULT '[]', hop_count INTEGER NOT NULL DEFAULT 0,
          position INTEGER NOT NULL DEFAULT 0, reply_body TEXT, reply_message_id TEXT, error TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(source_message_id, target_principal)
        );
        CREATE TABLE cursors (
          room_id TEXT NOT NULL REFERENCES rooms(id), principal TEXT NOT NULL,
          last_sequence INTEGER NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY(room_id, principal)
        );
        """
    )
    stamp = "2026-08-21T20:00:00+00:00"
    conn.execute("INSERT INTO rooms VALUES ('ao', 'Agent Organization', ?)", (stamp,))
    conn.execute("INSERT INTO members VALUES ('ao', 'dennis', 'human', 'Dennis', 'owner', '@Dennis', 'operator', 1, 1)")
    conn.execute("INSERT INTO members VALUES ('ao', 'hermes', 'agent', 'Hermes', 'member', '@Hermes', 'local', 1, 1)")
    conn.execute(
        "INSERT INTO messages VALUES ('m1', 'ao', 1, 'dennis', 'legacy-1', 'dennis', 'human', 'legacy searchable', 'm1', NULL, ?)",
        (stamp,),
    )
    conn.execute(
        """INSERT INTO mentions VALUES (
          'mention1', 'ao', 'm1', 'hermes', '@Hermes', 'hermes_local', 'completed',
          NULL, 'm1', ?, 0, 0, NULL, 'm2', NULL, ?, ?
        )""",
        (json.dumps(["hermes"]), stamp, stamp),
    )
    conn.execute("INSERT INTO cursors VALUES ('ao', 'dennis', 1, ?)", (stamp,))
    conn.commit()
    conn.close()

    store = RoomStore(path, owner=OWNER, members=[HERMES])
    room = store.room(room_id="ao", principal="dennis")
    synced = store.sync(room_id="ao", after=0, limit=20, history=True)
    searched = store.search(query="legacy", principal="dennis", scope="current", room_id="ao", history=True)

    assert room["status"] == "active"
    assert room["active_from_sequence"] == 1
    assert room["latest_sequence"] == 1
    assert store.cursor(room_id="ao", principal="dennis") == 1
    assert [message["id"] for message in synced["messages"]] == ["m1"]
    assert store.mention("mention1")["reply_message_id"] == "m2"
    assert [result["id"] for result in searched] == ["m1"]
    assert store.list_rooms(principal="dennis", status="all")[0]["id"] == "ao"
