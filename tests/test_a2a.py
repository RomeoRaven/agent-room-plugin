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
