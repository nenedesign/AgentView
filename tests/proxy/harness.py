"""Test harness: spawn proxy wrapping fake_server, pipe requests, collect output.

Usage in tests:

    from tests.proxy.harness import run_through_proxy

    result = run_through_proxy(
        server_mode="echo",
        requests=[{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{...}}],
        timeout=10,
    )
    assert result.exit_code == 0
    assert len(result.responses) == 1
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Path to the fake server script
FAKE_SERVER = Path(__file__).parent / "fake_server.py"

# Path to a minimal wrapper that calls run_proxy() directly
PROXY_RUNNER = Path(__file__).parent / "_proxy_runner.py"


@dataclass
class ProxyResult:
    exit_code: int
    stdout_raw: bytes
    stderr_raw: bytes
    responses: list[dict] = field(default_factory=list)
    skipped_lines: int = 0


def _build_input(requests: list[dict]) -> bytes:
    lines = [json.dumps(r) + "\n" for r in requests]
    return "".join(lines).encode()


def run_through_proxy(
    requests: list[dict],
    *,
    server_mode: str = "echo",
    server_mode_arg: str | None = None,
    timeout: float = 15,
) -> ProxyResult:
    """Run requests through proxy → fake_server and return collected output."""
    server_cmd = [sys.executable, str(FAKE_SERVER), server_mode]
    if server_mode_arg is not None:
        server_cmd.append(server_mode_arg)

    proxy_cmd = [
        sys.executable, str(PROXY_RUNNER),
    ] + server_cmd

    input_bytes = _build_input(requests)

    proc = subprocess.Popen(
        proxy_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        stdout, stderr = proc.communicate(input=input_bytes, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()

    responses = []
    skipped = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            responses.append(msg)
        except json.JSONDecodeError:
            skipped += 1

    return ProxyResult(
        exit_code=proc.returncode,
        stdout_raw=stdout,
        stderr_raw=stderr,
        responses=responses,
        skipped_lines=skipped,
    )
