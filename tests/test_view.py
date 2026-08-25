from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "agent_room_view_test", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_public_view_router_serves_only_accessible_chrome_and_assets():
    plugin = _load_plugin()
    router = plugin.build_view_router()
    app = FastAPI()
    app.include_router(router, prefix="/plugins/agent-room")
    client = TestClient(app)

    page = client.get("/plugins/agent-room/view")
    css = client.get("/plugins/agent-room/assets/room.css")
    script = client.get("/plugins/agent-room/assets/room.js")

    assert page.status_code == css.status_code == script.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "max-age=31536000" in css.headers["cache-control"]
    assert "max-age=31536000" in script.headers["cache-control"]
    assert 'location.pathname.split("/plugins/")[0]' in page.text
    assert "/_ds/plugin-kit.css" in page.text
    assert 'aria-live="polite"' in page.text
    assert 'role="alert"' in page.text
    assert client.get("/plugins/agent-room/rooms").status_code == 404
