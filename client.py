"""PC1 client-mode state and lifecycle; never stores canonical Room messages."""

from __future__ import annotations

import sqlite3
import asyncio
import json
import ssl
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


CONTRACT_VERSION = "1"


class Peer(Protocol):
    def execute(self, operation: str, payload: dict) -> dict: ...


class PeerUnavailable(RuntimeError):
    pass


class ClientConflict(RuntimeError):
    pass


class PeerRejected(RuntimeError):
    pass


class A2APeer:
    def __init__(
        self,
        url: str,
        token_file: Path,
        *,
        open_request=urllib.request.urlopen,
        timeout: float = 30,
        poll_interval: float = 0.25,
        max_polls: int = 120,
        ca_file: Path | None = None,
    ) -> None:
        self.url = str(url or "").strip()
        self.token_file = Path(token_file)
        self.open_request = open_request
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self.ssl_context = ssl.create_default_context(cafile=str(ca_file)) if ca_file else None
        if not self.url.startswith("https://"):
            raise ValueError("peer_url must use https")
        if not self.token_file.is_file():
            raise ValueError("peer_token_file does not exist")

    def _rpc(self, body: dict) -> dict:
        token = self.token_file.read_text().strip()
        if not token:
            raise ValueError("peer credential is empty")
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "A2A-Version": "1.0",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            if self.ssl_context is not None:
                response_context = self.open_request(request, timeout=self.timeout, context=self.ssl_context)
            else:
                response_context = self.open_request(request, timeout=self.timeout)
            with response_context as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise PeerRejected(f"peer rejected request with HTTP {exc.code}") from exc
            if 400 <= exc.code < 500:
                raise ValueError(f"peer rejected request with HTTP {exc.code}") from exc
            raise PeerUnavailable(f"peer HTTP failure {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PeerUnavailable("room owner is unavailable") from exc
        if payload.get("error"):
            raise ValueError(f"peer A2A error: {payload['error']}")
        return payload

    @staticmethod
    def _state(task: dict) -> str:
        return str((task.get("status") or {}).get("state") or "")

    @staticmethod
    def _result(task: dict, operation: str) -> dict:
        for artifact in task.get("artifacts") or []:
            for part in artifact.get("parts") or []:
                data = part.get("data")
                if data is None and isinstance(part.get("content"), dict):
                    data = part["content"].get("value")
                if not isinstance(data, dict):
                    continue
                if data.get("contract_version") != CONTRACT_VERSION or data.get("operation") != operation:
                    raise ValueError("peer returned a mismatched Agent Room envelope")
                result = data.get("result")
                if not isinstance(result, dict):
                    raise ValueError("peer Agent Room result must be an object")
                return result
        raise ValueError("peer task completed without an Agent Room result")

    def execute(self, operation: str, payload: dict) -> dict:
        call_id = uuid.uuid4().hex
        send = self._rpc(
            {
                "jsonrpc": "2.0",
                "id": "send",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": call_id,
                        "role": "ROLE_USER",
                        "parts": [{"text": "deterministic agent-room operation"}],
                    },
                    "metadata": {
                        "skillHint": "agent-room-v1",
                        "agent_room": {
                            "contract_version": CONTRACT_VERSION,
                            "operation": operation,
                            "payload": payload,
                        },
                    },
                },
            }
        )
        task = (send.get("result") or {}).get("task") or {}
        task_id = task.get("id")
        if not task_id:
            raise ValueError("peer A2A response omitted task id")
        for _ in range(self.max_polls):
            state = self._state(task)
            if state == "TASK_STATE_COMPLETED":
                return self._result(task, operation)
            if state in {"TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}:
                raise ValueError(f"peer Agent Room task ended in {state}")
            if self.poll_interval:
                time.sleep(self.poll_interval)
            current = self._rpc({"jsonrpc": "2.0", "id": "get", "method": "GetTask", "params": {"id": task_id}})
            task = (current.get("result") or {}).get("task") or current.get("result") or {}
        raise PeerUnavailable("peer Agent Room task did not complete before timeout")


class ClientState:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS pending_posts (
                    room_id TEXT NOT NULL,
                    client_message_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    thread_id TEXT,
                    reply_to_message_id TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (room_id, client_message_id)
                );
                CREATE TABLE IF NOT EXISTS cursors (
                    room_id TEXT PRIMARY KEY,
                    last_sequence INTEGER NOT NULL DEFAULT 0,
                    remote_sequence INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(cursors)")}
            if "remote_sequence" not in columns:
                conn.execute("ALTER TABLE cursors ADD COLUMN remote_sequence INTEGER NOT NULL DEFAULT 0")
            pending_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(pending_posts)")}
            if "thread_id" not in pending_columns:
                conn.execute("ALTER TABLE pending_posts ADD COLUMN thread_id TEXT")
            if "reply_to_message_id" not in pending_columns:
                conn.execute("ALTER TABLE pending_posts ADD COLUMN reply_to_message_id TEXT")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def cursor(self, room_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT last_sequence FROM cursors WHERE room_id=?", (room_id,)).fetchone()
        return int(row[0]) if row else 0

    def set_cursor(self, room_id: str, sequence: int) -> int:
        requested = max(0, int(sequence))
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO cursors(room_id,last_sequence,remote_sequence) VALUES(?,?,0)
                   ON CONFLICT(room_id) DO UPDATE SET
                     last_sequence=MAX(cursors.last_sequence,excluded.last_sequence)""",
                (room_id, requested),
            )
            value = conn.execute("SELECT last_sequence FROM cursors WHERE room_id=?", (room_id,)).fetchone()[0]
        return int(value)

    def pending_ack(self, room_id: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_sequence,remote_sequence FROM cursors WHERE room_id=?", (room_id,)
            ).fetchone()
        if not row or int(row[0]) <= int(row[1]):
            return None
        return int(row[0])

    def mark_acknowledged(self, room_id: str, sequence: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursors SET remote_sequence=MAX(remote_sequence,?) WHERE room_id=?",
                (int(sequence), room_id),
            )

    def pending(self, room_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT client_message_id,body,created_at,thread_id,reply_to_message_id
                   FROM pending_posts WHERE room_id=? ORDER BY created_at""",
                (room_id,),
            ).fetchall()
        return [
            {
                "room_id": room_id,
                "client_message_id": row[0],
                "body": row[1],
                "created_at": row[2],
                "thread_id": row[3],
                "reply_to_message_id": row[4],
                "status": "pending",
            }
            for row in rows
        ]

    def queue_post(
        self,
        room_id: str,
        client_message_id: str,
        body: str,
        *,
        thread_id: str | None = None,
        reply_to_message_id: str | None = None,
    ) -> dict:
        message_id = str(client_message_id or "").strip()
        text = str(body or "").strip()
        if not message_id or not text:
            raise ValueError("client_message_id and body are required")
        with self._connect() as conn:
            existing = conn.execute(
                """SELECT body,created_at,thread_id,reply_to_message_id FROM pending_posts
                   WHERE room_id=? AND client_message_id=?""",
                (room_id, message_id),
            ).fetchone()
            if existing:
                if (str(existing[0]), existing[2], existing[3]) != (text, thread_id, reply_to_message_id):
                    raise ClientConflict("client_message_id was already queued with different content")
                created_at = str(existing[1])
            else:
                created_at = datetime.now(UTC).isoformat()
                conn.execute(
                    """INSERT INTO pending_posts(
                         room_id,client_message_id,body,thread_id,reply_to_message_id,created_at
                       ) VALUES(?,?,?,?,?,?)""",
                    (room_id, message_id, text, thread_id, reply_to_message_id, created_at),
                )
        return {
            "room_id": room_id,
            "client_message_id": message_id,
            "body": text,
            "created_at": created_at,
            "thread_id": thread_id,
            "reply_to_message_id": reply_to_message_id,
            "status": "pending",
        }

    def remove_pending(self, room_id: str, client_message_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM pending_posts WHERE room_id=? AND client_message_id=?",
                (room_id, client_message_id),
            )


class ClientRoomService:
    def __init__(self, state: ClientState, peer: Peer) -> None:
        self.state = state
        self.peer = peer
        self.owner_online = True

    @staticmethod
    def _room(room_id: str) -> str:
        if room_id != "ao":
            raise KeyError(f"unknown room {room_id!r}")
        return room_id

    def list_rooms(self) -> dict:
        return {
            "contract_version": CONTRACT_VERSION,
            "rooms": [
                {
                    "id": "ao",
                    "name": "Agent Organization",
                    "created_at": "",
                    "latest_sequence": self.state.cursor("ao"),
                    "client_mode": True,
                    "owner_online": self.owner_online,
                }
            ],
        }

    @staticmethod
    def _validated_post(result: dict, outbound: dict) -> dict:
        message = result.get("message") if isinstance(result, dict) else None
        if (
            not isinstance(message, dict)
            or message.get("room_id") != outbound["room_id"]
            or message.get("client_message_id") != outbound["client_message_id"]
            or not str(message.get("id") or "").strip()
            or int(message.get("sequence") or 0) <= 0
        ):
            raise ValueError("peer did not confirm a matching canonical message")
        return result

    def post(self, room_id: str, payload: dict) -> dict:
        room = self._room(room_id)
        outbound = {
            "room_id": room,
            "client_message_id": str(payload.get("client_message_id") or "").strip(),
            "body": str(payload.get("body") or "").strip(),
        }
        for field in ("thread_id", "reply_to_message_id"):
            value = str(payload.get(field) or "").strip()
            if value:
                outbound[field] = value
        if not outbound["client_message_id"] or not outbound["body"]:
            raise ValueError("client_message_id and body are required")
        try:
            result = self._validated_post(self.peer.execute("room.post", outbound), outbound)
        except PeerUnavailable:
            self.owner_online = False
            pending = self.state.queue_post(
                room,
                outbound["client_message_id"],
                outbound["body"],
                thread_id=outbound.get("thread_id"),
                reply_to_message_id=outbound.get("reply_to_message_id"),
            )
            return {
                "contract_version": CONTRACT_VERSION,
                "operation": "room.post",
                "result": {"created": False, "pending": True, "pending_post": pending},
            }
        self.owner_online = True
        return {"contract_version": CONTRACT_VERSION, "operation": "room.post", "result": result}

    def sync(self, room_id: str, *, after: int, limit: int) -> dict:
        room = self._room(room_id)
        try:
            result = self.peer.execute("room.sync", {"room_id": room, "after": int(after), "limit": int(limit)})
        except PeerUnavailable:
            self.owner_online = False
            result = {
                "messages": [],
                "mentions": [],
                "next_sequence": self.state.cursor(room),
                "has_more": False,
                "has_older": False,
                "oldest_sequence": None,
                "active_from_sequence": 1,
                "history_available": False,
                "pending_posts": self.state.pending(room),
                "owner_online": False,
            }
            return {"contract_version": CONTRACT_VERSION, "operation": "room.sync", "result": result}
        self.owner_online = True
        result = {**result, "pending_posts": self.state.pending(room), "owner_online": True}
        return {"contract_version": CONTRACT_VERSION, "operation": "room.sync", "result": result}

    def ack(self, room_id: str, sequence: int) -> dict:
        room = self._room(room_id)
        desired = self.state.set_cursor(room, int(sequence))
        try:
            result = self.peer.execute("room.ack", {"room_id": room, "sequence": desired})
        except PeerUnavailable:
            self.owner_online = False
            return {
                "contract_version": CONTRACT_VERSION,
                "operation": "room.ack",
                "result": {"room_id": room, "principal": "pc1", "last_sequence": desired, "pending": True},
            }
        self.owner_online = True
        self.state.mark_acknowledged(room, int(result.get("last_sequence") or desired))
        return {"contract_version": CONTRACT_VERSION, "operation": "room.ack", "result": result}

    def members(self, room_id: str) -> dict:
        room = self._room(room_id)
        try:
            result = self.peer.execute("room.members", {"room_id": room})
        except PeerUnavailable:
            self.owner_online = False
            result = {"members": [], "owner_online": False}
            return {"contract_version": CONTRACT_VERSION, "operation": "room.members", "result": result}
        self.owner_online = True
        return {"contract_version": CONTRACT_VERSION, "operation": "room.members", "result": result}

    def reconcile_once(self) -> int:
        reconciled = 0
        for pending in self.state.pending("ao"):
            outbound = {
                "room_id": "ao",
                "client_message_id": pending["client_message_id"],
                "body": pending["body"],
            }
            for field in ("thread_id", "reply_to_message_id"):
                if pending.get(field):
                    outbound[field] = pending[field]
            self._validated_post(self.peer.execute("room.post", outbound), outbound)
            self.state.remove_pending("ao", pending["client_message_id"])
            reconciled += 1
        desired = self.state.pending_ack("ao")
        if desired is not None:
            result = self.peer.execute("room.ack", {"room_id": "ao", "sequence": desired})
            self.state.mark_acknowledged("ao", int(result.get("last_sequence") or desired))
            reconciled += 1
        self.owner_online = True
        return reconciled


class PeerReconciler:
    def __init__(self, service: ClientRoomService, *, interval: float = 5) -> None:
        self.service = service
        self.interval = max(0.1, float(interval))
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.service.reconcile_once)
            except PeerUnavailable:
                self.service.owner_online = False
            await asyncio.sleep(self.interval)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="agent-room-peer-reconciliation")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
