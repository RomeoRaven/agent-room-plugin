from __future__ import annotations

import hashlib
import json

from client import ClientRoomService, ClientState


def _canonical():
    message = {
        "id": "message-1",
        "room_id": "ao",
        "sequence": 21,
        "thread_id": "thread-1",
        "author_kind": "host",
    }
    mention = {
        "id": "mention-1",
        "room_id": "ao",
        "source_message_id": "message-1",
        "target_principal": "pla",
        "status": "pending",
    }
    return message, mention


def test_client_state_claims_each_canonical_local_mention_once_with_stable_dispatch_id(tmp_path):
    state = ClientState(tmp_path / "client.db")
    message, mention = _canonical()

    assert state.import_local_mentions([mention], [message], {"pla"}) == 1
    assert state.import_local_mentions([mention], [message], {"pla"}) == 0

    work = state.claim_local_mention()
    expected = hashlib.sha256(
        json.dumps(["ao", "thread-1", "message-1", "pla"], separators=(",", ":")).encode()
    ).hexdigest()
    assert work["dispatch_id"] == expected
    assert work["mention_id"] == "mention-1"
    assert work["status"] == "invoking"
    assert state.claim_local_mention() is None


def test_client_state_never_reinvokes_an_interrupted_mention(tmp_path):
    state = ClientState(tmp_path / "client.db")
    message, mention = _canonical()
    state.import_local_mentions([mention], [message], {"pla"})
    state.claim_local_mention()

    assert state.mark_interrupted_local_mentions() == 1
    work = state.claim_local_mention()

    assert work["status"] == "reply_ready"
    assert "restart interrupted" in work["reply_body"]
    state.complete_local_mention(work["dispatch_id"], "reply-1")
    assert state.claim_local_mention() is None


def test_client_service_background_poll_imports_remote_mentions_once_and_advances_separate_delivery_cursor(tmp_path):
    state = ClientState(tmp_path / "client.db")
    message, mention = _canonical()

    class Peer:
        def __init__(self):
            self.afters = []

        def execute(self, operation, payload, *, source_principal=None):
            assert operation == "room.sync" and source_principal is None
            self.afters.append(payload["after"])
            if payload["after"] < 21:
                return {"messages": [message], "mentions": [mention], "next_sequence": 21, "has_more": False}
            return {"messages": [], "mentions": [], "next_sequence": 21, "has_more": False}

    peer = Peer()
    service = ClientRoomService(state, peer, local_mention_targets={"pla"})

    assert service.poll_mentions() == 1
    assert service.poll_mentions() == 0
    assert peer.afters == [0, 21]
    assert state.claim_local_mention()["mention_id"] == "mention-1"
