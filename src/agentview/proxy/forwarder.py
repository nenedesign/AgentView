"""Transparent MCP stdio proxy: forward bytes, preserve framing, forward signals.

Contract:
- stdin  → child stdin  (byte-for-byte, line-at-a-time, no modification)
- child stdout → stdout (byte-for-byte, line-at-a-time, no modification)
- child stderr → stderr (inherited file descriptor, no proxy involvement)
- proxy diagnostics → stderr only; NEVER to stdout
- Exit code: child's exit code on normal/error exit; PROXY_EXIT_FAILURE on
  proxy-side failure (process spawn error, thread error).

Signal handling:
- SIGTERM / SIGINT: forwarded to child immediately; SIGKILL fired after
  SIGKILL_TIMEOUT_SECONDS if child has not exited.
- Windows: not supported. run_proxy() prints a message and returns
  PROXY_EXIT_UNSUPPORTED.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any

PROXY_EXIT_FAILURE = 3
PROXY_EXIT_UNSUPPORTED = 4
SIGKILL_TIMEOUT_SECONDS = 5.0


def run_proxy(
    server_argv: list[str],
    *,
    capture: Callable[[bytes, str], Any] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> int:
    """Spawn server_argv as a subprocess and proxy its stdio.

    Parameters
    ----------
    server_argv:
        The wrapped server's command and arguments.
    capture:
        Optional hook called after each line is forwarded.
        Signature: capture(line: bytes, direction: str) where direction is
        "client_to_server" or "server_to_client". Must not raise.
    env:
        Environment for the child process. Defaults to the current process
        environment (os.environ). Pass a mapping to override or extend.
    cwd:
        Working directory for the child process. Defaults to the current
        working directory.

    Returns
    -------
    int
        The child process exit code, or PROXY_EXIT_FAILURE on proxy error,
        or PROXY_EXIT_UNSUPPORTED on Windows.
    """
    if sys.platform == "win32":
        print(
            "agentview proxy does not support Windows in v1. "
            "Use WSL or a Linux/macOS host.",
            file=sys.stderr,
        )
        return PROXY_EXIT_UNSUPPORTED

    if not server_argv:
        print("agentview proxy: no server command provided.", file=sys.stderr)
        return PROXY_EXIT_FAILURE

    child_env = dict(os.environ) if env is None else env

    try:
        proc = subprocess.Popen(
            server_argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            env=child_env,
            cwd=cwd,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        print(f"agentview proxy: failed to start server: {exc}", file=sys.stderr)
        return PROXY_EXIT_FAILURE

    _install_signal_handlers(proc)

    errors: list[Exception] = []

    def client_to_server() -> None:
        try:
            for line in sys.stdin.buffer:
                proc.stdin.write(line)
                proc.stdin.flush()
                if capture is not None:
                    try:
                        capture(line, "client_to_server")
                    except Exception:
                        pass
        except BrokenPipeError:
            pass
        except Exception as exc:
            errors.append(exc)
            print(f"agentview proxy [c2s]: {exc}", file=sys.stderr)
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

    def server_to_client() -> None:
        try:
            for line in proc.stdout:
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
                if capture is not None:
                    try:
                        capture(line, "server_to_client")
                    except Exception:
                        pass
        except BrokenPipeError:
            pass
        except Exception as exc:
            errors.append(exc)
            print(f"agentview proxy [s2c]: {exc}", file=sys.stderr)

    t_in = threading.Thread(target=client_to_server, name="proxy-c2s", daemon=True)
    t_out = threading.Thread(target=server_to_client, name="proxy-s2c", daemon=True)
    t_in.start()
    t_out.start()
    t_in.join()
    t_out.join()

    exit_code = proc.wait()

    if errors:
        return PROXY_EXIT_FAILURE
    return exit_code


def _install_signal_handlers(proc: subprocess.Popen) -> None:
    """Forward SIGTERM and SIGINT to the child; schedule SIGKILL if needed."""

    def _make_handler(signum: int) -> Callable:
        def handler(received_sig: int, frame: Any) -> None:
            try:
                proc.send_signal(signum)
            except ProcessLookupError:
                return

            kill_timer = threading.Timer(
                SIGKILL_TIMEOUT_SECONDS, _force_kill, args=(proc,)
            )
            kill_timer.daemon = True
            kill_timer.start()

        return handler

    signal.signal(signal.SIGTERM, _make_handler(signal.SIGTERM))
    signal.signal(signal.SIGINT, _make_handler(signal.SIGINT))


def _force_kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass
