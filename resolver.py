"""Bounded stdin/JSON bridge to a host-owned live roster resolver."""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time

_AGENT_CODE = re.compile(r"^[A-Z][A-Z0-9]{1,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AGENT_FIELDS = {
    "name",
    "display_name",
    "code",
    "type",
    "status",
    "owner_surface",
    "start_here",
    "source_of_truth",
    "record_path",
    "record_sha256",
}


class ResolverError(RuntimeError):
    pass


class RosterResolver:
    def __init__(
        self,
        command: str,
        args: list[str] | tuple[str, ...],
        *,
        timeout: float = 5,
        max_output_bytes: int = 65536,
        env: dict[str, str] | None = None,
    ) -> None:
        self.argv = [str(command), *[str(value) for value in args]]
        if not self.argv[0]:
            raise ValueError("resolver command is required")
        self.timeout = max(0.05, float(timeout))
        self.max_output_bytes = max(64, int(max_output_bytes))
        inherited = {
            key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if key in os.environ
        }
        self.env = {**inherited, "PYTHONUTF8": "1", **{str(k): str(v) for k, v in (env or {}).items()}}

    @staticmethod
    def _stop_process(process: subprocess.Popen) -> None:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _run(self, request: bytes) -> tuple[int, bytes, bytes]:
        try:
            process = subprocess.Popen(
                self.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env=self.env,
            )
        except OSError as exc:
            raise ResolverError("roster resolver could not start") from exc
        assert process.stdin and process.stdout and process.stderr
        process_stdin = process.stdin
        process_stdout = process.stdout
        process_stderr = process.stderr
        events: queue.Queue[tuple[str, bytes | None]] = queue.Queue(maxsize=16)
        stopped = threading.Event()

        def feed() -> None:
            try:
                process_stdin.write(request)
                process_stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    process_stdin.close()
                except OSError:
                    pass

        def drain(name: str, stream) -> None:
            try:
                while not stopped.is_set():
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    while not stopped.is_set():
                        try:
                            events.put((name, chunk), timeout=0.05)
                            break
                        except queue.Full:
                            continue
            finally:
                while not stopped.is_set():
                    try:
                        events.put((name, None), timeout=0.05)
                        break
                    except queue.Full:
                        continue

        threads = [
            threading.Thread(target=feed, daemon=True),
            threading.Thread(target=drain, args=("stdout", process_stdout), daemon=True),
            threading.Thread(target=drain, args=("stderr", process_stderr), daemon=True),
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + self.timeout
        eof: set[str] = set()
        total = 0
        failure: str | None = None
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            while len(eof) < 2:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = f"roster resolver timed out after {self.timeout:g} seconds"
                    break
                try:
                    name, chunk = events.get(timeout=min(remaining, 0.1))
                except queue.Empty:
                    continue
                if chunk is None:
                    eof.add(name)
                    continue
                total += len(chunk)
                if total > self.max_output_bytes:
                    failure = f"roster resolver output exceeded {self.max_output_bytes} bytes"
                    break
                (stdout_file if name == "stdout" else stderr_file).write(chunk)
            if failure:
                stopped.set()
                self._stop_process(process)
                for thread in threads:
                    thread.join(timeout=1)
                raise ResolverError(failure)
            try:
                returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                stopped.set()
                self._stop_process(process)
                raise ResolverError(f"roster resolver timed out after {self.timeout:g} seconds") from exc
            stopped.set()
            for thread in threads:
                thread.join(timeout=1)
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read()
            stderr = stderr_file.read()
        return returncode, stdout, stderr

    def resolve(self, agent: str) -> dict[str, str]:
        code = str(agent or "").strip()
        if not _AGENT_CODE.fullmatch(code):
            raise ResolverError("agent must be an exact roster code")
        request = json.dumps({"agent": code, "mode": "mention-code"}, separators=(",", ":")).encode()
        returncode, stdout_bytes, _stderr_bytes = self._run(request)
        try:
            payload = json.loads(stdout_bytes.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResolverError("roster resolver returned invalid JSON") from exc
        if returncode != 0:
            detail = payload.get("error") if isinstance(payload, dict) and isinstance(payload.get("error"), str) else ""
            raise ResolverError(f"roster resolver rejected the target{': ' + detail[:500] if detail else ''}")
        if not isinstance(payload, dict) or set(payload) != {"status", "agent"} or payload.get("status") != "PASS":
            raise ResolverError("roster resolver returned an invalid success envelope")
        record = payload.get("agent")
        if not isinstance(record, dict) or set(record) != _AGENT_FIELDS:
            raise ResolverError("roster resolver returned an invalid agent record")
        if any(not isinstance(record[field], str) or not record[field] for field in _AGENT_FIELDS):
            raise ResolverError("roster resolver agent fields must be non-empty strings")
        if record["code"] != code or record["status"] != "active" or not _SHA256.fullmatch(record["record_sha256"]):
            raise ResolverError("roster resolver returned a mismatched agent identity")
        return {field: record[field] for field in _AGENT_FIELDS}
