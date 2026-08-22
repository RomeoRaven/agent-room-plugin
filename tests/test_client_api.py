from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from client import ClientRoomService, ClientState, PeerReconciler, PeerUnavailable
from client_api import build_client_router


class FakePeer:
    def __init__(self):
        self.calls = []
        self.available = True

    def execute(self, operation, payload):
        if not self.available:
            raise PeerUnavailable("owner offline")
        self.calls.append((operation, payload))
        if operation == "room.post":
            return {
                "created": True,
                "message": {
                    "id": "canonical-1",
                    "room_id": "ao",
                    "sequence": 7,
                    "client_message_id": payload["client_message_id"],
                    "author_principal": "pc1",
                    "author_kind": "human",
                    "body": payload["body"],
                    "thread_id": payload.get("thread_id") or "canonical-1",
                    "reply_to_message_id": payload.get("reply_to_message_id"),
                    "created_at": "2026-08-22T00:00:00+00:00",
                },
            }
        if operation == "room.sync":
            return {
                "messages": [],
                "mentions": [],
                "next_sequence": 7,
                "has_more": False,
                "has_older": False,
                "oldest_sequence": None,
                "active_from_sequence": 1,
                "history_available": False,
            }
        if operation == "room.ack":
            return {"room_id": "ao", "principal": "pc1", "last_sequence": payload["sequence"]}
        if operation == "room.members":
            return {"members": [{"principal": "pc1", "display_name": "PC1"}]}
        raise AssertionError(operation)


def _client(tmp_path, peer):
    service = ClientRoomService(ClientState(tmp_path / "client.db"), peer)
    app = FastAPI()
    app.include_router(build_client_router(service), prefix="/api/plugins/agent-room")
    return TestClient(app)


def test_client_api_proxies_fixed_room_operations_without_local_canonical_storage(tmp_path):
    peer = FakePeer()
    client = _client(tmp_path, peer)

    rooms = client.get("/api/plugins/agent-room/rooms")
    posted = client.post(
        "/api/plugins/agent-room/rooms/ao/post",
        json={"client_message_id": "pc1-1", "body": "Hello from PC1"},
    )
    synced = client.get("/api/plugins/agent-room/rooms/ao/messages?after=0&limit=50")
    acked = client.post("/api/plugins/agent-room/rooms/ao/ack", json={"sequence": 7})
    members = client.get("/api/plugins/agent-room/rooms/ao/members")

    assert rooms.status_code == 200
    assert rooms.json()["rooms"] == [
        {
            "id": "ao",
            "name": "Agent Organization",
            "created_at": "",
            "latest_sequence": 0,
            "client_mode": True,
            "owner_online": True,
        }
    ]
    assert posted.status_code == 200 and posted.json()["result"]["message"]["id"] == "canonical-1"
    assert synced.status_code == 200 and synced.json()["result"]["pending_posts"] == []
    assert acked.status_code == 200 and acked.json()["result"]["last_sequence"] == 7
    assert members.status_code == 200 and members.json()["result"]["members"][0]["principal"] == "pc1"
    assert [operation for operation, _payload in peer.calls] == ["room.post", "room.sync", "room.ack", "room.members"]


def test_offline_post_survives_restart_and_reconciles_exactly_once(tmp_path):
    peer = FakePeer()
    peer.available = False
    client = _client(tmp_path, peer)
    path = "/api/plugins/agent-room/rooms/ao/post"

    offline_payload = {
        "client_message_id": "offline-1",
        "body": "Queued once",
        "thread_id": "thread-offline",
        "reply_to_message_id": "canonical-4",
    }
    queued = client.post(path, json=offline_payload)
    retried = client.post(path, json=offline_payload)
    conflict = client.post(path, json={"client_message_id": "offline-1", "body": "Different"})
    offline_sync = client.get("/api/plugins/agent-room/rooms/ao/messages?after=0&limit=50")
    offline_members = client.get("/api/plugins/agent-room/rooms/ao/members")

    assert queued.status_code == 200 and queued.json()["result"]["pending"] is True
    assert retried.status_code == 200 and retried.json()["result"] == queued.json()["result"]
    assert conflict.status_code == 409
    assert offline_sync.status_code == 200
    assert offline_sync.json()["result"]["owner_online"] is False
    assert [row["client_message_id"] for row in offline_sync.json()["result"]["pending_posts"]] == ["offline-1"]
    assert offline_members.status_code == 200 and offline_members.json()["result"] == {
        "members": [],
        "owner_online": False,
    }

    restarted = ClientRoomService(ClientState(tmp_path / "client.db"), peer)
    assert [row["client_message_id"] for row in restarted.state.pending("ao")] == ["offline-1"]

    peer.available = True
    assert restarted.reconcile_once() == 1
    assert restarted.reconcile_once() == 0
    assert restarted.state.pending("ao") == []
    posts = [payload for operation, payload in peer.calls if operation == "room.post"]
    assert posts == [
        {
            "room_id": "ao",
            "client_message_id": "offline-1",
            "body": "Queued once",
            "thread_id": "thread-offline",
            "reply_to_message_id": "canonical-4",
        }
    ]


def test_offline_ack_survives_restart_and_reconciles_monotonically(tmp_path):
    peer = FakePeer()
    peer.available = False
    client = _client(tmp_path, peer)

    queued = client.post("/api/plugins/agent-room/rooms/ao/ack", json={"sequence": 9})

    assert queued.status_code == 200
    assert queued.json()["result"] == {"room_id": "ao", "principal": "pc1", "last_sequence": 9, "pending": True}
    restarted = ClientRoomService(ClientState(tmp_path / "client.db"), peer)
    assert restarted.state.cursor("ao") == 9

    peer.available = True
    assert restarted.reconcile_once() == 1
    assert restarted.reconcile_once() == 0
    acks = [payload for operation, payload in peer.calls if operation == "room.ack"]
    assert acks == [{"room_id": "ao", "sequence": 9}]


def test_cursor_upsert_remains_monotonic_when_callers_observe_stale_state(tmp_path):
    class StaleReaderState(ClientState):
        def cursor(self, room_id):
            return 0

    state = StaleReaderState(tmp_path / "client.db")

    state.set_cursor("ao", 10)
    state.set_cursor("ao", 9)

    assert ClientState.cursor(state, "ao") == 10


def test_reconcile_keeps_pending_post_until_peer_confirms_matching_canonical_message(tmp_path):
    class MalformedPeer:
        def execute(self, operation, payload):
            return {}

    state = ClientState(tmp_path / "client.db")
    state.queue_post("ao", "pending-1", "Keep me")
    service = ClientRoomService(state, MalformedPeer())

    with pytest.raises(ValueError, match="matching canonical message"):
        service.reconcile_once()

    assert [row["client_message_id"] for row in state.pending("ao")] == ["pending-1"]


def test_post_rejects_canonical_confirmation_that_drops_thread_links(tmp_path):
    class LinkDroppingPeer:
        def execute(self, operation, payload):
            return {
                "created": True,
                "message": {
                    "id": "canonical-8",
                    "room_id": "ao",
                    "sequence": 8,
                    "client_message_id": payload["client_message_id"],
                    "body": payload["body"],
                    "thread_id": "canonical-8",
                    "reply_to_message_id": None,
                },
            }

    service = ClientRoomService(ClientState(tmp_path / "client.db"), LinkDroppingPeer())

    with pytest.raises(ValueError, match="matching canonical message"):
        service.post(
            "ao",
            {
                "client_message_id": "reply-1",
                "body": "Nested reply",
                "thread_id": "thread-7",
                "reply_to_message_id": "canonical-7",
            },
        )


def test_client_post_preserves_thread_and_reply_links(tmp_path):
    peer = FakePeer()
    service = ClientRoomService(ClientState(tmp_path / "client.db"), peer)

    service.post(
        "ao",
        {
            "client_message_id": "reply-1",
            "body": "Nested reply",
            "thread_id": "thread-7",
            "reply_to_message_id": "canonical-7",
        },
    )

    assert peer.calls[0] == (
        "room.post",
        {
            "room_id": "ao",
            "client_message_id": "reply-1",
            "body": "Nested reply",
            "thread_id": "thread-7",
            "reply_to_message_id": "canonical-7",
        },
    )


@pytest.mark.asyncio
async def test_reconciler_keeps_blocking_peer_work_off_the_host_event_loop(tmp_path):
    class SlowService:
        owner_online = True

        def reconcile_once(self):
            time.sleep(0.15)
            return 0

    reconciler = PeerReconciler(SlowService(), interval=60)
    started = time.monotonic()
    await reconciler.start()
    await asyncio.sleep(0.02)
    elapsed = time.monotonic() - started
    await reconciler.stop()

    assert elapsed < 0.08
