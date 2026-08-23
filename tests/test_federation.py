from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from federation import build_federation_router
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
PC1 = {
    "principal": "pc1",
    "kind": "host",
    "display_name": "PC1",
    "role": "member",
    "mention_token": "@PC1",
    "host": "pc1",
    "can_post": True,
    "can_mention": True,
}
PLA = {
    "principal": "pla",
    "kind": "agent",
    "display_name": "protoLabs Agent",
    "role": "member",
    "mention_token": "@PLA",
    "host": "pc1",
    "can_post": True,
    "can_mention": False,
}
HERMES = {
    "principal": "hermes",
    "kind": "agent",
    "display_name": "Hermes",
    "role": "member",
    "mention_token": "@Hermes",
    "host": "s1",
    "can_post": True,
    "can_mention": True,
}


def _envelope(operation: str, payload: dict) -> dict:
    return {"contract_version": "1", "operation": operation, "payload": payload}


def _client(tmp_path, *, allowed_agents=frozenset({"pla"}), pla_can_mention=False, include_hermes=False):
    members = [PC1, {**PLA, "can_mention": pla_can_mention}]
    targets = {"pla": {"remote_peer": "pc1"}}
    if include_hermes:
        members.append(HERMES)
        targets["hermes"] = {"delegate": "hermes_s1"}
    store = RoomStore(tmp_path / "agent-room.db", owner=OWNER, members=members)
    operations = RoomOperations(store, dispatch_targets=targets)
    app = FastAPI()

    @app.middleware("http")
    async def test_trust_tier(request: Request, call_next):
        tier = request.headers.get("x-test-trust-tier")
        if tier:
            request.state.trust_tier = tier
        return await call_next(request)

    app.include_router(
        build_federation_router(
            operations,
            local_principal="dennis",
            peer_principal="pc1",
            peer_agent_principals=set(allowed_agents),
        ),
        prefix="/api/plugins/agent-room",
    )
    return TestClient(app), store


def _post(client: TestClient, envelope: dict, tier: str = "federation"):
    return client.post(
        "/api/plugins/agent-room/v1/execute",
        headers={"x-test-trust-tier": tier},
        json=envelope,
    )


def test_federation_route_binds_peer_and_operator_tiers_without_wire_identity(tmp_path):
    client, _store = _client(tmp_path)

    peer = _post(
        client,
        _envelope("room.post", {"room_id": "ao", "client_message_id": "peer-1", "body": "peer post"}),
    )
    operator = _post(
        client,
        _envelope("room.post", {"room_id": "ao", "client_message_id": "local-1", "body": "local post"}),
        tier="operator",
    )

    assert peer.status_code == operator.status_code == 200
    assert peer.json()["result"]["message"]["author_principal"] == "pc1"
    assert operator.json()["result"]["message"]["author_principal"] == "dennis"
    forged = _envelope("room.members", {"room_id": "ao"})
    forged["source_principal"] = "pla"
    rejected = _post(client, forged)
    assert rejected.status_code == 400
    assert "unsupported Agent Room envelope field" in rejected.json()["detail"]
    payload_forgery = _post(
        client,
        _envelope("room.members", {"room_id": "ao", "source_principal": "pla"}),
    )
    assert payload_forgery.status_code == 400
    assert "identity fields are host-bound" in payload_forgery.json()["detail"]


def test_federation_route_derives_remote_agent_author_from_canonical_pending_mention(tmp_path):
    client, store = _client(tmp_path)
    source = _post(
        client,
        _envelope("room.post", {"room_id": "ao", "client_message_id": "source", "body": "@PLA status?"}),
    ).json()["result"]
    mention = source["mentions"][0]
    message = source["message"]

    reply_envelope = _envelope(
        "room.post",
        {
            "room_id": "ao",
            "client_message_id": f"mention-reply:{mention['id']}",
            "body": "PLA reply",
            "thread_id": message["thread_id"],
            "reply_to_message_id": message["id"],
            "completes_mention_id": mention["id"],
        },
    )
    reply = _post(client, reply_envelope)

    assert reply.status_code == 200
    result = reply.json()["result"]
    assert result["message"]["author_principal"] == "pla"
    assert result["message"]["author_kind"] == "agent"
    assert store.mention(mention["id"])["status"] == "completed"

    retry = _post(client, reply_envelope)
    conflict = _post(
        client,
        _envelope(
            "room.post",
            {
                **reply_envelope["payload"],
                "client_message_id": "different-reply-id",
                "body": "second PLA reply",
            },
        ),
    )
    assert retry.status_code == 200 and retry.json()["result"]["created"] is False
    assert conflict.status_code == 409
    messages = store.sync(room_id="ao", after=0, limit=100)["messages"]
    assert [item["body"] for item in messages if item["author_principal"] == "pla"] == ["PLA reply"]


def test_remote_agent_reply_preserves_parent_root_and_hop_for_local_child_mention(tmp_path):
    client, _store = _client(tmp_path, pla_can_mention=True, include_hermes=True)
    source = _post(
        client,
        _envelope("room.post", {"room_id": "ao", "client_message_id": "source", "body": "@PLA hand off"}),
    ).json()["result"]
    parent = source["mentions"][0]
    message = source["message"]

    reply = _post(
        client,
        _envelope(
            "room.post",
            {
                "room_id": "ao",
                "client_message_id": f"mention-reply:{parent['id']}",
                "body": "Handoff @Hermes",
                "thread_id": message["thread_id"],
                "reply_to_message_id": message["id"],
                "completes_mention_id": parent["id"],
            },
        ),
    )

    assert reply.status_code == 200
    child = reply.json()["result"]["mentions"][0]
    assert child["target_principal"] == "hermes"
    assert child["parent_mention_id"] == parent["id"]
    assert child["origin_message_id"] == message["id"]
    assert child["origin_chain"] == ["pla", "hermes"]
    assert child["hop_count"] == 1


def test_federation_route_refuses_unowned_agent_completion_and_missing_trust_tier(tmp_path):
    client, _store = _client(tmp_path, allowed_agents=frozenset())
    source = _post(
        client,
        _envelope("room.post", {"room_id": "ao", "client_message_id": "source", "body": "@PLA status?"}),
    ).json()["result"]
    mention = source["mentions"][0]
    message = source["message"]
    payload = {
        "room_id": "ao",
        "client_message_id": f"mention-reply:{mention['id']}",
        "body": "not allowed",
        "thread_id": message["thread_id"],
        "reply_to_message_id": message["id"],
        "completes_mention_id": mention["id"],
    }

    rejected = _post(client, _envelope("room.post", payload))
    missing_tier = client.post("/api/plugins/agent-room/v1/execute", json=_envelope("room.members", {"room_id": "ao"}))

    assert rejected.status_code == 403
    assert "not authorized for this federation peer" in rejected.json()["detail"]
    assert missing_tier.status_code == 403


@pytest.mark.parametrize(
    "operation",
    ["room.list", "room.create", "room.rename", "room.archive", "room.restore", "room.reset", "room.search"],
)
def test_federation_route_keeps_lifecycle_and_search_local(tmp_path, operation):
    client, _store = _client(tmp_path)
    response = _post(client, _envelope(operation, {"room_id": "ao", "name": "No", "query": "No"}))
    assert response.status_code == 400
    assert "unsupported Agent Room federation operation" in response.json()["detail"]


@pytest.mark.parametrize("field,value", [("before", 3), ("around", 2), ("history", True)])
def test_federation_sync_rejects_local_history_controls(tmp_path, field, value):
    client, _store = _client(tmp_path)
    response = _post(client, _envelope("room.sync", {"room_id": "ao", field: value}))
    assert response.status_code == 400
    assert "unsupported Agent Room federation payload field" in response.json()["detail"]
