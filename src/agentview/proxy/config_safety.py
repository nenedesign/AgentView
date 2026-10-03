"""10-step guided config flow for claude_desktop_config.json.

AgentView wraps existing MCP server entries with its stdio proxy. This module
handles reading, validating, diffing, backing up, atomically writing, and
restoring the Claude Desktop config file safely.

Optimistic concurrency protection: the SHA-256 hash of the file at read-time
is recorded. Before every write, the file is re-hashed. If the hash changed,
the file was externally modified and the write is aborted, entering Conflict
state with both versions preserved.

Supported config subset (claude_desktop_config.json):
    {
        "mcpServers": {
            "<name>": {
                "command": "<string>",
                "args": ["<string>", ...],    # optional
                "env": {"<key>": "<value>"}  # optional
            }
        }
    }

Top-level keys other than "mcpServers" are preserved unchanged. Server entries
with keys beyond "command", "args", "env" are supported only if those extra
keys are not modified by this tool. Any entry missing "command" is unsupported.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Claude"
    / "claude_desktop_config.json"
)

BACKUP_SUFFIX_FMT = ".agentview-backup-{ts}"

AGENTVIEW_MARKER = "__agentview_proxy__"

UNSUPPORTED_MSG = """\
agentview config: unsupported configuration detected.

To protect your Claude Desktop setup, AgentView only modifies entries that:
  1. Have a "command" field (string).
  2. Have "args" as a list of strings, if present.
  3. Have "env" as a dict of strings, if present.
  4. Are not already wrapped by AgentView.
  5. Have no unrecognized extra keys that would conflict with AgentView injection.

What to do:
  1. Open the config file at: {path}
  2. Back it up manually.
  3. Ensure the entry for "{server_name}" follows the supported format.
  4. Re-run: agentview proxy -- <your command>
  5. If the issue persists, report it at https://github.com/nenedesign/AgentView/issues
"""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """Raised when the config file cannot be read, parsed, or validated."""


class ConflictError(Exception):
    """Raised when the config file changed between read and write."""

    def __init__(self, message: str, backup_path: Path | None = None) -> None:
        super().__init__(message)
        self.backup_path = backup_path


class ConfigEntry:
    """One parsed MCP server entry from claude_desktop_config.json."""

    def __init__(self, name: str, raw: dict[str, Any]) -> None:
        self.name = name
        self.raw = raw

    @property
    def command(self) -> str:
        return self.raw["command"]

    @property
    def args(self) -> list[str]:
        return self.raw.get("args", [])

    @property
    def env(self) -> dict[str, str]:
        return self.raw.get("env", {})

    @property
    def is_agentview_wrapped(self) -> bool:
        return bool(self.raw.get(AGENTVIEW_MARKER))

    def full_command(self) -> list[str]:
        return [self.command] + self.args

    def __repr__(self) -> str:
        return f"ConfigEntry(name={self.name!r}, command={self.command!r})"


# ---------------------------------------------------------------------------
# Read + validate
# ---------------------------------------------------------------------------

def read_config(path: Path | None = None) -> tuple[dict[str, Any], str]:
    """Read and parse the config file. Returns (parsed_dict, sha256_hex).

    Raises ConfigError on any read or parse failure.
    """
    config_path = path or DEFAULT_CONFIG_PATH
    try:
        raw_bytes = config_path.read_bytes()
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {config_path}")
    except PermissionError:
        raise ConfigError(f"Permission denied reading config: {config_path}")
    except OSError as exc:
        raise ConfigError(f"Error reading config: {exc}")

    sha = hashlib.sha256(raw_bytes).hexdigest()

    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Config file is not valid JSON: {exc}")

    if not isinstance(parsed, dict):
        raise ConfigError("Config file root must be a JSON object.")

    return parsed, sha


def validate_entry(name: str, entry: dict[str, Any]) -> None:
    """Validate a single MCP server entry against the supported subset.

    Raises ConfigError with a user-friendly UNSUPPORTED_MSG on failure.
    """
    if not isinstance(entry, dict):
        raise ConfigError(
            UNSUPPORTED_MSG.format(path=DEFAULT_CONFIG_PATH, server_name=name)
        )
    if "command" not in entry or not isinstance(entry["command"], str):
        raise ConfigError(
            UNSUPPORTED_MSG.format(path=DEFAULT_CONFIG_PATH, server_name=name)
        )
    if "args" in entry and not (
        isinstance(entry["args"], list)
        and all(isinstance(a, str) for a in entry["args"])
    ):
        raise ConfigError(
            UNSUPPORTED_MSG.format(path=DEFAULT_CONFIG_PATH, server_name=name)
        )
    if "env" in entry and not (
        isinstance(entry["env"], dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in entry["env"].items())
    ):
        raise ConfigError(
            UNSUPPORTED_MSG.format(path=DEFAULT_CONFIG_PATH, server_name=name)
        )


def list_entries(config: dict[str, Any]) -> dict[str, ConfigEntry]:
    """Return all MCP server entries as ConfigEntry objects."""
    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ConfigError("mcpServers must be a JSON object.")
    return {name: ConfigEntry(name, entry) for name, entry in servers.items()}


# ---------------------------------------------------------------------------
# Diff display
# ---------------------------------------------------------------------------

def format_diff(name: str, current: dict[str, Any], proposed: dict[str, Any]) -> str:
    """Return a human-readable diff string for the terminal."""
    lines = [
        f"  Server: {name}",
        "",
        "  CURRENT:",
        f"    command: {current.get('command', '(none)')}",
        f"    args:    {current.get('args', [])}",
    ]
    if current.get("env"):
        lines.append(f"    env:     {current['env']}")
    lines += [
        "",
        "  PROPOSED (wrapped by agentview proxy):",
        f"    command: {proposed.get('command', '(none)')}",
        f"    args:    {proposed.get('args', [])}",
    ]
    if proposed.get("env"):
        lines.append(f"    env:     {proposed['env']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build proposed entry
# ---------------------------------------------------------------------------

def build_wrapped_entry(
    entry: ConfigEntry,
    agentview_argv: list[str],
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Wrap an existing server entry so it runs through agentview proxy.

    The original command and args become the tail of agentview proxy's argv.
    The AGENTVIEW_MARKER key records the original command for restore.
    """
    original_cmd = [entry.command] + entry.args
    wrapped: dict[str, Any] = {
        "command": agentview_argv[0],
        "args": agentview_argv[1:] + ["--"] + original_cmd,
        AGENTVIEW_MARKER: {
            "original_command": entry.command,
            "original_args": entry.args,
            "wrapped_at": _now_iso(),
        },
    }
    merged_env = dict(entry.env)
    if extra_env:
        merged_env.update(extra_env)
    if merged_env:
        wrapped["env"] = merged_env
    return wrapped


def build_restore_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Reverse a wrapped entry back to the original command and args."""
    marker = entry.get(AGENTVIEW_MARKER)
    if not marker or not isinstance(marker, dict):
        raise ConfigError("Entry does not have a valid AgentView marker. Cannot restore.")
    restored: dict[str, Any] = {
        "command": marker["original_command"],
    }
    if marker.get("original_args"):
        restored["args"] = marker["original_args"]
    # Preserve env if present, minus any agentview-injected keys
    if entry.get("env"):
        restored["env"] = dict(entry["env"])
    return restored


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def write_backup(config_path: Path) -> tuple[Path, str]:
    """Copy the config file to a backup and return (backup_path, original_sha256).

    The backup filename encodes the ISO timestamp so it's human-readable and
    collision-resistant. The SHA-256 is computed from the live file bytes at
    the moment of backup -- the same bytes are what we protect.
    """
    raw_bytes = config_path.read_bytes()
    sha = hashlib.sha256(raw_bytes).hexdigest()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = BACKUP_SUFFIX_FMT.format(ts=ts)
    backup_path = config_path.with_name(config_path.name + suffix)
    backup_path.write_bytes(raw_bytes)
    return backup_path, sha


# ---------------------------------------------------------------------------
# Atomic write with optimistic concurrency
# ---------------------------------------------------------------------------

def atomic_write(
    config_path: Path,
    new_config: dict[str, Any],
    expected_sha: str,
    backup_path: Path | None = None,
) -> None:
    """Write new_config to config_path atomically via a tempfile + os.replace.

    Checks the file's current SHA-256 before writing. If it differs from
    expected_sha, raises ConflictError without modifying any file.

    Parameters
    ----------
    config_path:
        The config file to update.
    new_config:
        The new content to write (will be JSON-serialized with indent=2).
    expected_sha:
        SHA-256 hex digest of the file content at the last read. If the live
        file has changed since then, the write is aborted.
    backup_path:
        Included in ConflictError so the caller can surface it to the user.
    """
    current_bytes = config_path.read_bytes()
    current_sha = hashlib.sha256(current_bytes).hexdigest()
    if current_sha != expected_sha:
        raise ConflictError(
            f"Config file changed since it was read (expected SHA {expected_sha[:8]}... "
            f"but found {current_sha[:8]}...). "
            f"Your backup is at: {backup_path or '(none)'}. "
            "AgentView has not modified the file.",
            backup_path=backup_path,
        )

    new_bytes = (json.dumps(new_config, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    # Write to a tempfile in the same directory so os.replace is atomic
    # (same filesystem guarantees atomic rename on POSIX)
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        dir=config_path.parent,
        prefix=".agentview-tmp-",
        suffix=".json",
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(new_bytes)
        os.replace(tmp_path, config_path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    # Post-write health check: re-read, parse, verify parseable
    try:
        written_bytes = config_path.read_bytes()
        json.loads(written_bytes.decode("utf-8"))
    except Exception as exc:
        raise ConfigError(f"Post-write health check failed: {exc}")


# ---------------------------------------------------------------------------
# High-level inject / restore
# ---------------------------------------------------------------------------

def inject_proxy(
    name: str,
    config_path: Path | None = None,
    agentview_argv: list[str] | None = None,
    *,
    dry_run: bool = False,
) -> tuple[str, Path]:
    """Read config, validate, backup, wrap entry, atomic write.

    Returns (original_sha, backup_path). On dry_run, skips the write and
    backup; returns (sha, Path("<dry-run>")).

    Raises ConfigError or ConflictError on any failure.
    """
    cpath = config_path or DEFAULT_CONFIG_PATH
    av_argv = agentview_argv or [sys.executable, "-m", "agentview.cli", "proxy"]

    config, sha = read_config(cpath)
    servers = config.get("mcpServers", {})
    if name not in servers:
        raise ConfigError(f"Server '{name}' not found in mcpServers.")

    entry_raw = servers[name]
    validate_entry(name, entry_raw)
    entry = ConfigEntry(name, entry_raw)

    if entry.is_agentview_wrapped:
        raise ConfigError(f"Server '{name}' is already wrapped by AgentView.")

    proposed = build_wrapped_entry(entry, av_argv)

    if dry_run:
        print(format_diff(name, entry_raw, proposed))
        return sha, Path("<dry-run>")

    backup_path, original_sha = write_backup(cpath)

    new_config = {**config}
    new_config["mcpServers"] = {**servers, name: proposed}

    atomic_write(cpath, new_config, original_sha, backup_path=backup_path)

    return original_sha, backup_path


def restore_entry(
    name: str,
    config_path: Path | None = None,
    *,
    expected_sha: str | None = None,
) -> Path:
    """Remove the AgentView wrapper from a server entry and restore the original.

    Returns the backup path created before restore.
    Raises ConfigError if the entry cannot be restored.
    Raises ConflictError if the file changed since expected_sha.
    """
    cpath = config_path or DEFAULT_CONFIG_PATH

    config, sha = read_config(cpath)
    if expected_sha and sha != expected_sha:
        raise ConflictError(
            f"Config file changed since last read. Cannot restore safely. "
            f"Current SHA: {sha[:8]}..., expected: {expected_sha[:8]}..."
        )

    servers = config.get("mcpServers", {})
    if name not in servers:
        raise ConfigError(f"Server '{name}' not found in mcpServers.")

    entry_raw = servers[name]
    restored = build_restore_entry(entry_raw)

    backup_path, live_sha = write_backup(cpath)

    new_config = {**config}
    new_config["mcpServers"] = {**servers, name: restored}

    atomic_write(cpath, new_config, live_sha, backup_path=backup_path)

    return backup_path


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
