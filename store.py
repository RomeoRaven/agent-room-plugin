"""Durable canonical room store for agent-room-plugin.

Host-free by design: stdlib SQLite only. The first product slice owns one fixed
Agent Organization room; dynamic room lifecycle is deliberately deferred.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOM_ID = "ao"
ROOM_NAME = "Agent Organization"
DEFAULT_OWNER = {
    "principal": "operator",
    "kind": "human",
    "display_name": "Operator",
    "role": "owner",
    "mention_token": "@Operator",
    "host": "operator",
    "can_post": True,
    "can_mention": True,
}


class RoomConflict(ValueError):
    """A stable id was reused for materially different room content."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RoomStore:
    def __init__(self, path: str | Path, *, owner: dict | None = None, members: list[dict] | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize(owner or DEFAULT_OWNER, members or [])

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _initialize(self, owner: dict, members: list[dict]) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL REFERENCES rooms(id),
                    sequence INTEGER NOT NULL,
                    client_principal TEXT NOT NULL,
                    client_message_id TEXT NOT NULL,
                    author_principal TEXT NOT NULL,
                    author_kind TEXT NOT NULL,
                    body TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    reply_to_message_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(room_id, sequence),
                    UNIQUE(room_id, client_principal, client_message_id)
                );
                CREATE TABLE IF NOT EXISTS members (
                    room_id TEXT NOT NULL REFERENCES rooms(id),
                    principal TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    mention_token TEXT NOT NULL,
                    host TEXT NOT NULL,
                    can_post INTEGER NOT NULL,
                    can_mention INTEGER NOT NULL,
                    PRIMARY KEY(room_id, principal),
                    UNIQUE(room_id, mention_token)
                );
                CREATE TABLE IF NOT EXISTS cursors (
                    room_id TEXT NOT NULL REFERENCES rooms(id),
                    principal TEXT NOT NULL,
                    last_sequence INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(room_id, principal)
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO rooms(id, name, created_at) VALUES (?, ?, ?)",
                (ROOM_ID, ROOM_NAME, _now()),
            )
            conn.execute(
                """INSERT OR IGNORE INTO members(
                       room_id, principal, kind, display_name, role,
                       mention_token, host, can_post, can_mention
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ROOM_ID,
                    str(owner["principal"]),
                    str(owner.get("kind") or "human"),
                    str(owner["display_name"]),
                    str(owner.get("role") or "owner"),
                    str(owner["mention_token"]),
                    str(owner.get("host") or "operator"),
                    int(bool(owner.get("can_post", True))),
                    int(bool(owner.get("can_mention", True))),
                ),
            )
            for member in members:
                conn.execute(
                    """INSERT OR IGNORE INTO members(
                           room_id, principal, kind, display_name, role,
                           mention_token, host, can_post, can_mention
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ROOM_ID,
                        str(member["principal"]),
                        str(member["kind"]),
                        str(member["display_name"]),
                        str(member.get("role") or "member"),
                        str(member["mention_token"]),
                        str(member["host"]),
                        int(bool(member.get("can_post", False))),
                        int(bool(member.get("can_mention", False))),
                    ),
                )

    @staticmethod
    def _message(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "room_id": row["room_id"],
            "sequence": row["sequence"],
            "client_message_id": row["client_message_id"],
            "author_principal": row["author_principal"],
            "author_kind": row["author_kind"],
            "body": row["body"],
            "thread_id": row["thread_id"],
            "reply_to_message_id": row["reply_to_message_id"],
            "created_at": row["created_at"],
        }

    def list_rooms(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, name, created_at FROM rooms ORDER BY created_at, id").fetchall()
        return [dict(row) for row in rows]

    def post(
        self,
        *,
        room_id: str,
        principal: str,
        client_message_id: str,
        body: str,
        author_kind: str = "human",
        thread_id: str | None = None,
        reply_to_message_id: str | None = None,
    ) -> dict:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            member = conn.execute(
                "SELECT can_post FROM members WHERE room_id=? AND principal=?",
                (room_id, principal),
            ).fetchone()
            if member is None or not bool(member["can_post"]):
                raise PermissionError(f"principal {principal!r} is not a posting member of room {room_id!r}")
            existing = conn.execute(
                """SELECT * FROM messages
                   WHERE room_id=? AND client_principal=? AND client_message_id=?""",
                (room_id, principal, client_message_id),
            ).fetchone()
            if existing is not None:
                same_content = (
                    existing["body"] == body
                    and existing["author_kind"] == author_kind
                    and existing["reply_to_message_id"] == reply_to_message_id
                    and (thread_id is None or existing["thread_id"] == thread_id)
                )
                if not same_content:
                    raise RoomConflict("client_message_id already exists with different content")
                conn.commit()
                return {"created": False, "message": self._message(existing)}

            room = conn.execute("SELECT 1 FROM rooms WHERE id=?", (room_id,)).fetchone()
            if room is None:
                raise KeyError(f"unknown room {room_id!r}")
            sequence = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM messages WHERE room_id=?",
                (room_id,),
            ).fetchone()[0]
            message_id = str(uuid.uuid4())
            canonical_thread = thread_id or message_id
            created_at = _now()
            conn.execute(
                """INSERT INTO messages(
                       id, room_id, sequence, client_principal, client_message_id,
                       author_principal, author_kind, body, thread_id,
                       reply_to_message_id, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id,
                    room_id,
                    sequence,
                    principal,
                    client_message_id,
                    principal,
                    author_kind,
                    body,
                    canonical_thread,
                    reply_to_message_id,
                    created_at,
                ),
            )
            row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            conn.commit()
        return {"created": True, "message": self._message(row)}

    def sync(self, *, room_id: str, after: int = 0, limit: int = 100) -> dict:
        page_size = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM messages
                   WHERE room_id=? AND sequence>?
                   ORDER BY sequence ASC LIMIT ?""",
                (room_id, max(0, int(after)), page_size + 1),
            ).fetchall()
        has_more = len(rows) > page_size
        page = rows[:page_size]
        messages = [self._message(row) for row in page]
        next_sequence = messages[-1]["sequence"] if messages else max(0, int(after))
        return {"messages": messages, "next_sequence": next_sequence, "has_more": has_more}

    def members(self, *, room_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT principal, kind, display_name, role, mention_token,
                          host, can_post, can_mention
                   FROM members WHERE room_id=? ORDER BY role, display_name, principal""",
                (room_id,),
            ).fetchall()
        return [
            {
                **{key: row[key] for key in ("principal", "kind", "display_name", "role", "mention_token", "host")},
                "can_post": bool(row["can_post"]),
                "can_mention": bool(row["can_mention"]),
            }
            for row in rows
        ]

    def is_member(self, *, room_id: str, principal: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM members WHERE room_id=? AND principal=?",
                (room_id, principal),
            ).fetchone()
        return row is not None

    def cursor(self, *, room_id: str, principal: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_sequence FROM cursors WHERE room_id=? AND principal=?",
                (room_id, principal),
            ).fetchone()
        return int(row["last_sequence"]) if row is not None else 0

    def ack(self, *, room_id: str, principal: str, sequence: int) -> dict:
        requested = max(0, int(sequence))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current_room = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS value FROM messages WHERE room_id=?",
                (room_id,),
            ).fetchone()["value"]
            if requested > current_room:
                raise ValueError("cannot acknowledge beyond current room sequence")
            current = conn.execute(
                "SELECT last_sequence FROM cursors WHERE room_id=? AND principal=?",
                (room_id, principal),
            ).fetchone()
            last = max(requested, int(current["last_sequence"]) if current is not None else 0)
            conn.execute(
                """INSERT INTO cursors(room_id, principal, last_sequence, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(room_id, principal) DO UPDATE SET
                     last_sequence=excluded.last_sequence,
                     updated_at=excluded.updated_at""",
                (room_id, principal, last, _now()),
            )
            conn.commit()
        return {"room_id": room_id, "principal": principal, "last_sequence": last}
