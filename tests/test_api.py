from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import build_router
from operations import RoomOperations
from store import RoomStore


def _client(tmp_path):
    app = FastAPI()
    operations = RoomOperations(
        RoomStore(
            tmp_path / "agent-room.db",
            owner={
                "principal": "dennis",
                "kind": "human",
                "display_name": "Dennis",
                "role": "owner",
                "mention_token": "@Dennis",
                "host": "operator",
                "can_post": True,
                "can_mention": True,
            },
        )
    )
    app.include_router(build_router(operations, local_principal="dennis"), prefix="/api/plugins/agent-room")
    return TestClient(app)


def test_gated_local_api_uses_bound_principal_for_same_operation_contract(tmp_path):
    client = _client(tmp_path)

    posted = client.post(
        "/api/plugins/agent-room/rooms/ao/post",
        json={"client_message_id": "local-1", "body": "Hello API"},
    )
    synced = client.get("/api/plugins/agent-room/rooms/ao/messages?after=0&limit=50")
    acked = client.post("/api/plugins/agent-room/rooms/ao/ack", json={"sequence": 1})
    members = client.get("/api/plugins/agent-room/rooms/ao/members")

    assert posted.status_code == 200 and posted.json()["result"]["message"]["author_principal"] == "dennis"
    assert synced.status_code == 200 and synced.json()["result"]["messages"] == [posted.json()["result"]["message"]]
    assert acked.status_code == 200 and acked.json()["result"]["last_sequence"] == 1
    assert members.status_code == 200 and members.json()["result"]["members"][0]["principal"] == "dennis"


def test_local_api_rejects_payload_identity_and_conflicting_retry(tmp_path):
    client = _client(tmp_path)
    path = "/api/plugins/agent-room/rooms/ao/post"

    assert client.post(path, json={"client_message_id": "x", "body": "No", "principal": "intruder"}).status_code == 400
    assert client.post(path, json={"client_message_id": "same", "body": "One"}).status_code == 200
    conflict = client.post(path, json={"client_message_id": "same", "body": "Two"})
    assert conflict.status_code == 409
    assert "different content" in conflict.json()["detail"]


def test_local_api_room_listing_requires_bound_membership(tmp_path):
    app = FastAPI()
    operations = RoomOperations(RoomStore(tmp_path / "agent-room.db"))
    app.include_router(build_router(operations, local_principal="intruder"), prefix="/api/plugins/agent-room")

    response = TestClient(app).get("/api/plugins/agent-room/rooms")

    assert response.status_code == 403
    assert "not a room member" in response.json()["detail"]


def test_local_api_exposes_room_lifecycle_bounded_history_and_search(tmp_path):
    client = _client(tmp_path)

    created = client.post("/api/plugins/agent-room/rooms", json={"name": "Subject room"})
    assert created.status_code == 200
    room_id = created.json()["result"]["room"]["id"]
    assert client.patch(f"/api/plugins/agent-room/rooms/{room_id}", json={"name": "Renamed subject"}).status_code == 200
    assert (
        client.post(
            f"/api/plugins/agent-room/rooms/{room_id}/post",
            json={"client_message_id": "searchable", "body": "searchable history"},
        ).status_code
        == 200
    )
    assert client.post(f"/api/plugins/agent-room/rooms/{room_id}/reset").status_code == 200

    current = client.get(f"/api/plugins/agent-room/rooms/{room_id}/messages?limit=20")
    history = client.get(f"/api/plugins/agent-room/rooms/{room_id}/messages?limit=20&history=true")
    searched = client.get(
        "/api/plugins/agent-room/search",
        params={"q": "searchable", "scope": "current", "room_id": room_id, "history": "true"},
    )
    assert current.status_code == 200 and current.json()["result"]["messages"] == []
    assert history.status_code == 200 and len(history.json()["result"]["messages"]) == 1
    assert searched.status_code == 200 and searched.json()["result"]["results"][0]["room_name"] == "Renamed subject"

    assert client.post(f"/api/plugins/agent-room/rooms/{room_id}/archive").status_code == 200
    archived = client.get("/api/plugins/agent-room/rooms?status=archived")
    assert [room["id"] for room in archived.json()["rooms"]] == [room_id]
    assert client.post(f"/api/plugins/agent-room/rooms/{room_id}/restore").status_code == 200
