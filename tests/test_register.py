from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "agent_room", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_room"] = module
    spec.loader.exec_module(module)
    return module


class FakeRegistry:
    def __init__(self, config):
        self.config = config
        self.routers = []
        self.skills = []
        self.handlers = {}

    def register_router(self, router, prefix=None):
        self.routers.append((prefix, router))

    def register_a2a_skill(self, spec):
        self.skills.append(spec)

    def register_a2a_handler(self, skill_id, handler):
        self.handlers[skill_id] = handler


def _config(tmp_path, *, peer=True):
    return {
        "data_dir": str(tmp_path),
        "local_principal": "dennis",
        "owner": {
            "principal": "dennis",
            "kind": "human",
            "display_name": "Dennis",
            "role": "owner",
            "mention_token": "@Dennis",
            "host": "operator",
            "can_post": True,
            "can_mention": True,
        },
        "peer_principal": "pc1" if peer else "",
        "members": [
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
    }


def test_register_mounts_gated_api_and_configured_a2a_handler(tmp_path):
    plugin = _load_plugin()
    registry = FakeRegistry(_config(tmp_path))

    plugin.register(registry)

    assert (tmp_path / "agent-room.db").exists()
    assert len(registry.routers) == 1 and registry.routers[0][0] == "/api/plugins/agent-room"
    assert [skill["id"] for skill in registry.skills] == ["agent-room-v1"]
    assert set(registry.handlers) == {"agent-room-v1"}


def test_register_without_peer_keeps_local_api_but_advertises_no_a2a_skill(tmp_path):
    plugin = _load_plugin()
    registry = FakeRegistry(_config(tmp_path, peer=False))

    plugin.register(registry)

    assert len(registry.routers) == 1
    assert registry.skills == []
    assert registry.handlers == {}
