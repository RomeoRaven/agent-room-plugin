from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_manifest_is_backend_only_disabled_and_version_synced():
    manifest = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    pyproject = (ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match is not None
    version = match.group(1)

    assert manifest["id"] == "agent-room"
    assert manifest["version"] == version == "0.2.0"
    assert manifest["enabled"] is False
    assert manifest["repository"] == "https://github.com/RomeoRaven/agent-room-plugin"
    assert manifest["min_protoagent_version"] == "0.142.1"
    assert manifest.get("views", []) == []
    assert manifest["capabilities"] == {"network": [], "filesystem": "scoped"}
    assert manifest["config"]["local_principal"] == "operator"
    assert manifest["config"]["dispatch_targets"] == {}
    assert manifest["config"]["peer_principal"] == ""
    assert manifest["config"]["members"] == []


def test_repo_has_no_duplicate_frontend_or_runtime_dependencies():
    assert not (ROOT / "frontend").exists()
    assert not (ROOT / "static").exists()
    assert "dependencies =" not in (ROOT / "pyproject.toml").read_text()
