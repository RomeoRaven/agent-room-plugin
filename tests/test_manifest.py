from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_manifest_is_backend_and_peer_client_disabled_and_version_synced():
    manifest = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    pyproject = (ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match is not None
    version = match.group(1)

    assert manifest["id"] == "agent-room"
    assert manifest["version"] == version == "0.8.1"
    assert manifest["enabled"] is False
    assert manifest["repository"] == "https://github.com/RomeoRaven/agent-room-plugin"
    assert manifest["min_protoagent_version"] == "0.146.0"
    assert manifest.get("views", []) == []
    assert manifest["capabilities"] == {"network": ["configured-peer-https"], "filesystem": "scoped"}
    assert manifest["federation_paths"] == ["/api/plugins/agent-room/v1/"]
    assert manifest["config"]["mode"] == "owner"
    assert manifest["config"]["local_principal"] == "operator"
    assert manifest["config"]["dispatch_targets"] == {}
    assert manifest["config"]["mention_policy"] == {
        "max_agent_hops": 1,
        "max_mentions_per_target": 5,
        "rate_window_seconds": 60,
    }
    assert manifest["config"]["peer_principal"] == ""
    assert manifest["config"]["members"] == []


def test_repo_has_no_duplicate_frontend_or_runtime_dependencies():
    assert not (ROOT / "frontend").exists()
    assert not (ROOT / "static").exists()
    assert "dependencies =" not in (ROOT / "pyproject.toml").read_text()


def test_active_plugin_source_has_no_agent_room_a2a_transport():
    assert not (ROOT / "transport.py").exists()
    source = "\n".join(path.read_text() for path in ROOT.glob("*.py") if path.name not in {"test_a2a.py"})
    for retired in ("A2APeer", "register_a2a_handler", "register_a2a_skill", "skillHint", "GetTask"):
        assert retired not in source
