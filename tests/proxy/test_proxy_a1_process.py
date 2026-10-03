"""A1 process-contract tests: env, cwd, stderr, encoding, partial lines.

These tests verify the proxy's process management contract -- independent of
JSON-RPC content -- to ensure nothing leaks into stdout and the child process
inherits the right execution context.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.proxy.harness import PROXY_RUNNER


# ---------------------------------------------------------------------------
# Helper: spawn proxy directly with full control over stdin/stdout/stderr
# ---------------------------------------------------------------------------

def _spawn_proxy(server_cmd: list[str], *, env=None, cwd=None, input_bytes: bytes = b"") -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(PROXY_RUNNER)] + server_cmd
    return subprocess.run(
        cmd,
        input=input_bytes,
        capture_output=True,
        env=env,
        cwd=cwd,
        timeout=15,
    )


# ---------------------------------------------------------------------------
# A1.6 -- Environment inheritance
# ---------------------------------------------------------------------------

FAKE_SERVER = Path(__file__).parent / "fake_server.py"

ENV_ECHO_SERVER = Path(__file__).parent / "env_echo_server.py"


def _ensure_env_echo_server():
    """Write a one-shot server that echoes AGENTVIEW_TEST_VAR from env."""
    script = """\
#!/usr/bin/env python3
import json, os, sys
for line in sys.stdin.buffer:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    val = os.environ.get("AGENTVIEW_TEST_VAR", "__missing__")
    msg = {"jsonrpc": "2.0", "id": req.get("id"), "result": {"env_val": val}}
    sys.stdout.buffer.write(json.dumps(msg).encode() + b"\\n")
    sys.stdout.buffer.flush()
"""
    ENV_ECHO_SERVER.write_text(script)


@pytest.fixture(autouse=True)
def _setup_env_echo_server():
    _ensure_env_echo_server()
    yield
    # leave the file for inspection if tests fail


class TestA1Environment:
    def test_env_var_inherited_by_child(self):
        """Child process receives env vars from the proxy's environment."""
        env = dict(os.environ)
        env["AGENTVIEW_TEST_VAR"] = "hello_from_proxy"
        req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
        result = _spawn_proxy(
            [sys.executable, str(ENV_ECHO_SERVER)],
            env=env,
            input_bytes=(req + "\n").encode(),
        )
        assert result.returncode == 0
        resp = json.loads(result.stdout.strip())
        assert resp["result"]["env_val"] == "hello_from_proxy"

    def test_custom_env_override(self):
        """A custom env dict replaces the default os.environ for the child."""
        minimal_env = {"PATH": os.environ.get("PATH", "/usr/bin"), "AGENTVIEW_TEST_VAR": "custom_only"}
        # Python needs PATH to be found; we strip everything else
        req = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}})
        result = _spawn_proxy(
            [sys.executable, str(ENV_ECHO_SERVER)],
            env=minimal_env,
            input_bytes=(req + "\n").encode(),
        )
        assert result.returncode == 0
        resp = json.loads(result.stdout.strip())
        assert resp["result"]["env_val"] == "custom_only"

    def test_missing_var_returns_missing(self):
        env = dict(os.environ)
        env.pop("AGENTVIEW_TEST_VAR", None)
        req = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}})
        result = _spawn_proxy(
            [sys.executable, str(ENV_ECHO_SERVER)],
            env=env,
            input_bytes=(req + "\n").encode(),
        )
        assert result.returncode == 0
        resp = json.loads(result.stdout.strip())
        assert resp["result"]["env_val"] == "__missing__"


# ---------------------------------------------------------------------------
# A1.7 -- Working directory
# ---------------------------------------------------------------------------

CWD_ECHO_SERVER = Path(__file__).parent / "cwd_echo_server.py"


def _ensure_cwd_echo_server():
    script = """\
#!/usr/bin/env python3
import json, os, sys
for line in sys.stdin.buffer:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    msg = {"jsonrpc": "2.0", "id": req.get("id"), "result": {"cwd": os.getcwd()}}
    sys.stdout.buffer.write(json.dumps(msg).encode() + b"\\n")
    sys.stdout.buffer.flush()
"""
    CWD_ECHO_SERVER.write_text(script)


@pytest.fixture(autouse=True)
def _setup_cwd_echo_server():
    _ensure_cwd_echo_server()
    yield


class TestA1WorkingDirectory:
    def test_child_cwd_matches_specified(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
            result = _spawn_proxy(
                [sys.executable, str(CWD_ECHO_SERVER)],
                cwd=tmpdir,
                input_bytes=(req + "\n").encode(),
            )
            assert result.returncode == 0
            resp = json.loads(result.stdout.strip())
            # Resolve both sides to handle macOS /private/tmp symlinks
            assert Path(resp["result"]["cwd"]).resolve() == Path(tmpdir).resolve()


# ---------------------------------------------------------------------------
# A1.8 -- Stderr contamination: proxy must not write to stdout
# ---------------------------------------------------------------------------

class TestA1StderrContamination:
    def test_crash_stderr_does_not_bleed_to_stdout(self):
        """When the server crashes, proxy diagnostics go to stderr, not stdout."""
        req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
        result = _spawn_proxy(
            [sys.executable, str(FAKE_SERVER), "crash", "7"],
            input_bytes=(req + "\n").encode(),
        )
        assert result.returncode == 7
        # stdout must be empty or valid JSON only
        for line in result.stdout.splitlines():
            line = line.strip()
            if line:
                json.loads(line)

    def test_missing_command_stderr_only(self):
        """run_proxy with a bad executable writes the error to stderr, not stdout."""
        req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
        result = _spawn_proxy(
            ["/usr/bin/definitely_does_not_exist_agentview"],
            input_bytes=(req + "\n").encode(),
        )
        assert result.returncode != 0
        assert result.stdout.strip() == b""
        assert b"agentview proxy" in result.stderr

    def test_slow_server_no_proxy_chatter_on_stdout(self):
        """A slow server must not cause the proxy to emit timing diagnostics to stdout."""
        reqs = [json.dumps({"jsonrpc": "2.0", "id": i, "method": "ping", "params": {}}) for i in range(3)]
        result = _spawn_proxy(
            [sys.executable, str(FAKE_SERVER), "slow", "100"],
            input_bytes=("\n".join(reqs) + "\n").encode(),
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line:
                json.loads(line)


# ---------------------------------------------------------------------------
# A1.9 -- UTF-8 and high-byte payloads
# ---------------------------------------------------------------------------

UTF8_SERVER = Path(__file__).parent / "utf8_echo_server.py"


def _ensure_utf8_server():
    script = """\
#!/usr/bin/env python3
import json, sys
for line in sys.stdin.buffer:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line.decode("utf-8"))
    # echo the params back verbatim
    msg = {"jsonrpc": "2.0", "id": req.get("id"), "result": req.get("params", {})}
    sys.stdout.buffer.write(json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\\n")
    sys.stdout.buffer.flush()
"""
    UTF8_SERVER.write_text(script)


@pytest.fixture(autouse=True)
def _setup_utf8_server():
    _ensure_utf8_server()
    yield


class TestA1Encoding:
    def test_unicode_payload_survives(self):
        """Multi-byte Unicode in params must survive the proxy byte-for-byte."""
        payload = "Bonjour \u00e9l\u00e8ve \u4e2d\u6587 \U0001f600"
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "echo",
            "params": {"text": payload},
        }
        result = _spawn_proxy(
            [sys.executable, str(UTF8_SERVER)],
            input_bytes=(json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        assert result.returncode == 0
        resp = json.loads(result.stdout.strip().decode("utf-8"))
        assert resp["result"]["text"] == payload

    def test_ascii_only_payload(self):
        req = {"jsonrpc": "2.0", "id": 1, "method": "echo", "params": {"text": "hello world"}}
        result = _spawn_proxy(
            [sys.executable, str(UTF8_SERVER)],
            input_bytes=(json.dumps(req) + "\n").encode(),
        )
        assert result.returncode == 0
        resp = json.loads(result.stdout.strip())
        assert resp["result"]["text"] == "hello world"

    def test_multiple_unicode_messages(self):
        """All messages survive when multiple Unicode requests are pipelined."""
        payloads = ["\u4e2d\u6587", "\u00e9l\u00e8ve", "\U0001f600\U0001f601"]
        reqs = [
            json.dumps({"jsonrpc": "2.0", "id": i, "method": "echo", "params": {"text": p}}, ensure_ascii=False)
            for i, p in enumerate(payloads)
        ]
        input_bytes = ("\n".join(reqs) + "\n").encode("utf-8")
        result = _spawn_proxy(
            [sys.executable, str(UTF8_SERVER)],
            input_bytes=input_bytes,
        )
        assert result.returncode == 0
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert len(lines) == len(payloads)
        for line in lines:
            resp = json.loads(line.decode("utf-8"))
            assert resp["result"]["text"] in payloads


# ---------------------------------------------------------------------------
# A1.10 -- Missing command: PROXY_EXIT_FAILURE on bad executable
# ---------------------------------------------------------------------------

class TestA1MissingCommand:
    def test_bad_executable_returns_failure_code(self):
        from agentview.proxy import PROXY_EXIT_FAILURE
        result = _spawn_proxy(["/usr/bin/totally_missing_agentview_cmd"])
        assert result.returncode == PROXY_EXIT_FAILURE

    def test_empty_argv_via_runner_returns_failure(self):
        """_proxy_runner.py with no args prints to stderr and exits with PROXY_EXIT_FAILURE."""
        from agentview.proxy import PROXY_EXIT_FAILURE
        proc = subprocess.run(
            [sys.executable, str(PROXY_RUNNER)],
            capture_output=True,
            timeout=10,
        )
        assert proc.returncode == PROXY_EXIT_FAILURE
        assert b"no server_argv" in proc.stderr
