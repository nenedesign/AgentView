"""A2 acceptance tests: config safety -- 16 scenarios.

Acceptance criterion A2: In every one of the 16 test cases, Claude Desktop's
config either returns to pre-AgentView state OR enters an explicit Conflict
state with both versions preserved. AgentView never silently overwrites an
externally changed configuration.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import threading
from pathlib import Path

import pytest

from agentview.proxy.config_safety import (
    AGENTVIEW_MARKER,
    ConfigError,
    ConflictError,
    atomic_write,
    build_restore_entry,
    build_wrapped_entry,
    format_diff,
    inject_proxy,
    list_entries,
    read_config,
    restore_entry,
    sha256_file,
    validate_entry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_CONFIG = {
    "mcpServers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        }
    }
}

AGENTVIEW_ARGV = ["agentview", "proxy", "--"]


def _write_config(path: Path, content: dict) -> None:
    path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# A2.1 -- Read + validate: supported subset
# ---------------------------------------------------------------------------

class TestReadValidate:
    def test_read_valid_config(self, tmp_path):
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        parsed, sha = read_config(cfg)
        assert "mcpServers" in parsed
        assert isinstance(sha, str) and len(sha) == 64

    def test_read_missing_file_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            read_config(tmp_path / "nonexistent.json")

    def test_read_invalid_json_raises(self, tmp_path):
        cfg = tmp_path / "claude_desktop_config.json"
        cfg.write_bytes(b"not valid json {{{")
        with pytest.raises(ConfigError, match="not valid JSON"):
            read_config(cfg)

    def test_validate_entry_ok(self):
        validate_entry("fs", {"command": "npx", "args": ["-y", "server"], "env": {"A": "B"}})

    def test_validate_entry_missing_command(self):
        with pytest.raises(ConfigError):
            validate_entry("fs", {"args": ["-y"]})

    def test_validate_entry_command_not_string(self):
        with pytest.raises(ConfigError):
            validate_entry("fs", {"command": 42})

    def test_validate_entry_args_not_list(self):
        with pytest.raises(ConfigError):
            validate_entry("fs", {"command": "npx", "args": "nope"})

    def test_validate_entry_env_non_string_value(self):
        with pytest.raises(ConfigError):
            validate_entry("fs", {"command": "npx", "env": {"A": 99}})

    def test_list_entries_returns_entries(self, tmp_path):
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        parsed, _ = read_config(cfg)
        entries = list_entries(parsed)
        assert "filesystem" in entries
        assert entries["filesystem"].command == "npx"

    def test_existing_invalid_config(self, tmp_path):
        """An already-invalid config (missing command) must raise ConfigError."""
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, {"mcpServers": {"broken": {"args": ["-y"]}}})
        with pytest.raises(ConfigError):
            inject_proxy("broken", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)


# ---------------------------------------------------------------------------
# A2.2 -- Backup
# ---------------------------------------------------------------------------

class TestBackup:
    def test_backup_file_created(self, tmp_path):
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)
        backups = list(tmp_path.glob("claude_desktop_config.json.agentview-backup-*"))
        assert len(backups) == 1

    def test_backup_content_matches_original(self, tmp_path):
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        original_bytes = cfg.read_bytes()
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)
        backups = list(tmp_path.glob("claude_desktop_config.json.agentview-backup-*"))
        assert backups[0].read_bytes() == original_bytes

    def test_backup_already_exists_handled(self, tmp_path):
        """Running inject twice creates two backups, not one overwriting the other."""
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)
        # Restore so we can inject again
        restore_entry("filesystem", config_path=cfg)
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)
        backups = list(tmp_path.glob("claude_desktop_config.json.agentview-backup-*"))
        assert len(backups) >= 2


# ---------------------------------------------------------------------------
# A2.3 -- Atomic write + post-write health check
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_successful_inject_produces_valid_json(self, tmp_path):
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)
        parsed = json.loads(cfg.read_bytes())
        assert "mcpServers" in parsed
        server = parsed["mcpServers"]["filesystem"]
        assert AGENTVIEW_MARKER in server

    def test_inject_preserves_other_servers(self, tmp_path):
        """Other mcpServers entries must not be touched."""
        config = {
            "mcpServers": {
                "filesystem": {"command": "npx", "args": ["-y", "server-filesystem"]},
                "other": {"command": "other-cmd"},
            }
        }
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, config)
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)
        parsed = json.loads(cfg.read_bytes())
        assert parsed["mcpServers"]["other"]["command"] == "other-cmd"

    def test_inject_preserves_top_level_keys(self, tmp_path):
        """Top-level keys beyond mcpServers must be preserved unchanged."""
        config = {**MINIMAL_CONFIG, "someOtherKey": "preserved"}
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, config)
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)
        parsed = json.loads(cfg.read_bytes())
        assert parsed.get("someOtherKey") == "preserved"


# ---------------------------------------------------------------------------
# A2.4 -- Optimistic concurrency: conflict detection
# ---------------------------------------------------------------------------

class TestConflictDetection:
    def test_external_edit_raises_conflict(self, tmp_path):
        """If the file changes between read and write, ConflictError must be raised."""
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        _, original_sha = read_config(cfg)

        # Simulate external edit
        modified = {**MINIMAL_CONFIG, "mcpServers": {**MINIMAL_CONFIG["mcpServers"], "extra": {"command": "x"}}}
        _write_config(cfg, modified)

        with pytest.raises(ConflictError):
            atomic_write(cfg, MINIMAL_CONFIG, original_sha)

    def test_conflict_does_not_modify_file(self, tmp_path):
        """On ConflictError, the file on disk must be unchanged."""
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        _, original_sha = read_config(cfg)

        modified = {**MINIMAL_CONFIG, "extra": True}
        _write_config(cfg, modified)
        sha_before = _sha(cfg)

        with pytest.raises(ConflictError):
            atomic_write(cfg, {"new": "content"}, original_sha)

        assert _sha(cfg) == sha_before

    def test_concurrent_edit_triggers_conflict(self, tmp_path):
        """Simulate another tool editing the file concurrently during the flow."""
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        config, sha = read_config(cfg)

        # Concurrent edit happens before atomic_write
        _write_config(cfg, {**MINIMAL_CONFIG, "concurrent": True})

        with pytest.raises(ConflictError):
            atomic_write(cfg, config, sha)

    def test_no_conflict_when_sha_matches(self, tmp_path):
        """When the SHA matches, atomic_write must succeed."""
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        _, sha = read_config(cfg)
        atomic_write(cfg, {**MINIMAL_CONFIG, "added": True}, sha)
        parsed = json.loads(cfg.read_bytes())
        assert parsed.get("added") is True


# ---------------------------------------------------------------------------
# A2.5 -- Server not found
# ---------------------------------------------------------------------------

class TestServerNotFound:
    def test_inject_missing_server_raises(self, tmp_path):
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        with pytest.raises(ConfigError, match="not found"):
            inject_proxy("nonexistent", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)

    def test_restore_missing_server_raises(self, tmp_path):
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        with pytest.raises(ConfigError):
            restore_entry("nonexistent", config_path=cfg)


# ---------------------------------------------------------------------------
# A2.6 -- Idempotency: already-wrapped entry
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_inject_twice_raises_already_wrapped(self, tmp_path):
        """Injecting again when the entry is already wrapped must raise ConfigError."""
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)
        with pytest.raises(ConfigError, match="already wrapped"):
            inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)

    def test_restore_then_inject_again_succeeds(self, tmp_path):
        """Restore then re-inject must succeed without error."""
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)
        restore_entry("filesystem", config_path=cfg)
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)
        parsed = json.loads(cfg.read_bytes())
        assert AGENTVIEW_MARKER in parsed["mcpServers"]["filesystem"]


# ---------------------------------------------------------------------------
# A2.7 -- Restore: original entry recovery
# ---------------------------------------------------------------------------

class TestRestore:
    def test_restore_recovers_original_command(self, tmp_path):
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        original = json.loads(cfg.read_bytes())
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)
        restore_entry("filesystem", config_path=cfg)
        restored = json.loads(cfg.read_bytes())
        assert restored["mcpServers"]["filesystem"]["command"] == original["mcpServers"]["filesystem"]["command"]

    def test_restore_removes_marker(self, tmp_path):
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)
        restore_entry("filesystem", config_path=cfg)
        parsed = json.loads(cfg.read_bytes())
        assert AGENTVIEW_MARKER not in parsed["mcpServers"]["filesystem"]

    def test_restore_without_marker_raises(self, tmp_path):
        """Restore on a non-wrapped entry must raise ConfigError."""
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        with pytest.raises(ConfigError):
            restore_entry("filesystem", config_path=cfg)

    def test_restore_writes_backup(self, tmp_path):
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)
        backups_before = list(tmp_path.glob("*.agentview-backup-*"))
        restore_entry("filesystem", config_path=cfg)
        backups_after = list(tmp_path.glob("*.agentview-backup-*"))
        assert len(backups_after) > len(backups_before)


# ---------------------------------------------------------------------------
# A2.8 -- Dry run
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_writes_no_file(self, tmp_path):
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        sha_before = _sha(cfg)
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV, dry_run=True)
        assert _sha(cfg) == sha_before

    def test_dry_run_creates_no_backup(self, tmp_path):
        cfg = tmp_path / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV, dry_run=True)
        backups = list(tmp_path.glob("*.agentview-backup-*"))
        assert len(backups) == 0


# ---------------------------------------------------------------------------
# A2.9 -- Permission denied (POSIX only)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="Windows permission model differs")
class TestPermissions:
    def test_read_only_directory_raises(self, tmp_path):
        """A read-only directory prevents creating a backup or tempfile."""
        subdir = tmp_path / "config_dir"
        subdir.mkdir()
        cfg = subdir / "claude_desktop_config.json"
        _write_config(cfg, MINIMAL_CONFIG)
        subdir.chmod(0o555)
        try:
            with pytest.raises((ConfigError, PermissionError, OSError)):
                inject_proxy("filesystem", config_path=cfg, agentview_argv=AGENTVIEW_ARGV)
        finally:
            subdir.chmod(0o755)


# ---------------------------------------------------------------------------
# A2.10 -- Diff display
# ---------------------------------------------------------------------------

class TestDiffDisplay:
    def test_format_diff_contains_server_name(self):
        current = {"command": "npx", "args": ["-y", "server"]}
        proposed = {"command": "agentview", "args": ["proxy", "--", "npx", "-y", "server"]}
        diff = format_diff("filesystem", current, proposed)
        assert "filesystem" in diff
        assert "npx" in diff
        assert "agentview" in diff

    def test_format_diff_shows_current_and_proposed(self):
        current = {"command": "cmd-a"}
        proposed = {"command": "cmd-b"}
        diff = format_diff("srv", current, proposed)
        assert "CURRENT" in diff
        assert "PROPOSED" in diff
