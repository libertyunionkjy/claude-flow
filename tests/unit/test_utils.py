from unittest.mock import patch

from claude_flow.utils import can_skip_permissions, is_running_as_root


class TestIsRunningAsRoot:
    @patch("claude_flow.utils.os.geteuid", return_value=0)
    def test_root_user(self, mock_euid):
        assert is_running_as_root() is True

    @patch("claude_flow.utils.os.geteuid", return_value=1000)
    def test_normal_user(self, mock_euid):
        assert is_running_as_root() is False


class TestCanSkipPermissions:
    @patch("claude_flow.utils.is_running_as_root", return_value=False)
    def test_enabled_non_root(self, mock_root):
        assert can_skip_permissions(True) is True

    @patch("claude_flow.utils.is_running_as_root", return_value=True)
    def test_enabled_root(self, mock_root):
        assert can_skip_permissions(True) is False

    @patch("claude_flow.utils.is_running_as_root", return_value=False)
    def test_disabled_non_root(self, mock_root):
        assert can_skip_permissions(False) is False
        # Should not even check root when disabled
        mock_root.assert_not_called()

    @patch("claude_flow.utils.is_running_as_root", return_value=True)
    def test_disabled_root(self, mock_root):
        assert can_skip_permissions(False) is False
        mock_root.assert_not_called()
