from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_manifest_declares_self_contained_rooms_view_and_version_is_synced():
    manifest = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    pyproject = (ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match is not None
    version = match.group(1)

    assert manifest["id"] == "agent-room"
    assert manifest["version"] == version == "1.0.0"
    assert manifest["enabled"] is False
    assert manifest["repository"] == "https://github.com/RomeoRaven/agent-room-plugin"
    assert manifest["min_protoagent_version"] == "0.146.0"
    assert manifest["views"] == [
        {
            "id": "rooms",
            "label": "Rooms",
            "icon": "MessagesSquare",
            "placement": "rail",
            "path": "/plugins/agent-room/view",
        }
    ]
    assert manifest["public_paths"] == ["/plugins/agent-room/"]
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


def test_repo_packages_self_contained_web_assets_without_runtime_dependencies():
    assert not (ROOT / "frontend").exists()
    assert (ROOT / "web" / "room.html").is_file()
    assert (ROOT / "web" / "room.css").is_file()
    assert (ROOT / "web" / "room.js").is_file()
    assert "dependencies =" not in (ROOT / "pyproject.toml").read_text()
    assert 'include = ["web/*"]' in (ROOT / "pyproject.toml").read_text()


def test_active_plugin_source_has_no_agent_room_a2a_transport():
    assert not (ROOT / "transport.py").exists()
    source = "\n".join(path.read_text() for path in ROOT.glob("*.py") if path.name not in {"test_a2a.py"})
    for retired in ("A2APeer", "register_a2a_handler", "register_a2a_skill", "skillHint", "GetTask"):
        assert retired not in source


def test_readme_documents_the_complete_optional_member_profile_and_bounds():
    readme = (ROOT / "README.md").read_text()

    assert "profile:" in readme
    for field in ("summary:", "capabilities:", "best_for:", "boundaries:", "fallback:"):
        assert field in readme
    assert "1,000 characters" in readme
    assert "20 items" in readme
    assert "200 characters" in readme
