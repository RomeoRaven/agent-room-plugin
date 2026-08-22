"""Durable canonical room store for agent-room-plugin.

Host-free by design: stdlib SQLite only. The Agent Organization room is the
migration-safe default; every room uses the same durable contract.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
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
        self._owner = dict(owner or DEFAULT_OWNER)
        self._configured_members = [self._owner, *[dict(member) for member in (members or [])]]
        self._initialize(self._owner, self._configured_members[1:])

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _name_key(name: str) -> str:
        return unicodedata.normalize("NFKC", name).casefold()

    def _active_name_exists(self, conn: sqlite3.Connection, *, name: str, exclude_room_id: str | None = None) -> bool:
        key = self._name_key(name)
        rows = conn.execute("SELECT id, name FROM rooms WHERE status='active'").fetchall()
        return any(row["id"] != exclude_room_id and self._name_key(row["name"]) == key for row in rows)

    def _sync_configured_members(self, conn: sqlite3.Connection, room_id: str) -> None:
        principals = [str(member["principal"]).strip() for member in self._configured_members]
        placeholders = ",".join("?" for _ in principals)
        conn.execute(
            f"DELETE FROM members WHERE room_id=? AND principal NOT IN ({placeholders})",
            (room_id, *principals),
        )
        conn.execute(
            f"DELETE FROM cursors WHERE room_id=? AND principal NOT IN ({placeholders})",
            (room_id, *principals),
        )
        for member in self._configured_members:
            conn.execute(
                """INSERT INTO members(
                       room_id, principal, kind, display_name, role,
                       mention_token, host, can_post, can_mention
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(room_id, principal) DO UPDATE SET
                     kind=excluded.kind,
                     display_name=excluded.display_name,
                     role=excluded.role,
                     mention_token=excluded.mention_token,
                     host=excluded.host,
                     can_post=excluded.can_post,
                     can_mention=excluded.can_mention""",
                (
                    room_id,
                    str(member["principal"]).strip(),
                    str(member.get("kind") or "human"),
                    str(member["display_name"]),
                    str(member.get("role") or "member"),
                    str(member["mention_token"]).strip(),
                    str(member.get("host") or "operator"),
                    int(bool(member.get("can_post", member is self._owner))),
                    int(bool(member.get("can_mention", member is self._owner))),
                ),
            )

    def _initialize(self, owner: dict, members: list[dict]) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    active_from_sequence INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT
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
                CREATE TABLE IF NOT EXISTS mentions (
                    id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL REFERENCES rooms(id),
                    source_message_id TEXT NOT NULL REFERENCES messages(id),
                    target_principal TEXT NOT NULL,
                    token TEXT NOT NULL,
                    delegate_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    parent_mention_id TEXT REFERENCES mentions(id),
                    origin_message_id TEXT,
                    origin_chain TEXT NOT NULL DEFAULT '[]',
                    hop_count INTEGER NOT NULL DEFAULT 0,
                    position INTEGER NOT NULL DEFAULT 0,
                    reply_body TEXT,
                    reply_message_id TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_message_id, target_principal)
                );
                CREATE TABLE IF NOT EXISTS cursors (
                    room_id TEXT NOT NULL REFERENCES rooms(id),
                    principal TEXT NOT NULL,
                    last_sequence INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(room_id, principal)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    body,
                    content='messages',
                    content_rowid='rowid'
                );
                CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, body) VALUES (new.rowid, new.body);
                END;
                CREATE TABLE IF NOT EXISTS room_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            fts_backfilled = conn.execute("SELECT value FROM room_meta WHERE key='messages_fts_backfilled'").fetchone()
            if fts_backfilled is None:
                conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
                conn.execute("INSERT INTO room_meta(key, value) VALUES ('messages_fts_backfilled', '1')")
            room_columns = {row["name"] for row in conn.execute("PRAGMA table_info(rooms)").fetchall()}
            room_migrations = {
                "status": "ALTER TABLE rooms ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
                "active_from_sequence": "ALTER TABLE rooms ADD COLUMN active_from_sequence INTEGER NOT NULL DEFAULT 1",
                "updated_at": "ALTER TABLE rooms ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
                "archived_at": "ALTER TABLE rooms ADD COLUMN archived_at TEXT",
            }
            for column, statement in room_migrations.items():
                if column not in room_columns:
                    conn.execute(statement)
            conn.execute("UPDATE rooms SET updated_at=created_at WHERE updated_at='' OR updated_at IS NULL")
            mention_columns = {row["name"] for row in conn.execute("PRAGMA table_info(mentions)").fetchall()}
            migrations = {
                "parent_mention_id": "ALTER TABLE mentions ADD COLUMN parent_mention_id TEXT REFERENCES mentions(id)",
                "origin_message_id": "ALTER TABLE mentions ADD COLUMN origin_message_id TEXT",
                "origin_chain": "ALTER TABLE mentions ADD COLUMN origin_chain TEXT NOT NULL DEFAULT '[]'",
                "hop_count": "ALTER TABLE mentions ADD COLUMN hop_count INTEGER NOT NULL DEFAULT 0",
                "position": "ALTER TABLE mentions ADD COLUMN position INTEGER NOT NULL DEFAULT 0",
            }
            for column, statement in migrations.items():
                if column not in mention_columns:
                    conn.execute(statement)
            legacy_mentions = conn.execute(
                "SELECT id, source_message_id, target_principal, origin_message_id, origin_chain FROM mentions"
            ).fetchall()
            for mention in legacy_mentions:
                origin_message_id = mention["origin_message_id"] or mention["source_message_id"]
                try:
                    chain = json.loads(mention["origin_chain"] or "[]")
                except (TypeError, ValueError):
                    chain = []
                if not isinstance(chain, list) or not chain:
                    chain = [mention["target_principal"]]
                conn.execute(
                    "UPDATE mentions SET origin_message_id=?, origin_chain=? WHERE id=?",
                    (origin_message_id, json.dumps(chain), mention["id"]),
                )
            now = _now()
            conn.execute(
                """INSERT OR IGNORE INTO rooms(
                       id, name, status, active_from_sequence, created_at, updated_at, archived_at
                   ) VALUES (?, ?, 'active', 1, ?, ?, NULL)""",
                (ROOM_ID, ROOM_NAME, now, now),
            )
            configured = [owner, *members]
            principals = [str(member["principal"]).strip() for member in configured]
            principal_keys = [principal.casefold() for principal in principals]
            if any(not principal for principal in principals) or len(set(principal_keys)) != len(principal_keys):
                raise ValueError("duplicate configured room principal")
            mention_tokens = [str(member["mention_token"]).strip() for member in configured]
            mention_keys = [token.casefold() for token in mention_tokens]
            if any(not token for token in mention_tokens) or len(set(mention_keys)) != len(mention_keys):
                raise ValueError("duplicate configured mention token")
            for room in conn.execute("SELECT id FROM rooms").fetchall():
                self._sync_configured_members(conn, str(room["id"]))

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

    @staticmethod
    def _mention(row: sqlite3.Row) -> dict:
        mention = {
            key: row[key]
            for key in (
                "id",
                "room_id",
                "source_message_id",
                "target_principal",
                "token",
                "delegate_name",
                "status",
                "parent_mention_id",
                "origin_message_id",
                "hop_count",
                "position",
                "reply_body",
                "reply_message_id",
                "error",
                "created_at",
                "updated_at",
            )
        }
        mention["origin_chain"] = json.loads(row["origin_chain"] or "[]")
        return mention

    def room(self, *, room_id: str, principal: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT r.*,
                          COALESCE((SELECT MAX(sequence) FROM messages WHERE room_id=r.id), 0) AS latest_sequence,
                          COALESCE((SELECT COUNT(*) FROM messages WHERE room_id=r.id), 0) AS message_count,
                          COALESCE((SELECT COUNT(*) FROM messages WHERE room_id=r.id AND sequence>=r.active_from_sequence), 0) AS current_message_count,
                          COALESCE((SELECT MAX(created_at) FROM messages WHERE room_id=r.id), r.updated_at) AS last_activity_at,
                          COALESCE((SELECT last_sequence FROM cursors WHERE room_id=r.id AND principal=?), 0) AS cursor_sequence
                   FROM rooms AS r
                   JOIN members AS member ON member.room_id=r.id AND member.principal=?
                   WHERE r.id=?""",
                (principal, principal, room_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown room {room_id!r}")
        result = dict(row)
        floor = max(0, int(result["active_from_sequence"]) - 1)
        cursor_sequence = int(result.pop("cursor_sequence"))
        threshold = max(floor, cursor_sequence)
        result["unread_count"] = max(0, int(result["latest_sequence"]) - threshold)
        with self._connect() as conn:
            result["unread_mentions"] = int(
                conn.execute(
                    """SELECT COUNT(*) AS value FROM mentions AS mention
                       JOIN messages AS message ON message.id=mention.source_message_id
                       WHERE mention.room_id=? AND mention.target_principal=? AND message.sequence>?""",
                    (room_id, principal, threshold),
                ).fetchone()["value"]
            )
        result["archived"] = result["status"] == "archived"
        result["history_available"] = int(result["active_from_sequence"]) > 1
        return result

    def list_rooms(self, *, principal: str | None = None, status: str = "all") -> list[dict]:
        if status not in {"active", "archived", "all"}:
            raise ValueError("status must be active, archived, or all")
        bound = principal or str(self._owner["principal"])
        with self._connect() as conn:
            room_ids = [
                str(row["id"])
                for row in conn.execute(
                    """SELECT r.id FROM rooms AS r
                       JOIN members AS member ON member.room_id=r.id AND member.principal=?
                       WHERE (?='all' OR r.status=?)
                       ORDER BY r.updated_at DESC, r.created_at DESC, r.id""",
                    (bound, status, status),
                ).fetchall()
            ]
        return [self.room(room_id=room_id, principal=bound) for room_id in room_ids]

    def create_room(self, *, name: str, principal: str) -> dict:
        title = str(name or "").strip()
        if not title:
            raise ValueError("room name is required")
        if len(title) > 120:
            raise ValueError("room name exceeds 120 characters")
        room_id = str(uuid.uuid4())
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if principal != str(self._owner["principal"]).strip():
                raise PermissionError("only the configured room owner may create rooms")
            if self._active_name_exists(conn, name=title):
                raise RoomConflict("an active room already uses that name")
            conn.execute(
                """INSERT INTO rooms(
                       id, name, status, active_from_sequence, created_at, updated_at, archived_at
                   ) VALUES (?, ?, 'active', 1, ?, ?, NULL)""",
                (room_id, title, now, now),
            )
            self._sync_configured_members(conn, room_id)
            conn.commit()
        return self.room(room_id=room_id, principal=principal)

    def _require_owner(self, conn: sqlite3.Connection, *, room_id: str, principal: str) -> None:
        if principal != str(self._owner["principal"]).strip():
            raise PermissionError("only the configured room owner may change room lifecycle")
        owner = conn.execute(
            "SELECT 1 FROM members WHERE room_id=? AND principal=? AND role='owner'",
            (room_id, principal),
        ).fetchone()
        if owner is None:
            raise PermissionError("only the configured room owner may change room lifecycle")

    def rename_room(self, *, room_id: str, name: str, principal: str) -> dict:
        title = str(name or "").strip()
        if not title:
            raise ValueError("room name is required")
        if len(title) > 120:
            raise ValueError("room name exceeds 120 characters")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_owner(conn, room_id=room_id, principal=principal)
            room = conn.execute("SELECT status FROM rooms WHERE id=?", (room_id,)).fetchone()
            if room is None:
                raise KeyError(f"unknown room {room_id!r}")
            if room["status"] == "active" and self._active_name_exists(conn, name=title, exclude_room_id=room_id):
                raise RoomConflict("an active room already uses that name")
            conn.execute("UPDATE rooms SET name=?, updated_at=? WHERE id=?", (title, _now(), room_id))
            conn.commit()
        return self.room(room_id=room_id, principal=principal)

    def archive_room(self, *, room_id: str, principal: str) -> dict:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_owner(conn, room_id=room_id, principal=principal)
            pending = conn.execute(
                """SELECT COUNT(*) AS value FROM mentions
                   WHERE room_id=? AND status IN ('pending', 'invoking', 'reply_ready')""",
                (room_id,),
            ).fetchone()["value"]
            if int(pending):
                raise RoomConflict("room has pending agent delivery")
            changed = conn.execute(
                """UPDATE rooms SET status='archived', archived_at=?, updated_at=?
                   WHERE id=? AND status='active'""",
                (now, now, room_id),
            ).rowcount
            if changed != 1:
                raise RoomConflict("room is already archived")
            conn.commit()
        return self.room(room_id=room_id, principal=principal)

    def restore_room(self, *, room_id: str, principal: str) -> dict:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_owner(conn, room_id=room_id, principal=principal)
            room = conn.execute("SELECT name, status FROM rooms WHERE id=?", (room_id,)).fetchone()
            if room is None:
                raise KeyError(f"unknown room {room_id!r}")
            if room["status"] != "archived":
                raise RoomConflict("room is already active")
            if self._active_name_exists(conn, name=room["name"], exclude_room_id=room_id):
                raise RoomConflict("an active room already uses that name")
            conn.execute(
                "UPDATE rooms SET status='active', archived_at=NULL, updated_at=? WHERE id=?",
                (now, room_id),
            )
            conn.commit()
        return self.room(room_id=room_id, principal=principal)

    def reset_room(self, *, room_id: str, principal: str) -> dict:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_owner(conn, room_id=room_id, principal=principal)
            room = conn.execute("SELECT status FROM rooms WHERE id=?", (room_id,)).fetchone()
            if room is None:
                raise KeyError(f"unknown room {room_id!r}")
            if room["status"] != "active":
                raise RoomConflict("archived room is read-only")
            pending = conn.execute(
                """SELECT COUNT(*) AS value FROM mentions
                   WHERE room_id=? AND status IN ('pending', 'invoking', 'reply_ready')""",
                (room_id,),
            ).fetchone()["value"]
            if int(pending):
                raise RoomConflict("room has pending agent delivery")
            next_sequence = int(
                conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 AS value FROM messages WHERE room_id=?",
                    (room_id,),
                ).fetchone()["value"]
            )
            conn.execute(
                "UPDATE rooms SET active_from_sequence=?, updated_at=? WHERE id=?",
                (next_sequence, now, room_id),
            )
            conn.commit()
        return self.room(room_id=room_id, principal=principal)

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
        mentions: list[dict] | None = None,
    ) -> dict:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            room = conn.execute(
                "SELECT status, active_from_sequence FROM rooms WHERE id=?",
                (room_id,),
            ).fetchone()
            if room is None:
                raise KeyError(f"unknown room {room_id!r}")
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
                mention_rows = conn.execute(
                    "SELECT * FROM mentions WHERE source_message_id=? ORDER BY position, created_at, id",
                    (existing["id"],),
                ).fetchall()
                conn.commit()
                result = {
                    "created": False,
                    "message": self._message(existing),
                }
                if mention_rows:
                    result["mentions"] = [self._mention(row) for row in mention_rows]
                return result

            if room["status"] != "active":
                raise RoomConflict("archived room is read-only")
            reply_target = None
            if reply_to_message_id:
                reply_target = conn.execute(
                    "SELECT room_id, sequence, thread_id FROM messages WHERE id=?",
                    (reply_to_message_id,),
                ).fetchone()
                if (
                    reply_target is None
                    or reply_target["room_id"] != room_id
                    or int(reply_target["sequence"]) < int(room["active_from_sequence"])
                ):
                    raise RoomConflict("reply target is outside current room history")
                if thread_id is not None and thread_id != reply_target["thread_id"]:
                    raise RoomConflict("reply thread does not match target thread")
            sequence = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM messages WHERE room_id=?",
                (room_id,),
            ).fetchone()[0]
            message_id = str(uuid.uuid4())
            canonical_thread = thread_id or (reply_target["thread_id"] if reply_target is not None else message_id)
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
            for mention in mentions or []:
                mention_id = str(uuid.uuid4())
                mention_created_at = _now()
                target_principal = str(mention["target_principal"])
                origin_chain = mention.get("origin_chain") or [target_principal]
                status = str(mention.get("status") or "pending")
                error = str(mention.get("error") or "") or None
                rate_limit = max(0, int(mention.get("rate_limit") or 0))
                rate_since = str(mention.get("rate_since") or "")
                if status == "pending" and rate_limit and rate_since:
                    recent = conn.execute(
                        """SELECT COUNT(*) AS value FROM mentions
                           WHERE room_id=? AND target_principal=? AND created_at>=? AND status!='blocked'""",
                        (room_id, target_principal, rate_since),
                    ).fetchone()["value"]
                    if int(recent) >= rate_limit:
                        status = "blocked"
                        error = "mention rate limit reached"
                conn.execute(
                    """INSERT INTO mentions(
                           id, room_id, source_message_id, target_principal,
                           token, delegate_name, status, parent_mention_id,
                           origin_message_id, origin_chain, hop_count, position,
                           error, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        mention_id,
                        room_id,
                        message_id,
                        target_principal,
                        str(mention["token"]),
                        str(mention["delegate_name"]),
                        status,
                        mention.get("parent_mention_id"),
                        str(mention.get("origin_message_id") or message_id),
                        json.dumps([str(principal) for principal in origin_chain]),
                        max(0, int(mention.get("hop_count") or 0)),
                        max(0, int(mention.get("position") or 0)),
                        error,
                        mention_created_at,
                        mention_created_at,
                    ),
                )
            conn.execute("UPDATE rooms SET updated_at=? WHERE id=?", (created_at, room_id))
            row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            mention_rows = conn.execute(
                "SELECT * FROM mentions WHERE source_message_id=? ORDER BY position, created_at, id",
                (message_id,),
            ).fetchall()
            conn.commit()
        result = {
            "created": True,
            "message": self._message(row),
        }
        if mention_rows:
            result["mentions"] = [self._mention(mention_row) for mention_row in mention_rows]
        return result

    def pending_mentions(self, *, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM mentions WHERE status='pending'
                   ORDER BY created_at, id LIMIT ?""",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [self._mention(row) for row in rows]

    def mentions_for_messages(self, message_ids: list[str]) -> list[dict]:
        if not message_ids:
            return []
        placeholders = ",".join("?" for _ in message_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT m.* FROM mentions AS m
                    JOIN messages AS msg ON msg.id=m.source_message_id
                    WHERE m.source_message_id IN ({placeholders})
                    ORDER BY msg.sequence, m.position, m.created_at, m.id""",
                tuple(message_ids),
            ).fetchall()
        return [self._mention(row) for row in rows]

    def mention(self, mention_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM mentions WHERE id=?", (mention_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown mention {mention_id!r}")
        return self._mention(row)

    def claim_mention_work(self) -> dict | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT m.*, msg.body AS source_body,
                          msg.thread_id AS source_thread_id,
                          msg.sequence AS source_sequence,
                          room.active_from_sequence AS source_generation
                   FROM mentions AS m
                   JOIN messages AS msg ON msg.id=m.source_message_id
                   JOIN rooms AS room ON room.id=m.room_id
                   WHERE m.status IN ('reply_ready', 'pending')
                     AND m.delegate_name NOT LIKE 'remote:%'
                     AND room.status='active'
                     AND msg.sequence>=room.active_from_sequence
                   ORDER BY CASE m.status WHEN 'reply_ready' THEN 0 ELSE 1 END,
                            msg.sequence, m.position, m.created_at, m.id LIMIT 1"""
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            if row["status"] == "pending":
                updated_at = _now()
                conn.execute(
                    "UPDATE mentions SET status='invoking', updated_at=? WHERE id=? AND status='pending'",
                    (updated_at, row["id"]),
                )
                status = "invoking"
            else:
                updated_at = row["updated_at"]
                status = row["status"]
            conn.commit()
        return {
            **self._mention(row),
            "status": status,
            "updated_at": updated_at,
            "source_body": row["source_body"],
            "source_thread_id": row["source_thread_id"],
            "source_sequence": row["source_sequence"],
            "source_generation": row["source_generation"],
        }

    def recent_mention_count(self, *, room_id: str, target_principal: str, since: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS value FROM mentions
                   WHERE room_id=? AND target_principal=? AND created_at>=? AND status!='blocked'""",
                (room_id, target_principal, since),
            ).fetchone()
        return int(row["value"])

    def thread_context(self, *, room_id: str, thread_id: str, through_sequence: int, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT msg.* FROM messages AS msg
                   JOIN rooms AS room ON room.id=msg.room_id
                   WHERE msg.room_id=? AND msg.thread_id=? AND msg.sequence<=?
                     AND msg.sequence>=room.active_from_sequence
                   ORDER BY msg.sequence DESC LIMIT ?""",
                (room_id, thread_id, through_sequence, max(1, min(int(limit), 20))),
            ).fetchall()
        return [self._message(row) for row in reversed(rows)]

    def save_mention_reply(self, mention_id: str, reply_body: str) -> dict:
        body = str(reply_body or "").strip()
        if not body:
            raise ValueError("delegate returned an empty room reply")
        if len(body) > 20000:
            raise ValueError("delegate room reply exceeds 20000 characters")
        with self._connect() as conn:
            changed = conn.execute(
                """UPDATE mentions SET status='reply_ready', reply_body=?, error=NULL, updated_at=?
                   WHERE id=? AND status='invoking'""",
                (body, _now(), mention_id),
            ).rowcount
            if changed != 1:
                raise RoomConflict("mention is not in invoking state")
            row = conn.execute("SELECT * FROM mentions WHERE id=?", (mention_id,)).fetchone()
        return self._mention(row)

    def complete_mention(self, mention_id: str, reply_message_id: str) -> dict:
        with self._connect() as conn:
            changed = conn.execute(
                """UPDATE mentions SET status='completed', reply_message_id=?, error=NULL, updated_at=?
                   WHERE id=? AND status='reply_ready'""",
                (reply_message_id, _now(), mention_id),
            ).rowcount
            if changed != 1:
                raise RoomConflict("mention reply is not ready for completion")
            row = conn.execute("SELECT * FROM mentions WHERE id=?", (mention_id,)).fetchone()
        return self._mention(row)

    def complete_remote_mention(self, mention_id: str, *, target_principal: str, reply_message_id: str) -> dict:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM mentions WHERE id=?", (mention_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown mention {mention_id!r}")
            if row["target_principal"] != target_principal or not str(row["delegate_name"]).startswith("remote:"):
                raise PermissionError("remote mention completion does not match the attested target")
            if row["status"] == "completed":
                if row["reply_message_id"] != reply_message_id:
                    raise RoomConflict("remote mention was completed by a different reply")
                conn.commit()
                return self._mention(row)
            if row["status"] != "pending":
                raise RoomConflict("remote mention is not pending")
            conn.execute(
                """UPDATE mentions SET status='completed', reply_message_id=?, error=NULL, updated_at=?
                   WHERE id=? AND status='pending'""",
                (reply_message_id, _now(), mention_id),
            )
            updated = conn.execute("SELECT * FROM mentions WHERE id=?", (mention_id,)).fetchone()
            conn.commit()
        return self._mention(updated)

    def fail_mention(self, mention_id: str, error: str) -> dict:
        detail = str(error or "mention dispatch failed").strip()[:1000]
        with self._connect() as conn:
            conn.execute(
                """UPDATE mentions SET status='failed', error=?, updated_at=?
                   WHERE id=? AND status IN ('invoking', 'reply_ready')""",
                (detail, _now(), mention_id),
            )
            row = conn.execute("SELECT * FROM mentions WHERE id=?", (mention_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown mention {mention_id!r}")
        return self._mention(row)

    def mark_interrupted_mentions_ambiguous(self) -> int:
        with self._connect() as conn:
            changed = conn.execute(
                """UPDATE mentions
                   SET status='ambiguous',
                       error='restart interrupted a possible delegate invocation; automatic replay blocked',
                       updated_at=?
                   WHERE status='invoking'""",
                (_now(),),
            ).rowcount
        return int(changed)

    def sync(
        self,
        *,
        room_id: str,
        after: int | None = None,
        before: int | None = None,
        around: int | None = None,
        limit: int = 100,
        history: bool = False,
    ) -> dict:
        page_size = max(1, min(int(limit), 200))
        selected_modes = sum(value is not None for value in (after, before, around))
        if selected_modes > 1:
            raise ValueError("choose only one of after, before, or around")
        with self._connect() as conn:
            room = conn.execute(
                "SELECT active_from_sequence FROM rooms WHERE id=?",
                (room_id,),
            ).fetchone()
            if room is None:
                raise KeyError(f"unknown room {room_id!r}")
            active_from = int(room["active_from_sequence"])
            lower_bound = 1 if history else active_from
            window_sequence = None
            if around is not None:
                window_sequence = max(1, int(around))
                start = max(lower_bound, window_sequence - (page_size // 2))
                rows = conn.execute(
                    """SELECT * FROM messages
                       WHERE room_id=? AND sequence>=? AND sequence<?
                       ORDER BY sequence ASC LIMIT ?""",
                    (room_id, start, start + page_size, page_size),
                ).fetchall()
                has_more = False
                has_older = bool(rows and int(rows[0]["sequence"]) > lower_bound)
            elif after is not None:
                cursor = max(0, int(after))
                rows = conn.execute(
                    """SELECT * FROM messages
                       WHERE room_id=? AND sequence>=? AND sequence>?
                       ORDER BY sequence ASC LIMIT ?""",
                    (room_id, lower_bound, cursor, page_size + 1),
                ).fetchall()
                has_more = len(rows) > page_size
                rows = rows[:page_size]
                has_older = bool(rows and int(rows[0]["sequence"]) > lower_bound)
            else:
                ceiling = max(1, int(before)) if before is not None else 9223372036854775807
                descending = conn.execute(
                    """SELECT * FROM messages
                       WHERE room_id=? AND sequence>=? AND sequence<?
                       ORDER BY sequence DESC LIMIT ?""",
                    (room_id, lower_bound, ceiling, page_size + 1),
                ).fetchall()
                has_older = len(descending) > page_size
                rows = list(reversed(descending[:page_size]))
                has_more = False
        messages = [self._message(row) for row in rows]
        default_cursor = max(0, int(after)) if after is not None else 0
        next_sequence = messages[-1]["sequence"] if messages else default_cursor
        result = {
            "messages": messages,
            "next_sequence": next_sequence,
            "has_more": has_more,
            "has_older": has_older,
            "oldest_sequence": messages[0]["sequence"] if messages else None,
            "active_from_sequence": active_from,
            "history_available": active_from > 1,
        }
        if window_sequence is not None:
            result["window_sequence"] = window_sequence
        return result

    def search(
        self,
        *,
        query: str,
        principal: str,
        scope: str = "current",
        room_id: str | None = None,
        history: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        if scope not in {"current", "all", "archived"}:
            raise ValueError("search scope must be current, all, or archived")
        if scope == "current" and not room_id:
            raise ValueError("room_id is required for current-room search")
        terms = re.findall(r"[\w-]+", str(query or ""), flags=re.UNICODE)[:20]
        if not terms:
            raise ValueError("search query needs a word")
        fts_query = " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"*' for term in terms)
        conditions = ["member.principal=?", "messages_fts MATCH ?"]
        params: list[object] = [principal, fts_query]
        if scope == "current":
            conditions.append("r.id=?")
            params.append(str(room_id))
            if not history:
                conditions.append("msg.sequence>=r.active_from_sequence")
        elif scope == "all":
            conditions.append("r.status='active'")
            if not history:
                conditions.append("msg.sequence>=r.active_from_sequence")
        else:
            conditions.append("r.status='archived'")
        params.append(max(1, min(int(limit), 100)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT msg.*, r.name AS room_name, r.status AS room_status,
                           r.active_from_sequence,
                           snippet(messages_fts, 0, '[', ']', '…', 16) AS snippet
                    FROM messages_fts
                    JOIN messages AS msg ON msg.rowid=messages_fts.rowid
                    JOIN rooms AS r ON r.id=msg.room_id
                    JOIN members AS member ON member.room_id=r.id
                    WHERE {" AND ".join(conditions)}
                    ORDER BY bm25(messages_fts), msg.created_at DESC, msg.id
                    LIMIT ?""",
                tuple(params),
            ).fetchall()
        return [
            {
                **self._message(row),
                "room_name": row["room_name"],
                "room_status": row["room_status"],
                "snippet": row["snippet"],
                "earlier": int(row["sequence"]) < int(row["active_from_sequence"]),
            }
            for row in rows
        ]

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
