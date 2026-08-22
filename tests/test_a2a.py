from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.protobuf.json_format import MessageToDict

from transport import MIME_TYPE, build_handler
from operations import RoomOperations
from store import RoomStore


def _handler(tmp_path):
    store = RoomStore(
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
            }
        ],
    )
    return build_handler(RoomOperations(store), peer_principal="pc1")


@pytest.mark.asyncio
async def test_a2a_handler_binds_peer_and_returns_structured_contract(tmp_path):
    handler = _handler(tmp_path)
    context = SimpleNamespace(
        metadata={
            "agent_room": {
                "contract_version": "1",
                "operation": "room.post",
                "payload": {"room_id": "ao", "client_message_id": "pc1-1", "body": "Hello over A2A"},
            }
        }
    )

    parts = await handler(context)
    wire = MessageToDict(parts[0])

    assert len(parts) == 1
    assert wire["metadata"]["mimeType"] == MIME_TYPE
    assert wire["data"]["contract_version"] == "1"
    assert wire["data"]["operation"] == "room.post"
    assert wire["data"]["result"]["message"]["author_principal"] == "pc1"


@pytest.mark.asyncio
async def test_a2a_handler_accepts_allowlisted_host_attested_agent_reply_and_completes_remote_mention(tmp_path):
    store = RoomStore(
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
                "can_mention": True,
            },
            {
                "principal": "pla",
                "kind": "agent",
                "display_name": "protoLabs Agent",
                "role": "member",
                "mention_token": "@PLA",
                "host": "pc1",
                "can_post": True,
                "can_mention": False,
            },
        ],
    )
    operations = RoomOperations(store, dispatch_targets={"pla": {"remote_peer": "pc1"}})
    source = operations.execute(
        "room.post", {"room_id": "ao", "client_message_id": "source", "body": "@PLA status?"}, principal="pc1"
    )["result"]
    mention = source["mentions"][0]
    message = source["message"]
    handler = build_handler(operations, peer_principal="pc1", peer_agent_principals={"pla"})
    context = SimpleNamespace(
        metadata={
            "agent_room": {
                "contract_version": "1",
                "operation": "room.post",
                "source_principal": "pla",
                "payload": {
                    "room_id": "ao",
                    "client_message_id": f"mention-reply:{mention['id']}",
                    "body": "PLA reply",
                    "thread_id": message["thread_id"],
                    "reply_to_message_id": message["id"],
                    "completes_mention_id": mention["id"],
                },
            }
        }
    )

    parts = await handler(context)
    result = MessageToDict(parts[0])["data"]["result"]

    assert result["message"]["author_principal"] == "pla"
    assert result["message"]["author_kind"] == "agent"
    assert store.mention(mention["id"])["status"] == "completed"
    assert store.mention(mention["id"])["reply_message_id"] == result["message"]["id"]

    forged = SimpleNamespace(metadata={"agent_room": {**context.metadata["agent_room"], "source_principal": "hermes"}})
    with pytest.raises(PermissionError, match="not authorized for peer"):
        await handler(forged)


@pytest.mark.asyncio
async def test_a2a_handler_rejects_missing_or_wrong_contract_and_wire_identity(tmp_path):
    handler = _handler(tmp_path)

    with pytest.raises(ValueError, match="agent_room metadata is required"):
        await handler(SimpleNamespace(metadata={}))
    with pytest.raises(ValueError, match="unsupported agent-room contract version"):
        await handler(
            SimpleNamespace(
                metadata={
                    "agent_room": {"contract_version": "2", "operation": "room.members", "payload": {"room_id": "ao"}}
                }
            )
        )
    with pytest.raises(ValueError, match="identity fields are host-bound"):
        await handler(
            SimpleNamespace(
                metadata={
                    "agent_room": {
                        "contract_version": "1",
                        "operation": "room.post",
                        "payload": {
                            "room_id": "ao",
                            "client_message_id": "x",
                            "body": "No",
                            "principal": "dennis",
                        },
                    }
                }
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    ["room.list", "room.create", "room.rename", "room.archive", "room.restore", "room.reset", "room.search"],
)
async def test_a2a_handler_keeps_local_lifecycle_and_search_off_transport(tmp_path, operation):
    handler = _handler(tmp_path)

    with pytest.raises(ValueError, match="unsupported agent-room transport operation"):
        await handler(
            SimpleNamespace(
                metadata={
                    "agent_room": {
                        "contract_version": "1",
                        "operation": operation,
                        "payload": {"room_id": "ao", "name": "No", "query": "No"},
                    }
                }
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("before", 3), ("around", 2), ("history", True)])
async def test_a2a_sync_rejects_local_history_controls(tmp_path, field, value):
    handler = _handler(tmp_path)

    with pytest.raises(ValueError, match="unsupported agent-room transport payload field"):
        await handler(
            SimpleNamespace(
                metadata={
                    "agent_room": {
                        "contract_version": "1",
                        "operation": "room.sync",
                        "payload": {"room_id": "ao", field: value},
                    }
                }
            )
        )


@pytest.mark.asyncio
async def test_a2a_sync_without_after_preserves_forward_from_zero_behavior(tmp_path):
    store = RoomStore(
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
            }
        ],
    )
    operations = RoomOperations(store)
    for index in range(1, 4):
        operations.execute(
            "room.post",
            {"room_id": "ao", "client_message_id": f"m-{index}", "body": f"message {index}"},
            principal="pc1",
        )
    handler = build_handler(operations, peer_principal="pc1")
    parts = await handler(
        SimpleNamespace(
            metadata={
                "agent_room": {
                    "contract_version": "1",
                    "operation": "room.sync",
                    "payload": {"room_id": "ao", "limit": 2},
                }
            }
        )
    )
    wire = MessageToDict(parts[0])

    assert [message["sequence"] for message in wire["data"]["result"]["messages"]] == [1, 2]
