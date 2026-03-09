"""Config robustness tests.

Tests corrupted config files, type mismatches, missing fields,
environment variable edge cases, and config save/reload scenarios.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_flow.config import Config


class TestConfigFileCorruption:
    """Test behavior with corrupted/invalid config files."""

    def test_empty_config_file(self, cf_project: Path):
        """Empty config.json should raise JSONDecodeError (no fallback)."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text("")
        with pytest.raises(json.JSONDecodeError):
            Config.load(cf_project)

    def test_invalid_json_config(self, cf_project: Path):
        """Malformed JSON config should raise JSONDecodeError."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text("{not valid json!!!")
        with pytest.raises(json.JSONDecodeError):
            Config.load(cf_project)

    def test_null_json_config(self, cf_project: Path):
        """config.json containing 'null' should raise TypeError."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text("null")
        with pytest.raises(TypeError):
            Config.load(cf_project)

    def test_array_instead_of_object(self, cf_project: Path):
        """config.json containing array should raise TypeError."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text("[1, 2, 3]")
        with pytest.raises(TypeError):
            Config.load(cf_project)

    def test_config_with_extra_unknown_fields(self, cf_project: Path):
        """Unknown fields in config should be ignored, not crash."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({
            "max_workers": 4,
            "unknown_field_xyz": "value",
            "another_unknown": 123,
        }))
        config = Config.load(cf_project)
        assert config.max_workers == 4

    def test_config_missing_file(self, cf_project: Path):
        """No config.json at all should use defaults."""
        config_file = cf_project / ".claude-flow" / "config.json"
        if config_file.exists():
            config_file.unlink()
        config = Config.load(cf_project)
        assert config.max_workers == 2
        assert config.main_branch == "main"


class TestConfigTypeMismatch:
    """Test behavior when config values have wrong types."""

    def test_max_workers_as_string(self, cf_project: Path):
        """max_workers='abc' should load (dataclass accepts any value)."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({"max_workers": "abc"}))
        # dataclass doesn't enforce types, so "abc" is accepted
        config = Config.load(cf_project)
        assert config.max_workers == "abc"

    def test_max_workers_negative(self, cf_project: Path):
        """max_workers=-1 should be loaded (validation elsewhere)."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({"max_workers": -1}))
        config = Config.load(cf_project)
        assert config.max_workers == -1

    def test_max_workers_zero(self, cf_project: Path):
        """max_workers=0 should be loaded."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({"max_workers": 0}))
        config = Config.load(cf_project)
        assert config.max_workers == 0

    def test_auto_merge_as_string(self, cf_project: Path):
        """auto_merge='yes' instead of True."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({"auto_merge": "yes"}))
        config = Config.load(cf_project)
        # Should be truthy string
        assert config.auto_merge == "yes"

    def test_claude_args_as_string(self, cf_project: Path):
        """claude_args='--verbose' instead of list."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({"claude_args": "--verbose"}))
        config = Config.load(cf_project)
        assert config.claude_args == "--verbose"

    def test_task_timeout_float(self, cf_project: Path):
        """task_timeout=30.5 (float instead of int)."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({"task_timeout": 30.5}))
        config = Config.load(cf_project)
        assert config.task_timeout == 30.5


class TestConfigSaveReload:
    """Test config save and reload round-trips."""

    def test_save_and_reload(self, cf_project: Path):
        """Saved config should be identical when reloaded."""
        config = Config.load(cf_project)
        config.max_workers = 8
        config.main_branch = "develop"
        config.claude_args = ["--verbose", "--model", "opus"]
        config.save(cf_project)

        reloaded = Config.load(cf_project)
        assert reloaded.max_workers == 8
        assert reloaded.main_branch == "develop"
        assert reloaded.claude_args == ["--verbose", "--model", "opus"]

    def test_save_creates_directory(self, tmp_path: Path):
        """Saving config to non-existent directory should create it."""
        config = Config()
        cf_dir = tmp_path / ".claude-flow"
        assert not cf_dir.exists()
        config.save(tmp_path)
        assert (cf_dir / "config.json").exists()

    def test_save_preserves_all_fields(self, cf_project: Path):
        """All config fields should survive save/reload."""
        config = Config()
        config.max_workers = 5
        config.merge_mode = "squash"
        config.pre_merge_commands = ["pytest -v"]
        config.auto_push = True
        config.save(cf_project)

        reloaded = Config.load(cf_project)
        assert reloaded.max_workers == 5
        assert reloaded.merge_mode == "squash"
        assert reloaded.pre_merge_commands == ["pytest -v"]
        assert reloaded.auto_push is True


class TestEnvironmentVariables:
    """Test CF_PROJECT_ROOT environment variable handling."""

    def test_cf_project_root_valid(self, cf_project: Path):
        """CF_PROJECT_ROOT pointing to valid project."""
        with patch.dict(os.environ, {"CF_PROJECT_ROOT": str(cf_project)}):
            config = Config.load(cf_project)
            assert config.max_workers >= 0

    def test_cf_project_root_nonexistent(self, tmp_path: Path):
        """CF_PROJECT_ROOT pointing to non-existent directory."""
        fake_path = tmp_path / "nonexistent"
        with patch.dict(os.environ, {"CF_PROJECT_ROOT": str(fake_path)}):
            # Config.load on non-existent path returns defaults (no config file)
            config = Config.load(fake_path)
            assert config.max_workers == 2  # defaults

    def test_cf_project_root_empty_string(self, cf_project: Path):
        """CF_PROJECT_ROOT set to empty string."""
        with patch.dict(os.environ, {"CF_PROJECT_ROOT": ""}):
            config = Config.load(cf_project)
            assert config is not None

    def test_valid_override_values(self, cf_project: Path):
        """Config with all valid override values loads correctly."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({
            "max_workers": 10,
            "main_branch": "develop",
            "auto_merge": False,
            "task_timeout": 300,
            "web_port": 9090,
        }))
        config = Config.load(cf_project)
        assert config.max_workers == 10
        assert config.main_branch == "develop"
        assert config.auto_merge is False
        assert config.task_timeout == 300
        assert config.web_port == 9090
