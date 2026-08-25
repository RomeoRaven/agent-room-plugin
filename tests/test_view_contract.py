from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "room.html").read_text()
JS = (ROOT / "web" / "room.js").read_text()
CSS = (ROOT / "web" / "room.css").read_text()


def test_slug_base_precedes_every_plugin_asset_and_data_uses_only_plugin_kit():
    base = HTML.index('location.pathname.split("/plugins/")[0]')
    assert base < HTML.index("/_ds/plugin-kit.css")
    assert base < HTML.index("/plugins/agent-room/assets/room.css")
    assert base < HTML.index("/_ds/plugin-kit.js")
    assert base < HTML.index("/plugins/agent-room/assets/room.js")
    assert "kit.apiFetch(" in JS
    assert "fetch(" not in JS.replace("apiFetch(", "")
    assert 'const root = "/api/plugins/agent-room"' in JS


def test_view_contract_contains_complete_room_and_history_controls():
    for label in (
        "New room",
        "Rename room",
        "Archive room",
        "Restore room",
        "Start fresh",
        "Active rooms",
        "Archived rooms",
        "Show earlier history",
        "Return to latest",
        "Search rooms",
        "Current room",
        "All active rooms",
        "Include earlier history",
    ):
        assert label in JS
    assert 'role="dialog"' in JS
    assert 'aria-modal="true"' in JS
    assert "window.confirm" in JS


def test_view_contract_contains_messages_delivery_composer_and_mentions():
    for text in (
        "Room message",
        "Post message",
        "Post to room only — no agents notified",
        "Unknown agent",
        "Mention an agent",
        "Mention delivery",
        "Pending — will send when the Room owner reconnects",
    ):
        assert text in JS
    for key in ("ArrowDown", "ArrowUp", "Escape", "Enter", "Tab"):
        assert f'"{key}"' in JS
    assert "crypto.randomUUID" in JS
    assert "await api.ack" in JS


def test_view_contract_contains_roster_profiles_and_exact_wake_copy():
    for text in (
        "Wakeable agents",
        "Other members",
        "Profile",
        "Purpose",
        "Capabilities",
        "Best for",
        "Boundaries",
        "Fallback",
        "Host",
        "Policy",
        "Wake as ${escapeHtml(member.mention_token)}",
        "Room archived — wake-up unavailable",
        "Not configured for wake-up",
    ):
        assert text in JS
    assert "insertExactMention" in JS
    assert "composer.focus()" in JS


def test_view_is_responsive_keyboard_visible_and_overflow_safe():
    assert "@media (max-width: 720px)" in CSS
    assert "@media (prefers-reduced-motion: reduce)" in CSS
    assert "overflow-x: hidden" in CSS
    assert "min-width: 0" in CSS
    assert ":focus-visible" in CSS
    assert "44px" in CSS
    assert "--pl-color-bg-elevated" not in CSS
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in CSS
