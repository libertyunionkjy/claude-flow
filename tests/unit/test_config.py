import json
import os
from pathlib import Path
from claude_flow.config import Config, DEFAULT_CONFIG


class TestConfig:
    def test_default_config(self):
        cfg = Config()
        assert cfg.max_workers == 2
        assert cfg.main_branch == "main"
        assert cfg.auto_merge is True
        assert cfg.skip_permissions is True
        assert cfg.task_timeout == 600

    def test_load_from_file(self, tmp_path):
        config_dir = tmp_path / ".claude-flow"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"max_workers": 5, "main_branch": "develop"}))
        cfg = Config.load(tmp_path)
        assert cfg.max_workers == 5
        assert cfg.main_branch == "develop"
        # defaults still apply for unset keys
        assert cfg.auto_merge is True

    def test_load_missing_file_returns_defaults(self, tmp_path):
        cfg = Config.load(tmp_path)
        assert cfg.max_workers == 2

    def test_save_config(self, tmp_path):
        config_dir = tmp_path / ".claude-flow"
        config_dir.mkdir()
        cfg = Config(max_workers=3)
        cfg.save(tmp_path)
        loaded = json.loads((config_dir / "config.json").read_text())
        assert loaded["max_workers"] == 3

    def test_claude_flow_dir(self, tmp_path):
        cfg = Config()
        d = cfg.claude_flow_dir(tmp_path)
        assert d == tmp_path / ".claude-flow"

    def test_use_subagent_default_false(self):
        cfg = Config()
        assert cfg.use_subagent is False

    def test_use_subagent_from_file(self, tmp_path):
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir()
        config_file = cf_dir / "config.json"
        config_file.write_text('{"use_subagent": true}')
        cfg = Config.load(tmp_path)
        assert cfg.use_subagent is True
