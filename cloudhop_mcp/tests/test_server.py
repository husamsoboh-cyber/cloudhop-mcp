"""Comprehensive tests for CloudHop MCP server.

Unit tests for all tools, resources, prompts, HTTP helpers,
edge cases, and configuration. Uses unittest.mock for isolation.
Integration tests are skipped when CloudHop is not running.
"""

from __future__ import annotations

import json
import os
import urllib.error
from http.cookiejar import Cookie
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(data, code=200):
    """Create a mock HTTP response with JSON body."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    resp.status = code
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_cookie(name, value):
    """Create a minimal Cookie object."""
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain="localhost",
        domain_specified=True,
        domain_initial_dot=False,
        path="/",
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
    )


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset module-level state before each test."""
    import cloudhop_mcp.server as srv

    srv._csrf_token = None
    yield
    srv._csrf_token = None


# ---------------------------------------------------------------------------
# 1. HTTP Helpers
# ---------------------------------------------------------------------------


class TestHTTPHelpers:
    """Tests for _get, _post, _ensure_csrf, _reset_csrf, _fmt."""

    def test_get_server_not_running(self):
        """_get returns clear error when server is not running."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_opener") as mock_opener:
            mock_opener.open.side_effect = urllib.error.URLError("Connection refused")
            result = srv._get("/api/status")

        assert result["ok"] is False
        assert "not running" in result["error"].lower() or "CloudHop" in result["error"]

    def test_get_returns_json(self):
        """_get parses JSON response correctly."""
        import cloudhop_mcp.server as srv

        expected = {"status": "idle", "pct": 0}
        with patch.object(srv, "_opener") as mock_opener:
            mock_opener.open.return_value = _mock_response(expected)
            result = srv._get("/api/status")

        assert result == expected

    def test_post_server_not_running(self):
        """_post returns clear error when CSRF fetch fails."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_opener") as mock_opener:
            mock_opener.open.side_effect = urllib.error.URLError("Connection refused")
            result = srv._post("/api/pause")

        assert result["ok"] is False
        assert "CloudHop" in result["error"]

    def test_ensure_csrf_fetches_token(self):
        """_ensure_csrf makes GET / and extracts csrf_token from cookies."""
        import cloudhop_mcp.server as srv

        cookie = _make_cookie("csrf_token", "abc123")
        with patch.object(srv, "_opener") as mock_opener:
            mock_opener.open.return_value = _mock_response({})
            srv._jar.set_cookie(cookie)
            token = srv._ensure_csrf()

        assert token == "abc123"
        srv._jar.clear()

    def test_ensure_csrf_caches_token(self):
        """Second call to _ensure_csrf does not make an HTTP request."""
        import cloudhop_mcp.server as srv

        srv._csrf_token = "cached_token"
        with patch.object(srv, "_opener") as mock_opener:
            token = srv._ensure_csrf()

        assert token == "cached_token"
        mock_opener.open.assert_not_called()

    def test_ensure_csrf_raises_if_no_cookie(self):
        """_ensure_csrf raises RuntimeError if no csrf_token cookie found."""
        import cloudhop_mcp.server as srv

        srv._jar.clear()
        with patch.object(srv, "_opener") as mock_opener:
            mock_opener.open.return_value = _mock_response({})
            with pytest.raises(RuntimeError, match="CSRF"):
                srv._ensure_csrf()

    def test_reset_csrf_clears_cache(self):
        """After reset, next _ensure_csrf makes a new request."""
        import cloudhop_mcp.server as srv

        srv._csrf_token = "old_token"
        srv._reset_csrf()
        assert srv._csrf_token is None

    def test_post_retries_on_403(self):
        """On 403, resets CSRF and retries once."""
        import cloudhop_mcp.server as srv

        srv._csrf_token = "initial_token"
        cookie = _make_cookie("csrf_token", "new_token")

        error_403 = urllib.error.HTTPError(
            url="http://localhost:8787/api/pause",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=None,
        )
        success_resp = _mock_response({"ok": True})

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First POST attempt -> 403
                raise error_403
            if call_count == 2:
                # Second call is _ensure_csrf after reset
                srv._jar.set_cookie(cookie)
                return _mock_response({})
            # Third call is the retry POST
            return success_resp

        with patch.object(srv, "_opener") as mock_opener:
            mock_opener.open.side_effect = side_effect
            result = srv._post("/api/pause")

        assert result == {"ok": True}
        srv._jar.clear()

    def test_post_does_not_retry_on_500(self):
        """On 500, does not retry."""
        import cloudhop_mcp.server as srv

        srv._csrf_token = "token"
        error_500 = urllib.error.HTTPError(
            url="http://localhost:8787/api/pause",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=None,
        )

        with patch.object(srv, "_opener") as mock_opener:
            mock_opener.open.side_effect = error_500
            result = srv._post("/api/pause")

        assert result["ok"] is False
        assert "500" in result["error"]

    def test_fmt_returns_json(self):
        """_fmt produces valid, indented JSON with unicode."""
        import cloudhop_mcp.server as srv

        data = {"name": "test", "unicode": "ăîșț"}
        result = srv._fmt(data)
        parsed = json.loads(result)
        assert parsed == data
        assert "ăîșț" in result  # ensure_ascii=False
        assert "\n" in result  # indented

    def test_post_sends_csrf_header(self):
        """_post includes X-CSRF-Token header in request."""
        import cloudhop_mcp.server as srv

        srv._csrf_token = "test_token"
        with patch.object(srv, "_opener") as mock_opener:
            mock_opener.open.return_value = _mock_response({"ok": True})
            srv._post("/api/pause", {"key": "value"})

        call_args = mock_opener.open.call_args
        req = call_args[0][0]
        assert req.get_header("X-csrf-token") == "test_token"

    def test_post_sends_json_content_type(self):
        """_post sets Content-Type to application/json."""
        import cloudhop_mcp.server as srv

        srv._csrf_token = "token"
        with patch.object(srv, "_opener") as mock_opener:
            mock_opener.open.return_value = _mock_response({"ok": True})
            srv._post("/api/test")

        req = mock_opener.open.call_args[0][0]
        assert req.get_header("Content-type") == "application/json"

    def test_get_custom_timeout(self):
        """_get passes custom timeout to opener."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_opener") as mock_opener:
            mock_opener.open.return_value = _mock_response({})
            srv._get("/api/status", timeout=60)

        _, kwargs = mock_opener.open.call_args
        assert kwargs["timeout"] == 60

    def test_post_custom_timeout(self):
        """_post passes custom timeout to opener."""
        import cloudhop_mcp.server as srv

        srv._csrf_token = "token"
        with patch.object(srv, "_opener") as mock_opener:
            mock_opener.open.return_value = _mock_response({"ok": True})
            srv._post("/api/test", timeout=90)

        _, kwargs = mock_opener.open.call_args
        assert kwargs["timeout"] == 90


# ---------------------------------------------------------------------------
# 2. Tool Tests
# ---------------------------------------------------------------------------


class TestListRemotes:
    """Tests for list_remotes tool."""

    def test_returns_remotes_list(self):
        """With mock /api/wizard/status, returns list of remotes."""
        import cloudhop_mcp.server as srv

        api_data = {
            "remotes": ["gdrive", "onedrive", "dropbox"],
            "rclone_installed": True,
            "home": "/Users/test",
        }
        with patch.object(srv, "_get", return_value=api_data):
            result = json.loads(srv.list_remotes())

        assert result["remotes"] == ["gdrive", "onedrive", "dropbox"]
        assert result["rclone_installed"] is True

    def test_handles_error(self):
        """When API returns error, passes it through."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_get", return_value={"ok": False, "error": "fail"}):
            result = json.loads(srv.list_remotes())

        assert result["ok"] is False


class TestBrowseRemote:
    """Tests for browse_remote tool."""

    def test_posts_remote_and_path(self):
        """browse_remote constructs full remote:path and sends as path."""
        import cloudhop_mcp.server as srv

        browse_data = {"ok": True, "folders": [{"name": "file.txt"}]}
        with patch.object(srv, "_post", return_value=browse_data) as mock_post:
            srv.browse_remote("gdrive", "Documents/Work")

        mock_post.assert_called_once_with(
            "/api/wizard/browse", {"path": "gdrive:Documents/Work"}
        )

    def test_empty_path_browses_root(self):
        """path='' browses remote root with trailing colon."""
        import cloudhop_mcp.server as srv

        remotes_data = {"remotes": ["onedrive"]}
        with (
            patch.object(
                srv, "_post", return_value={"ok": True, "folders": []}
            ) as mock_post,
            patch.object(srv, "_get", return_value=remotes_data),
        ):
            srv.browse_remote("onedrive", "")

        mock_post.assert_called_once_with("/api/wizard/browse", {"path": "onedrive:"})

    def test_returns_folders(self):
        """Returns parsed folders from API."""
        import cloudhop_mcp.server as srv

        folders = [
            {"name": "Photos", "path": "Photos"},
            {"name": "Documents", "path": "Documents"},
        ]
        with patch.object(srv, "_post", return_value={"ok": True, "folders": folders}):
            result = json.loads(srv.browse_remote("gdrive", ""))

        assert len(result["folders"]) == 2
        assert result["folders"][0]["name"] == "Photos"

    def test_invalid_remote_returns_error(self):
        """FM-07: browse_remote returns error for non-existent remotes."""
        import cloudhop_mcp.server as srv

        remotes_data = {"remotes": ["gdrive", "onedrive"]}
        with (
            patch.object(srv, "_post", return_value={"ok": True, "folders": []}),
            patch.object(srv, "_get", return_value=remotes_data),
        ):
            result = json.loads(srv.browse_remote("fakeremote999", ""))

        assert result["ok"] is False
        assert "not found" in result["error"].lower()


class TestPreviewTransfer:
    """Tests for preview_transfer tool."""

    def test_returns_file_count_and_size(self):
        """Preview returns count and total size."""
        import cloudhop_mcp.server as srv

        preview_data = {"count": 150, "size": 1073741824, "ok": True}
        with patch.object(srv, "_post", return_value=preview_data):
            result = json.loads(srv.preview_transfer("onedrive:"))

        assert result["count"] == 150
        assert result["size"] == 1073741824

    def test_timeout_90_seconds(self):
        """Preview uses 90s timeout, not default 30s."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_post", return_value={"ok": True}) as mock_post:
            srv.preview_transfer("gdrive:Documents")

        mock_post.assert_called_once_with(
            "/api/wizard/preview", {"source": "gdrive:Documents"}, timeout=90
        )


class TestStartTransfer:
    """Tests for start_transfer tool."""

    def _remotes(self, *names):
        return {"remotes": list(names)}

    def test_valid_copy_mode(self):
        """mode='copy' is accepted."""
        import cloudhop_mcp.server as srv

        with (
            patch.object(srv, "_get", return_value=self._remotes("dst")),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            result = json.loads(srv.start_transfer("src:", "dst:", mode="copy"))

        assert result["ok"] is True
        data = mock_post.call_args[0][1]
        assert data["mode"] == "copy"

    def test_valid_sync_mode(self):
        """mode='sync' is accepted."""
        import cloudhop_mcp.server as srv

        with (
            patch.object(srv, "_get", return_value=self._remotes("dst")),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            srv.start_transfer("src:", "dst:", mode="sync")

        data = mock_post.call_args[0][1]
        assert data["mode"] == "sync"

    def test_invalid_mode_rejected(self):
        """mode='invalid' returns error without calling API."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_post") as mock_post:
            result = json.loads(srv.start_transfer("src:", "dst:", mode="invalid"))

        assert result["ok"] is False
        assert "invalid" in result["error"].lower() or "Invalid" in result["error"]
        mock_post.assert_not_called()

    def test_excludes_split_by_comma(self):
        """'a,b,c' becomes ['a', 'b', 'c']."""
        import cloudhop_mcp.server as srv

        with (
            patch.object(srv, "_get", return_value=self._remotes("dst")),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            srv.start_transfer("src:", "dst:", excludes="node_modules,.git,*.tmp")

        data = mock_post.call_args[0][1]
        assert data["excludes"] == ["node_modules", ".git", "*.tmp"]

    def test_excludes_stripped(self):
        """Spaces around excludes are stripped."""
        import cloudhop_mcp.server as srv

        with (
            patch.object(srv, "_get", return_value=self._remotes("dst")),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            srv.start_transfer("src:", "dst:", excludes=" a , b , c ")

        data = mock_post.call_args[0][1]
        assert data["excludes"] == ["a", "b", "c"]

    def test_optional_params_not_sent_when_empty(self):
        """Empty excludes/bw_limit are not included in request."""
        import cloudhop_mcp.server as srv

        with (
            patch.object(srv, "_get", return_value=self._remotes("dst")),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            srv.start_transfer("src:", "dst:", excludes="", bw_limit="")

        data = mock_post.call_args[0][1]
        assert "excludes" not in data
        assert "bw_limit" not in data

    def test_bw_limit_sent_when_set(self):
        """Non-empty bw_limit is included in request."""
        import cloudhop_mcp.server as srv

        with (
            patch.object(srv, "_get", return_value=self._remotes("dst")),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            srv.start_transfer("src:", "dst:", bw_limit="10M")

        data = mock_post.call_args[0][1]
        assert data["bw_limit"] == "10M"

    def test_checksum_not_sent_when_false(self):
        """checksum=False (default) does not include checksum in request."""
        import cloudhop_mcp.server as srv

        with (
            patch.object(srv, "_get", return_value=self._remotes("dst")),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            srv.start_transfer("src:", "dst:", checksum=False)

        data = mock_post.call_args[0][1]
        assert "checksum" not in data

    def test_checksum_sent_when_true(self):
        """checksum=True includes checksum in request."""
        import cloudhop_mcp.server as srv

        with (
            patch.object(srv, "_get", return_value=self._remotes("dst")),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            srv.start_transfer("src:", "dst:", checksum=True)

        data = mock_post.call_args[0][1]
        assert data["checksum"] is True

    def test_default_transfers_8(self):
        """Default transfers value is 8."""
        import cloudhop_mcp.server as srv

        with (
            patch.object(srv, "_get", return_value=self._remotes("dst")),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            srv.start_transfer("src:", "dst:")

        data = mock_post.call_args[0][1]
        assert data["transfers"] == 8

    def test_custom_transfers(self):
        """Custom transfers value is passed through."""
        import cloudhop_mcp.server as srv

        with (
            patch.object(srv, "_get", return_value=self._remotes("dst")),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            srv.start_transfer("src:", "dst:", transfers=32)

        data = mock_post.call_args[0][1]
        assert data["transfers"] == 32

    def test_posts_to_wizard_start(self):
        """start_transfer posts to /api/wizard/start."""
        import cloudhop_mcp.server as srv

        with (
            patch.object(srv, "_get", return_value=self._remotes("dst")),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            srv.start_transfer("src:", "dst:")

        assert mock_post.call_args[0][0] == "/api/wizard/start"

    def test_dry_run_sent_when_true(self):
        """FM-11: dry_run=True includes dry_run in request."""
        import cloudhop_mcp.server as srv

        with (
            patch.object(srv, "_get", return_value=self._remotes("dst")),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            srv.start_transfer("src:", "dst:", dry_run=True)

        data = mock_post.call_args[0][1]
        assert data["dry_run"] is True

    def test_dry_run_not_sent_when_false(self):
        """dry_run=False (default) does not include dry_run in request."""
        import cloudhop_mcp.server as srv

        with (
            patch.object(srv, "_get", return_value=self._remotes("dst")),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            srv.start_transfer("src:", "dst:", dry_run=False)

        data = mock_post.call_args[0][1]
        assert "dry_run" not in data

    def test_invalid_remote_rejected(self):
        """FM-06: start_transfer rejects unknown remote."""
        import cloudhop_mcp.server as srv

        with patch.object(
            srv, "_get", return_value=self._remotes("gdrive", "onedrive")
        ):
            result = json.loads(srv.start_transfer("src:", "fakeremote:", mode="copy"))

        assert result["ok"] is False
        assert "not found" in result["error"].lower()

    def test_local_source_not_found(self):
        """FM-06: start_transfer rejects non-existent local source."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_get", return_value=self._remotes("dst")):
            result = json.loads(
                srv.start_transfer("/nonexistent/path/xyz", "dst:", mode="copy")
            )

        assert result["ok"] is False
        assert "does not exist" in result["error"].lower()


class TestTransferStatus:
    """Tests for transfer_status tool."""

    def test_returns_status(self):
        """Returns status dict."""
        import cloudhop_mcp.server as srv

        status_data = {"rclone_running": True, "pct": 45, "speed": "10MB/s"}
        with patch.object(srv, "_get", return_value=status_data):
            result = json.loads(srv.transfer_status())

        assert result["pct"] == 45
        assert result["speed"] == "10MB/s"

    def test_suggested_action_complete(self):
        """finished=True, rclone_running=False -> complete."""
        import cloudhop_mcp.server as srv

        with patch.object(
            srv,
            "_get",
            return_value={"finished": True, "rclone_running": False, "pct": 100},
        ):
            result = json.loads(srv.transfer_status())

        assert "complete" in result["suggested_action"].lower()

    def test_suggested_action_errors(self):
        """errors > 0 -> suggested_action mentions errors."""
        import cloudhop_mcp.server as srv

        with patch.object(
            srv,
            "_get",
            return_value={
                "finished": False,
                "rclone_running": True,
                "errors": 3,
                "pct": 50,
            },
        ):
            result = json.loads(srv.transfer_status())

        assert "error" in result["suggested_action"].lower()

    def test_suggested_action_paused(self):
        """not running, not finished, pct > 0 -> paused."""
        import cloudhop_mcp.server as srv

        with patch.object(
            srv,
            "_get",
            return_value={
                "finished": False,
                "rclone_running": False,
                "pct": 30,
            },
        ):
            result = json.loads(srv.transfer_status())

        assert "paused" in result["suggested_action"].lower()

    def test_suggested_action_in_progress(self):
        """rclone_running=True -> in progress."""
        import cloudhop_mcp.server as srv

        with patch.object(
            srv,
            "_get",
            return_value={
                "finished": False,
                "rclone_running": True,
                "errors": 0,
                "pct": 25,
            },
        ):
            result = json.loads(srv.transfer_status())

        assert "progress" in result["suggested_action"].lower()

    def test_suggested_action_idle(self):
        """No active transfer -> 'idle'."""
        import cloudhop_mcp.server as srv

        with patch.object(
            srv,
            "_get",
            return_value={
                "finished": False,
                "rclone_running": False,
                "pct": 0,
            },
        ):
            result = json.loads(srv.transfer_status())

        assert "idle" in result["suggested_action"].lower()

    def test_error_in_response_skips_suggested_action(self):
        """When response has 'error' key, no suggested_action added."""
        import cloudhop_mcp.server as srv

        with patch.object(
            srv, "_get", return_value={"ok": False, "error": "not running"}
        ):
            result = json.loads(srv.transfer_status())

        assert "suggested_action" not in result

    def test_errors_take_priority_over_progress(self):
        """Errors check comes before progress check."""
        import cloudhop_mcp.server as srv

        with patch.object(
            srv,
            "_get",
            return_value={
                "finished": False,
                "rclone_running": True,
                "errors": 5,
                "pct": 50,
            },
        ):
            result = json.loads(srv.transfer_status())

        assert "error" in result["suggested_action"].lower()


class TestPauseTransfer:
    """Tests for pause_transfer tool."""

    def test_calls_pause_endpoint(self):
        """pause_transfer posts to /api/pause."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_post", return_value={"ok": True}) as mock_post:
            result = json.loads(srv.pause_transfer())

        mock_post.assert_called_once_with("/api/pause")
        assert result["ok"] is True


class TestResumeTransfer:
    """Tests for resume_transfer tool."""

    def test_calls_resume_endpoint(self):
        """resume_transfer posts to /api/resume when transfer is paused."""
        import cloudhop_mcp.server as srv

        paused_status = {"rclone_running": False, "finished": True, "pct": 50}
        with (
            patch.object(srv, "_get", return_value=paused_status),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            result = json.loads(srv.resume_transfer())

        mock_post.assert_called_once_with("/api/resume")
        assert result["ok"] is True

    def test_blocks_resume_when_running(self):
        """FM-08: resume returns error when transfer is already running."""
        import cloudhop_mcp.server as srv

        running_status = {"rclone_running": True, "finished": False, "pct": 50}
        with patch.object(srv, "_get", return_value=running_status):
            result = json.loads(srv.resume_transfer())

        assert result["ok"] is False
        assert "already running" in result["error"].lower()

    def test_blocks_resume_when_completed(self):
        """FM-08: resume returns error when transfer is completed."""
        import cloudhop_mcp.server as srv

        completed_status = {"rclone_running": False, "finished": True, "pct": 100}
        with patch.object(srv, "_get", return_value=completed_status):
            result = json.loads(srv.resume_transfer())

        assert result["ok"] is False
        assert "no paused transfer" in result["error"].lower()


class TestStopTransfer:
    """Tests for stop_transfer tool."""

    def test_calls_pause_endpoint(self):
        """stop_transfer posts to /api/pause (same as pause)."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_post", return_value={"ok": True}) as mock_post:
            srv.stop_transfer()

        mock_post.assert_called_once_with("/api/pause")


class TestChangeSpeed:
    """Tests for change_speed tool."""

    def test_sends_rate_parameter(self):
        """change_speed('10M') sends {"rate": "10M"}."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_post", return_value={"ok": True}) as mock_post:
            srv.change_speed("10M")

        mock_post.assert_called_once_with("/api/bwlimit", {"rate": "10M"})

    def test_unlimited_rate(self):
        """change_speed('0') sends rate '0' for unlimited."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_post", return_value={"ok": True}) as mock_post:
            srv.change_speed("0")

        mock_post.assert_called_once_with("/api/bwlimit", {"rate": "0"})


class TestVerifyTransfer:
    """Tests for verify_transfer tool."""

    def test_calls_verify_endpoint(self):
        """verify_transfer posts to /api/verify."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_post", return_value={"ok": True}) as mock_post:
            srv.verify_transfer()

        mock_post.assert_called_once_with("/api/verify")


class TestErrorLog:
    """Tests for error_log tool."""

    def test_returns_log_content(self):
        """error_log gets /api/error-log."""
        import cloudhop_mcp.server as srv

        log_data = {"errors": ["file1: permission denied", "file2: not found"]}
        with patch.object(srv, "_get", return_value=log_data) as mock_get:
            result = json.loads(srv.error_log())

        mock_get.assert_called_once_with("/api/error-log")
        assert len(result["errors"]) == 2


class TestTransferHistory:
    """Tests for transfer_history tool."""

    def test_handles_list_response(self):
        """FM-03: transfer_history handles list response from API."""
        import cloudhop_mcp.server as srv

        history_list = [{"id": "abc123", "label": "Transfer", "sessions": 1}]
        with patch.object(srv, "_get", return_value=history_list) as mock_get:
            result = json.loads(srv.transfer_history())

        mock_get.assert_called_once_with("/api/history")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_handles_dict_response(self):
        """transfer_history handles dict response with history key."""
        import cloudhop_mcp.server as srv

        history_data = {"history": [{"id": "abc123", "label": "Transfer"}]}
        with patch.object(srv, "_get", return_value=history_data):
            result = json.loads(srv.transfer_history())

        assert isinstance(result, list)
        assert len(result) == 1

    def test_returns_empty_history(self):
        """Empty history returns empty list."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_get", return_value=[]):
            result = json.loads(srv.transfer_history())

        assert result == []

    def test_handles_error_response(self):
        """Error response is passed through."""
        import cloudhop_mcp.server as srv

        with patch.object(
            srv, "_get", return_value={"ok": False, "error": "not running"}
        ):
            result = json.loads(srv.transfer_history())

        assert result["ok"] is False


class TestServerHealth:
    """Tests for server_health tool."""

    def test_returns_health_info(self):
        """Returns rclone version and status."""
        import cloudhop_mcp.server as srv

        health_data = {"rclone": "/usr/local/bin/rclone", "version": "1.65.0"}
        with patch.object(srv, "_post", return_value=health_data):
            result = json.loads(srv.server_health())

        assert result["version"] == "1.65.0"

    def test_not_running_message(self):
        """Error message includes install instructions."""
        import cloudhop_mcp.server as srv

        with patch.object(
            srv, "_post", return_value={"ok": False, "error": srv._NOT_RUNNING}
        ):
            result = json.loads(srv.server_health())

        assert "pip install" in result["error"] or "Install" in result["error"]

    def test_calls_check_rclone(self):
        """server_health posts to /api/wizard/check-rclone."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_post", return_value={"ok": True}) as mock_post:
            srv.server_health()

        mock_post.assert_called_once_with("/api/wizard/check-rclone")


# ---------------------------------------------------------------------------
# 3. Resources
# ---------------------------------------------------------------------------


class TestResources:
    """Tests for MCP resources."""

    def test_remotes_resource(self):
        """cloudhop://remotes returns list of remotes."""
        import cloudhop_mcp.server as srv

        api_data = {"remotes": ["gdrive", "onedrive"]}
        with patch.object(srv, "_get", return_value=api_data):
            result = json.loads(srv.remotes_resource())

        assert result["remotes"] == ["gdrive", "onedrive"]

    def test_remotes_resource_error(self):
        """cloudhop://remotes passes through errors."""
        import cloudhop_mcp.server as srv

        with patch.object(
            srv, "_get", return_value={"error": "not running", "ok": False}
        ):
            result = json.loads(srv.remotes_resource())

        assert "error" in result

    def test_status_resource(self):
        """cloudhop://status returns current status."""
        import cloudhop_mcp.server as srv

        status_data = {"status": "idle", "pct": 0}
        with patch.object(srv, "_get", return_value=status_data):
            result = json.loads(srv.status_resource())

        assert result["status"] == "idle"

    def test_instructions_resource(self):
        """cloudhop://instructions returns usage guide text."""
        import cloudhop_mcp.server as srv

        text = srv.instructions_resource()
        assert "preview" in text.lower()
        assert "confirm" in text.lower()
        assert "sync" in text.lower()
        assert "WORKFLOW" in text
        assert "RULES" in text


# ---------------------------------------------------------------------------
# 4. Prompts
# ---------------------------------------------------------------------------


class TestPrompts:
    """Tests for MCP prompts."""

    def test_backup_prompt_contains_steps(self):
        """backup() returns prompt with workflow steps."""
        import cloudhop_mcp.server as srv

        text = srv.backup("gdrive:Photos", "onedrive:Backup")
        assert "server_health" in text
        assert "preview_transfer" in text
        assert "start_transfer" in text
        assert "verify_transfer" in text

    def test_backup_prompt_includes_source_dest(self):
        """Source and dest parameters appear in prompt text."""
        import cloudhop_mcp.server as srv

        text = srv.backup("gdrive:Photos", "dropbox:Archive")
        assert "gdrive:Photos" in text
        assert "dropbox:Archive" in text

    def test_backup_prompt_uses_copy_mode(self):
        """Backup prompt specifies copy mode."""
        import cloudhop_mcp.server as srv

        text = srv.backup("src:", "dst:")
        assert 'mode="copy"' in text

    def test_migrate_prompt_contains_warning(self):
        """migrate() contains warning about sync deletion."""
        import cloudhop_mcp.server as srv

        text = srv.migrate("gdrive:", "onedrive:")
        assert "DELETE" in text or "delete" in text

    def test_migrate_prompt_includes_source_dest(self):
        """Source and dest appear in migrate prompt."""
        import cloudhop_mcp.server as srv

        text = srv.migrate("gdrive:Data", "onedrive:Mirror")
        assert "gdrive:Data" in text
        assert "onedrive:Mirror" in text

    def test_migrate_prompt_uses_sync_mode(self):
        """Migrate prompt specifies sync mode."""
        import cloudhop_mcp.server as srv

        text = srv.migrate("src:", "dst:")
        assert 'mode="sync"' in text


# ---------------------------------------------------------------------------
# 5. Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case tests for robustness."""

    def test_browse_remote_special_chars_in_path(self):
        """Path with spaces and diacritics is passed through."""
        import cloudhop_mcp.server as srv

        with patch.object(
            srv, "_post", return_value={"ok": True, "folders": [{"name": "f"}]}
        ) as mock_post:
            srv.browse_remote("gdrive", "My Folder/Documente și fișiere")

        data = mock_post.call_args[0][1]
        assert data["path"] == "gdrive:My Folder/Documente și fișiere"

    def test_start_transfer_empty_source(self):
        """FM-06: Empty local source returns validation error."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_get", return_value={"remotes": ["dst"]}):
            result = json.loads(srv.start_transfer("", "dst:"))

        assert result["ok"] is False
        assert "does not exist" in result["error"].lower()

    def test_start_transfer_empty_dest(self):
        """Empty dest is sent to API (server validates)."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_post", return_value={"ok": True}) as mock_post:
            srv.start_transfer("src:", "")

        data = mock_post.call_args[0][1]
        assert data["dest"] == ""

    def test_change_speed_invalid_value(self):
        """Invalid limit value is sent to API (server decides)."""
        import cloudhop_mcp.server as srv

        with patch.object(srv, "_post", return_value={"ok": True}) as mock_post:
            srv.change_speed("abc")

        mock_post.assert_called_once_with("/api/bwlimit", {"rate": "abc"})

    def test_browse_very_long_path(self):
        """Very long path (>500 chars) is passed through."""
        import cloudhop_mcp.server as srv

        long_path = "a" * 600
        with patch.object(
            srv, "_post", return_value={"ok": True, "folders": [{"name": "f"}]}
        ) as mock_post:
            srv.browse_remote("gdrive", long_path)

        data = mock_post.call_args[0][1]
        assert data["path"] == f"gdrive:{long_path}"

    def test_start_transfer_all_params(self):
        """All optional params set at once."""
        import cloudhop_mcp.server as srv

        with (
            patch.object(srv, "_get", return_value={"remotes": ["onedrive"]}),
            patch.object(srv, "_post", return_value={"ok": True}) as mock_post,
        ):
            srv.start_transfer(
                source="gdrive:Photos",
                dest="onedrive:Backup",
                transfers=16,
                excludes=".git,*.tmp",
                bw_limit="50M",
                checksum=True,
                mode="sync",
                dry_run=True,
            )

        data = mock_post.call_args[0][1]
        assert data["source"] == "gdrive:Photos"
        assert data["dest"] == "onedrive:Backup"
        assert data["transfers"] == 16
        assert data["excludes"] == [".git", "*.tmp"]
        assert data["bw_limit"] == "50M"
        assert data["checksum"] is True
        assert data["mode"] == "sync"
        assert data["dry_run"] is True

    def test_fmt_handles_nested_data(self):
        """_fmt handles nested dicts and lists."""
        import cloudhop_mcp.server as srv

        data = {"a": [1, 2, {"b": "c"}]}
        result = srv._fmt(data)
        assert json.loads(result) == data

    def test_fmt_handles_empty_dict(self):
        """_fmt handles empty dict."""
        import cloudhop_mcp.server as srv

        assert json.loads(srv._fmt({})) == {}

    def test_fmt_handles_none_values(self):
        """_fmt handles None values."""
        import cloudhop_mcp.server as srv

        data = {"key": None}
        assert json.loads(srv._fmt(data)) == data

    def test_post_empty_data(self):
        """_post with data=None sends empty JSON object."""
        import cloudhop_mcp.server as srv

        srv._csrf_token = "token"
        with patch.object(srv, "_opener") as mock_opener:
            mock_opener.open.return_value = _mock_response({"ok": True})
            srv._post("/api/pause", None)

        req = mock_opener.open.call_args[0][0]
        assert req.data == b"{}"


# ---------------------------------------------------------------------------
# 6. Configuration
# ---------------------------------------------------------------------------


class TestConfiguration:
    """Tests for server configuration."""

    def test_default_port(self):
        """PORT default is 8787."""
        import cloudhop_mcp.server as srv

        # PORT is set at import time, so test the current value
        # Since CLOUDHOP_PORT env var is likely not set, default should be 8787
        assert srv.PORT == int(os.environ.get("CLOUDHOP_PORT", "8787"))

    def test_base_url_uses_port(self):
        """BASE includes the port."""
        import cloudhop_mcp.server as srv

        assert str(srv.PORT) in srv.BASE
        assert srv.BASE == f"http://localhost:{srv.PORT}"

    def test_host_returns_correct_value(self):
        """_host() returns localhost:PORT."""
        import cloudhop_mcp.server as srv

        assert srv._host() == f"localhost:{srv.PORT}"

    def test_not_running_message_includes_port(self):
        """_NOT_RUNNING message includes the configured port."""
        import cloudhop_mcp.server as srv

        assert str(srv.PORT) in srv._NOT_RUNNING

    def test_not_running_message_includes_install_steps(self):
        """_NOT_RUNNING includes pip install and cloudhop commands."""
        import cloudhop_mcp.server as srv

        assert "pip install cloudhop" in srv._NOT_RUNNING
        assert "cloudhop" in srv._NOT_RUNNING


# ---------------------------------------------------------------------------
# 7. Integration Tests (skip if CloudHop not running)
# ---------------------------------------------------------------------------


def _cloudhop_running():
    """Check if CloudHop is running on localhost."""
    try:
        import urllib.request as req

        req.urlopen("http://localhost:8787/", timeout=2)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _cloudhop_running(), reason="CloudHop not running")
class TestIntegration:
    """Integration tests that require a running CloudHop instance."""

    def test_list_remotes_real(self):
        """list_remotes returns real data."""
        import cloudhop_mcp.server as srv

        result = json.loads(srv.list_remotes())
        assert "remotes" in result or "error" in result

    def test_server_health_real(self):
        """server_health returns version info."""
        import cloudhop_mcp.server as srv

        result = json.loads(srv.server_health())
        # Should have rclone info or an error
        assert isinstance(result, dict)

    def test_transfer_status_real(self):
        """transfer_status returns status (idle or active)."""
        import cloudhop_mcp.server as srv

        result = json.loads(srv.transfer_status())
        assert "suggested_action" in result or "error" in result

    def test_transfer_history_real(self):
        """transfer_history returns data (list or dict)."""
        import cloudhop_mcp.server as srv

        result = json.loads(srv.transfer_history())
        assert isinstance(result, (list, dict))

    def test_error_log_real(self):
        """error_log returns something (empty list OK)."""
        import cloudhop_mcp.server as srv

        result = json.loads(srv.error_log())
        assert isinstance(result, dict)

    def test_instructions_resource_real(self):
        """instructions_resource returns non-empty text."""
        import cloudhop_mcp.server as srv

        text = srv.instructions_resource()
        assert len(text) > 100
