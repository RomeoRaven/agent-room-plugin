from __future__ import annotations

import importlib.util
import sys
from types import ModuleType, SimpleNamespace
from pathlib import Path

import pytest


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
        self.surfaces = []

        async def invoke_delegate(_name, _prompt, _key, *, permissions="readonly"):
            return "reply"

        self.host = SimpleNamespace(invoke_delegate=invoke_delegate)

    def register_router(self, router, prefix=None):
        self.routers.append((prefix, router))

    def register_surface(self, start, stop=None, name=None, reload=None):
        self.surfaces.append({"start": start, "stop": stop, "name": name, "reload": reload})


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


def test_register_mounts_local_and_federation_routers_without_a2a_ownership(tmp_path):
    plugin = _load_plugin()
    registry = FakeRegistry(_config(tmp_path))

    plugin.register(registry)

    assert (tmp_path / "agent-room.db").exists()
    assert len(registry.routers) == 2
    assert [prefix for prefix, _router in registry.routers] == [
        "/api/plugins/agent-room",
        "/api/plugins/agent-room",
    ]
    assert {route.path for route in registry.routers[1][1].routes} == {"/v1/execute"}
    assert registry.skills == []
    assert registry.handlers == {}


def test_register_without_peer_keeps_local_api_but_advertises_no_a2a_skill(tmp_path):
    plugin = _load_plugin()
    registry = FakeRegistry(_config(tmp_path, peer=False))

    plugin.register(registry)

    assert len(registry.routers) == 1
    assert registry.skills == []
    assert registry.handlers == {}
    assert registry.surfaces == []


def test_register_client_mode_mounts_proxy_and_reconcile_without_owner_database(tmp_path):
    plugin = _load_plugin()
    config = _config(tmp_path, peer=False)
    config.update(
        {
            "mode": "client",
            "peer_url": "https://room-owner.example/api/plugins/agent-room/v1/execute",
            "peer_token_file": str(tmp_path / "peer.token"),
        }
    )
    (tmp_path / "peer.token").write_text("secret\n")
    registry = FakeRegistry(config)

    plugin.register(registry)

    assert not (tmp_path / "agent-room.db").exists()
    assert (tmp_path / "agent-room-client.db").exists()
    assert len(registry.routers) == 1 and registry.routers[0][0] == "/api/plugins/agent-room"
    assert [surface["name"] for surface in registry.surfaces] == ["peer-reconciliation"]
    assert registry.skills == []
    assert registry.handlers == {}


def test_register_client_roster_target_adds_live_resolver_and_durable_delivery_surface(tmp_path):
    plugin = _load_plugin()
    config = _config(tmp_path, peer=False)
    config.update(
        {
            "mode": "client",
            "peer_url": "https://room-owner.example/api/plugins/agent-room/v1/execute",
            "peer_token_file": str(tmp_path / "peer.token"),
            "dispatch_targets": {
                "pla": {
                    "agent_code": "PLA",
                    "delegate": "pla-room",
                    "resolver": {"command": sys.executable, "args": ["resolver-adapter.py"], "timeout_seconds": 5},
                }
            },
        }
    )
    (tmp_path / "peer.token").write_text("secret\n")
    registry = FakeRegistry(config)

    plugin.register(registry)

    assert [surface["name"] for surface in registry.surfaces] == [
        "peer-reconciliation",
        "client-mention-delivery",
    ]


def test_register_rejects_peer_agent_attestation_for_non_agent_member(tmp_path):
    plugin = _load_plugin()
    config = _config(tmp_path)
    config["peer_agent_principals"] = ["pc1"]
    registry = FakeRegistry(config)

    with pytest.raises(ValueError, match="peer agent principal must be a configured agent member"):
        plugin.register(registry)


def test_register_requires_remote_target_to_be_allowlisted_for_the_configured_peer(tmp_path):
    plugin = _load_plugin()
    config = _config(tmp_path)
    config["members"].append(
        {
            "principal": "pla",
            "kind": "agent",
            "display_name": "protoLabs Agent",
            "role": "member",
            "mention_token": "@PLA",
            "host": "pc1",
            "can_post": True,
            "can_mention": False,
        }
    )
    config["dispatch_targets"] = {"pla": {"remote_peer": "pc1"}}

    with pytest.raises(ValueError, match="remote dispatch target must be allowlisted"):
        plugin.register(FakeRegistry(config))

    config["peer_agent_principals"] = ["pla"]
    registry = FakeRegistry(config)
    plugin.register(registry)
    assert len(registry.routers) == 2


def test_register_rejects_remote_target_owned_by_another_peer(tmp_path):
    plugin = _load_plugin()
    config = _config(tmp_path)
    config["members"].append(
        {
            "principal": "pla",
            "kind": "agent",
            "display_name": "protoLabs Agent",
            "role": "member",
            "mention_token": "@PLA",
            "host": "pc1",
            "can_post": True,
            "can_mention": False,
        }
    )
    config["peer_agent_principals"] = ["pla"]
    config["dispatch_targets"] = {"pla": {"remote_peer": "other-peer"}}

    with pytest.raises(ValueError, match="configured peer"):
        plugin.register(FakeRegistry(config))


def test_register_configured_dispatch_target_adds_one_worker_surface(tmp_path):
    plugin = _load_plugin()
    config = _config(tmp_path, peer=False)
    config["members"].append(
        {
            "principal": "hermes",
            "kind": "agent",
            "display_name": "Hermes",
            "role": "member",
            "mention_token": "@Hermes",
            "host": "s1",
            "can_post": True,
            "can_mention": False,
        }
    )
    config["dispatch_targets"] = {"hermes": {"delegate": "hermes_s1"}}
    registry = FakeRegistry(config)

    plugin.register(registry)

    assert len(registry.surfaces) == 1
    assert registry.surfaces[0]["name"] == "mention-delivery"
    assert callable(registry.surfaces[0]["start"])
    assert callable(registry.surfaces[0]["stop"])


def test_register_accepts_multiple_configured_members_behind_one_worker_surface(tmp_path):
    plugin = _load_plugin()
    config = _config(tmp_path, peer=False)
    config["members"].extend(
        [
            {
                "principal": "hermes",
                "kind": "agent",
                "display_name": "Hermes",
                "role": "member",
                "mention_token": "@Hermes",
                "host": "s1",
                "can_post": True,
                "can_mention": False,
            },
            {
                "principal": "headroom",
                "kind": "agent",
                "display_name": "Headroom",
                "role": "member",
                "mention_token": "@Headroom",
                "host": "s1",
                "can_post": True,
                "can_mention": False,
            },
        ]
    )
    config["dispatch_targets"] = {
        "hermes": {"delegate": "hermes_s1"},
        "headroom": {"delegate": "headroom_s1"},
    }
    registry = FakeRegistry(config)

    plugin.register(registry)

    assert [surface["name"] for surface in registry.surfaces] == ["mention-delivery"]


def test_data_dir_does_not_fallback_when_host_path_resolution_fails(monkeypatch):
    plugin = _load_plugin()
    infra = ModuleType("infra")
    infra.__path__ = []
    paths = ModuleType("infra.paths")

    def broken_paths():
        raise RuntimeError("host path resolution failed")

    paths.instance_paths = broken_paths
    monkeypatch.setitem(sys.modules, "infra", infra)
    monkeypatch.setitem(sys.modules, "infra.paths", paths)
    monkeypatch.delenv("AGENT_ROOM_DIR", raising=False)

    with pytest.raises(RuntimeError, match="host path resolution failed"):
        plugin._data_dir({})
