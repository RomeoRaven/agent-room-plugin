from __future__ import annotations

import inspect
import json
import urllib.error
import urllib.request

import pytest

from client import FederationPeer, PeerRejected, PeerUnavailable, _RejectRedirects


class Response:
    def __init__(self, payload=None, *, raw: bytes | None = None):
        self.raw = raw if raw is not None else json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, size=-1):
        return self.raw if size is None or size < 0 else self.raw[:size]


def test_federation_peer_posts_exact_envelope_once_and_returns_matching_result(tmp_path):
    peer_file = tmp_path / "peer.value"
    peer_file.write_text("directional-value\n")
    requests = []

    def open_request(request, timeout):
        requests.append((request, timeout, json.loads(request.data)))
        return Response(
            {
                "contract_version": "1",
                "operation": "room.members",
                "result": {"members": [{"principal": "pc1"}]},
            }
        )

    peer = FederationPeer(
        "https://room-owner.example/api/plugins/agent-room/v1/execute",
        peer_file,
        open_request=open_request,
    )
    result = peer.execute("room.members", {"room_id": "ao"})

    assert result == {"members": [{"principal": "pc1"}]}
    assert len(requests) == 1
    request, timeout, body = requests[0]
    assert timeout == 30
    assert request.full_url == "https://room-owner.example/api/plugins/agent-room/v1/execute"
    assert request.headers["Authorization"] == "Bearer directional-value"
    assert request.headers["Content-type"] == "application/json"
    assert "A2a-version" not in request.headers
    assert body == {
        "contract_version": "1",
        "operation": "room.members",
        "payload": {"room_id": "ao"},
    }
    assert "source_principal" not in inspect.signature(peer.execute).parameters


def test_federation_peer_rejects_noncanonical_endpoint_and_mismatched_response(tmp_path):
    peer_file = tmp_path / "peer.value"
    peer_file.write_text("test-value\n")
    with pytest.raises(ValueError, match="federation endpoint"):
        FederationPeer("https://room-owner.example/a2a", peer_file)

    peer = FederationPeer(
        "https://room-owner.example/api/plugins/agent-room/v1/execute",
        peer_file,
        open_request=lambda _request, timeout: Response(
            {"contract_version": "1", "operation": "room.sync", "result": {}}
        ),
    )
    with pytest.raises(ValueError, match="mismatched Agent Room envelope"):
        peer.execute("room.members", {"room_id": "ao"})


def test_federation_peer_bounds_response_and_rejects_nonobject_json(tmp_path):
    peer_file = tmp_path / "peer.value"
    peer_file.write_text("test-value\n")
    peer = FederationPeer(
        "https://room-owner.example/api/plugins/agent-room/v1/execute",
        peer_file,
        open_request=lambda _request, timeout: Response(raw=b"x" * 256),
        max_response_bytes=128,
    )
    with pytest.raises(PeerUnavailable, match="response exceeded"):
        peer.execute("room.members", {"room_id": "ao"})

    malformed = FederationPeer(
        "https://room-owner.example/api/plugins/agent-room/v1/execute",
        peer_file,
        open_request=lambda _request, timeout: Response(raw=b"[]"),
    )
    with pytest.raises(ValueError, match="response must be an object"):
        malformed.execute("room.members", {"room_id": "ao"})


def test_federation_peer_classifies_auth_rejection_as_nonretryable(tmp_path):
    peer_file = tmp_path / "peer.value"
    peer_file.write_text("wrong-value\n")

    def reject(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    peer = FederationPeer(
        "https://room-owner.example/api/plugins/agent-room/v1/execute",
        peer_file,
        open_request=reject,
    )
    with pytest.raises(PeerRejected, match="HTTP 401"):
        peer.execute("room.members", {"room_id": "ao"})


def test_federation_peer_default_opener_rejects_redirect_before_a_second_request(tmp_path, monkeypatch):
    peer_file = tmp_path / "peer.value"
    peer_file.write_text("test-value\n")
    installed_handlers = []

    class FakeOpener:
        def open(self, request, timeout):
            raise AssertionError("no network request is part of constructor proof")

    def build_opener(*handlers):
        installed_handlers.extend(handlers)
        return FakeOpener()

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    FederationPeer("https://room-owner.example/api/plugins/agent-room/v1/execute", peer_file)

    handler = next(item for item in installed_handlers if isinstance(item, _RejectRedirects))
    request = urllib.request.Request(
        "https://room-owner.example/api/plugins/agent-room/v1/execute",
        headers={"Authorization": "Bearer test-value"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        handler.redirect_request(request, None, 302, "Found", {}, "http://redirect.example/other")
    assert exc.value.code == 302
