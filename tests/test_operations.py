from __future__ import annotations

import pytest

from operations import RoomOperations
from store import RoomStore


def _service(tmp_path):
    store = RoomStore(
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
    return RoomOperations(store)


def test_versioned_operations_share_one_bound_principal_contract(tmp_path):
    operations = _service(tmp_path)

    posted = operations.execute(
        "room.post",
        {"room_id": "ao", "client_message_id": "pc1-1", "body": "Hello"},
        principal="pc1",
    )
    synced = operations.execute("room.sync", {"room_id": "ao", "after": 0, "limit": 50}, principal="pc1")
    acked = operations.execute("room.ack", {"room_id": "ao", "sequence": 1}, principal="pc1")
    members = operations.execute("room.members", {"room_id": "ao"}, principal="pc1")

    assert posted["contract_version"] == "1" and posted["operation"] == "room.post"
    assert posted["result"]["message"]["author_principal"] == "pc1"
    assert synced["result"]["messages"] == [posted["result"]["message"]]
    assert acked["result"]["last_sequence"] == 1
    assert {m["principal"] for m in members["result"]["members"]} == {"dennis", "pc1"}


def test_members_identify_only_configured_dispatch_targets_as_mentionable(tmp_path):
    store = _service(tmp_path).store
    operations = RoomOperations(store, dispatch_targets={"pc1": {"delegate": "pc1_local"}})

    result = operations.execute("room.members", {"room_id": "ao"}, principal="dennis")
    members = {member["principal"]: member for member in result["result"]["members"]}

    assert members["pc1"]["mentionable"] is True
    assert members["dennis"]["mentionable"] is False


def test_operations_reject_wire_identity_and_nonmembers(tmp_path):
    operations = _service(tmp_path)

    with pytest.raises(ValueError, match="identity fields are host-bound"):
        operations.execute(
            "room.post",
            {"room_id": "ao", "client_message_id": "x", "body": "No", "principal": "dennis"},
            principal="pc1",
        )
    with pytest.raises(PermissionError, match="not a room member"):
        operations.execute("room.members", {"room_id": "ao"}, principal="intruder")
    with pytest.raises(ValueError, match="unsupported room operation"):
        operations.execute("room.delete", {"room_id": "ao"}, principal="pc1")


def test_operations_bound_message_and_identifier_sizes(tmp_path):
    operations = _service(tmp_path)

    with pytest.raises(ValueError, match="body exceeds 20000 characters"):
        operations.execute(
            "room.post",
            {"room_id": "ao", "client_message_id": "x", "body": "x" * 20001},
            principal="pc1",
        )
    with pytest.raises(ValueError, match="client_message_id exceeds 200 characters"):
        operations.execute(
            "room.post",
            {"room_id": "ao", "client_message_id": "x" * 201, "body": "ok"},
            principal="pc1",
        )
    with pytest.raises(ValueError, match="thread_id exceeds 200 characters"):
        operations.execute(
            "room.post",
            {"room_id": "ao", "client_message_id": "x", "body": "ok", "thread_id": "t" * 201},
            principal="pc1",
        )
