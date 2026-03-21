"""CloudHop MCP Server - control cloud file transfers from Claude.

Wraps CloudHop's localhost HTTP API so Claude can list remotes,
start/pause/resume transfers, check progress, and view history.

CloudHop must be running (``cloudhop`` or the .app) before using these tools.
Set CLOUDHOP_PORT env var if CloudHop runs on a non-default port.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP("CloudHop")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PORT = int(os.environ.get("CLOUDHOP_PORT", "8787"))
BASE = f"http://localhost:{PORT}"

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_csrf_token: str | None = None
_jar = CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))


def _host() -> str:
    return f"localhost:{PORT}"


def _ensure_csrf() -> str:
    global _csrf_token
    if _csrf_token:
        return _csrf_token
    req = urllib.request.Request(f"{BASE}/", headers={"Host": _host()})
    _opener.open(req, timeout=5)
    for cookie in _jar:
        if cookie.name == "csrf_token":
            _csrf_token = cookie.value
            return _csrf_token
    raise RuntimeError("CloudHop did not return a CSRF token")


def _reset_csrf() -> None:
    global _csrf_token
    _csrf_token = None


def _get(path: str, timeout: int = 10) -> dict:
    try:
        req = urllib.request.Request(
            f"{BASE}{path}", headers={"Host": _host()}
        )
        with _opener.open(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"CloudHop not reachable on port {PORT}: {e.reason}"}


def _post(path: str, data: dict | None = None, timeout: int = 30) -> dict:
    for attempt in range(2):
        try:
            token = _ensure_csrf()
        except Exception as e:
            return {"ok": False, "error": f"Cannot connect to CloudHop: {e}"}

        body = json.dumps(data or {}).encode()
        req = urllib.request.Request(
            f"{BASE}{path}",
            data=body,
            headers={
                "Host": _host(),
                "Content-Type": "application/json",
                "X-CSRF-Token": token,
            },
        )
        try:
            with _opener.open(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 403 and attempt == 0:
                _reset_csrf()
                continue
            return {"ok": False, "error": f"HTTP {e.code}: {e.reason}"}
        except urllib.error.URLError as e:
            return {"ok": False, "error": f"CloudHop not reachable: {e.reason}"}
    return {"ok": False, "error": "Request failed after retry"}


def _fmt(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_remotes() -> str:
    """List configured cloud storage accounts in CloudHop.

    Returns remote names (e.g. gdrive, onedrive, dropbox),
    whether rclone is installed, and the user's home directory.
    """
    return _fmt(_get("/api/wizard/status"))


@mcp.tool()
def browse_remote(remote: str, path: str = "") -> str:
    """Browse files and folders on a cloud storage remote.

    Args:
        remote: Remote name, e.g. "onedrive" or "gdrive"
        path: Folder path within the remote (empty for root)
    """
    return _fmt(_post("/api/wizard/browse", {"remote": remote, "path": path}))


@mcp.tool()
def preview_transfer(source: str) -> str:
    """Preview a transfer before starting - shows file count and total size.

    Use this to check how big a transfer will be before committing.

    Args:
        source: Source path to scan, e.g. "onedrive:" or "gdrive:Documents"
    """
    return _fmt(_post("/api/wizard/preview", {"source": source}, timeout=90))


@mcp.tool()
def start_transfer(
    source: str,
    dest: str,
    transfers: int = 8,
    excludes: str = "",
    bw_limit: str = "",
    checksum: bool = False,
) -> str:
    """Start a file transfer between two cloud storage locations.

    Args:
        source: Source path, e.g. "onedrive:" or "gdrive:Photos"
        dest: Destination path, e.g. "gdrive:Backup" or "dropbox:Archive"
        transfers: Number of parallel file transfers (1-128, default 8)
        excludes: Comma-separated patterns to skip, e.g. "node_modules,.git,*.tmp"
        bw_limit: Bandwidth limit, e.g. "10M" or "1G" (empty = unlimited)
        checksum: Verify files with checksums after transfer
    """
    data: dict = {
        "source": source,
        "dest": dest,
        "transfers": transfers,
    }
    if excludes:
        data["excludes"] = [e.strip() for e in excludes.split(",")]
    if bw_limit:
        data["bw_limit"] = bw_limit
    if checksum:
        data["checksum"] = True
    return _fmt(_post("/api/wizard/start", data))


@mcp.tool()
def transfer_status() -> str:
    """Get current transfer progress.

    Returns speed, bytes transferred, ETA, percentage,
    active files, recent files, and any errors.
    """
    return _fmt(_get("/api/status"))


@mcp.tool()
def pause_transfer() -> str:
    """Pause the currently running transfer."""
    return _fmt(_post("/api/pause"))


@mcp.tool()
def resume_transfer() -> str:
    """Resume a paused transfer."""
    return _fmt(_post("/api/resume"))


@mcp.tool()
def transfer_history() -> str:
    """View past transfer history with details of previous runs."""
    return _fmt(_get("/api/history"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
