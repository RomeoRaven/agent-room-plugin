from __future__ import annotations

import json
import urllib.error

import pytest

from client import A2APeer, PeerRejected


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


def test_a2a_peer_sends_skill_hinted_operation_and_returns_structured_result(tmp_path):
    token = tmp_path / "peer.token"
    token.write_text("directional-secret\n")
    requests = []
    responses = [
        {
            "jsonrpc": "2.0",
            "id": "send",
            "result": {"task": {"id": "task-1", "status": {"state": "TASK_STATE_SUBMITTED"}}},
        },
        {
            "jsonrpc": "2.0",
            "id": "get",
            "result": {
                "id": "task-1",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [
                    {
                        "parts": [
                            {
                                "data": {
                                    "contract_version": "1",
                                    "operation": "room.members",
                                    "result": {"members": [{"principal": "pc1"}]},
                                }
                            }
                        ]
                    }
                ],
            },
        },
    ]

    def open_request(request, timeout):
        requests.append((request, timeout, json.loads(request.data)))
        return Response(responses.pop(0))

    peer = A2APeer("https://room-owner.example/s1/a2a", token, open_request=open_request, poll_interval=0)

    result = peer.execute("room.members", {"room_id": "ao"})

    assert result == {"members": [{"principal": "pc1"}]}
    assert [body["method"] for _request, _timeout, body in requests] == ["SendMessage", "GetTask"]
    send_request, _timeout, send = requests[0]
    assert send_request.headers["Authorization"] == "Bearer directional-secret"
    assert send_request.headers["A2a-version"] == "1.0"
    assert send["params"]["metadata"] == {
        "skillHint": "agent-room-v1",
        "agent_room": {"contract_version": "1", "operation": "room.members", "payload": {"room_id": "ao"}},
    }


def test_a2a_peer_can_attest_an_allowlisted_local_agent_in_room_envelope(tmp_path):
    token = tmp_path / "peer.token"
    token.write_text("directional-secret\n")
    requests = []
    response = {
        "jsonrpc": "2.0",
        "id": "send",
        "result": {
            "task": {
                "id": "task-1",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [
                    {
                        "parts": [
                            {
                                "data": {
                                    "contract_version": "1",
                                    "operation": "room.post",
                                    "result": {"message": {"id": "m"}},
                                }
                            }
                        ]
                    }
                ],
            }
        },
    }

    def open_request(request, timeout):
        requests.append(json.loads(request.data))
        return Response(response)

    peer = A2APeer("https://room-owner.example/s1/a2a", token, open_request=open_request, poll_interval=0)
    peer.execute("room.post", {"room_id": "ao"}, source_principal="pla")

    assert requests[0]["params"]["metadata"]["agent_room"]["source_principal"] == "pla"


def test_a2a_peer_classifies_auth_rejection_as_non_retryable(tmp_path):
    token = tmp_path / "peer.token"
    token.write_text("wrong-secret\n")

    def reject(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    peer = A2APeer("https://room-owner.example/s1/a2a", token, open_request=reject)

    with pytest.raises(PeerRejected, match="HTTP 401"):
        peer.execute("room.members", {"room_id": "ao"})
