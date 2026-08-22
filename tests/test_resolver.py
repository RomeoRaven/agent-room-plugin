from __future__ import annotations

import hashlib
import json
import sys

import pytest

from resolver import ResolverError, RosterResolver


VALID_AGENT = {
    "name": "pla",
    "display_name": "protoLabs Agent",
    "code": "PLA",
    "type": "scoped-agent",
    "status": "active",
    "owner_surface": r"C:\agent-hq\protolabs-platform",
    "start_here": r"C:\agent-hq\protolabs-platform\_steward\START_HERE.md",
    "source_of_truth": "agents/scoped/pla.md",
    "record_path": r"C:\agent-hq\agents\scoped\pla.md",
    "record_sha256": "a" * 64,
    "startup_context": "===== guard =====\nguard\n\n===== agents =====\nagents\n\n===== start =====\nstart",
    "startup_sources": [
        {"path": r"C:\guard.md", "sha256": "b" * 64},
        {"path": r"C:\agent-hq\AGENTS.md", "sha256": "c" * 64},
        {"path": r"C:\agent-hq\protolabs-platform\_steward\START_HERE.md", "sha256": "d" * 64},
    ],
    "startup_context_sha256": "e" * 64,
}
VALID_AGENT["startup_context_sha256"] = hashlib.sha256(VALID_AGENT["startup_context"].encode()).hexdigest()


def _helper(tmp_path, body: str):
    path = tmp_path / "helper.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_resolver_sends_exact_json_over_stdin_with_fixed_argv_and_allowlisted_env(tmp_path, monkeypatch):
    helper = _helper(
        tmp_path,
        f"""import json,os,sys
request=json.load(sys.stdin)
assert request == {{"agent":"PLA","mode":"mention-code"}}
assert "PARENT_SECRET_SENTINEL" not in os.environ
agent=json.loads({json.dumps(json.dumps(VALID_AGENT))}); agent["code"]=request["agent"]
print(json.dumps({{"status":"PASS","agent":agent}}))
""",
    )
    monkeypatch.setenv("PARENT_SECRET_SENTINEL", "must-not-leak")
    resolver = RosterResolver(sys.executable, [str(helper)], timeout=2)

    result = resolver.resolve("PLA")

    assert result == VALID_AGENT
    assert resolver.argv == [sys.executable, str(helper)]
    assert "PLA" not in resolver.argv


def test_resolver_kills_and_fails_cleanly_on_timeout(tmp_path):
    helper = _helper(tmp_path, "import time; time.sleep(10)\n")
    resolver = RosterResolver(sys.executable, [str(helper)], timeout=0.1)

    with pytest.raises(ResolverError, match="timed out"):
        resolver.resolve("PLA")


def test_resolver_kills_and_fails_cleanly_on_aggregate_output_overflow(tmp_path):
    helper = _helper(tmp_path, "import sys; sys.stdout.write('x'*80); sys.stderr.write('y'*80)\n")
    resolver = RosterResolver(sys.executable, [str(helper)], timeout=2, max_output_bytes=100)

    with pytest.raises(ResolverError, match="output exceeded"):
        resolver.resolve("PLA")
