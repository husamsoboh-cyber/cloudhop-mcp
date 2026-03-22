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

_NOT_RUNNING = (
    f"CloudHop is not running. To start it:\n"
    f"  1. Install: pip install cloudhop\n"
    f"  2. Run: cloudhop --port {PORT}\n"
    f"  3. Or launch the CloudHop.app if installed\n"
    f"Then try again."
)

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
        req = urllib.request.Request(f"{BASE}{path}", headers={"Host": _host()})
        with _opener.open(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.URLError:
        return {"ok": False, "error": _NOT_RUNNING}


def _post(path: str, data: dict | None = None, timeout: int = 30) -> dict:
    for attempt in range(2):
        try:
            token = _ensure_csrf()
        except Exception:
            return {"ok": False, "error": _NOT_RUNNING}

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
        except urllib.error.URLError:
            return {"ok": False, "error": _NOT_RUNNING}
    return {"ok": False, "error": "Request failed after retry"}


def _fmt(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("cloudhop://instructions")
def instructions_resource() -> str:
    """How to use CloudHop tools effectively."""
    return """
CloudHop MCP - Usage Guide for Claude

WORKFLOW:
1. Always call server_health() first to check if CloudHop is running
2. Call list_remotes() to discover available cloud accounts
3. Use browse_remote() to explore folder contents
4. ALWAYS call preview_transfer() before start_transfer() - show the user file count and size
5. Ask user confirmation before starting any transfer
6. After starting, poll transfer_status() every 10-15 seconds to monitor progress
7. When status shows "Completed", inform the user with final stats

RULES:
- NEVER start a sync mode transfer without explicit user confirmation - sync DELETES files
- If transfer_status() shows errors > 0, call error_log() and inform the user
- If the user asks to slow down a transfer, use change_speed()
- If the user says "stop" or "cancel", use stop_transfer() not pause_transfer()
- Pause is for temporary stops (user wants to resume later)
- Stop is for permanent cancellation

FORMATTING:
- Show file sizes in human-readable format (MB, GB)
- Show transfer speeds in MB/s
- Show ETAs in minutes/hours, not seconds
- When listing remotes, explain what each type is (Google Drive, OneDrive, etc.)
"""


@mcp.resource("cloudhop://remotes")
def remotes_resource() -> str:
    """Currently configured cloud storage accounts."""
    resp = _get("/api/wizard/status")
    if "error" in resp:
        return _fmt(resp)
    return _fmt({"remotes": resp.get("remotes", [])})


@mcp.resource("cloudhop://status")
def status_resource() -> str:
    """Current transfer status if any transfer is active."""
    return _fmt(_get("/api/status"))


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def server_health() -> str:
    """Check if CloudHop is running and ready. Call this first before any
    other tool. Returns rclone installation status and path.
    """
    return _fmt(_post("/api/wizard/check-rclone"))


@mcp.tool()
def list_remotes() -> str:
    """List configured cloud storage accounts in CloudHop.

    Call server_health() first to ensure CloudHop is running.

    Returns remote names (e.g. gdrive, onedrive, dropbox),
    whether rclone is installed, and the user's home directory.
    """
    return _fmt(_get("/api/wizard/status"))


@mcp.tool()
def browse_remote(remote: str, path: str = "") -> str:
    """Browse files and folders on a cloud storage remote.

    Call list_remotes() first to discover available remote names.

    Args:
        remote: Remote name, e.g. "onedrive" or "gdrive"
        path: Folder path within the remote (empty for root)
    """
    return _fmt(_post("/api/wizard/browse", {"remote": remote, "path": path}))


@mcp.tool()
def preview_transfer(source: str) -> str:
    """Preview a transfer before starting - shows file count and total size.

    Always call this before start_transfer(). Show the preview to the user
    and ask for confirmation before proceeding.

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
    mode: str = "copy",
) -> str:
    """Start a file transfer between two cloud storage locations.

    PREREQUISITE: Always call preview_transfer() first and get user
    confirmation before starting.
    AFTER: Poll transfer_status() every 10-15 seconds to monitor progress.

    Two modes:
    - copy (default): only adds new/changed files to the destination.
    - sync: makes the destination identical to the source. WARNING: sync
      DELETES files at the destination that don't exist in the source.

    Args:
        source: Source path, e.g. "onedrive:" or "gdrive:Photos"
        dest: Destination path, e.g. "gdrive:Backup" or "dropbox:Archive"
        transfers: Number of parallel file transfers (1-128, default 8)
        excludes: Comma-separated patterns to skip, e.g. "node_modules,.git,*.tmp"
        bw_limit: Bandwidth limit, e.g. "10M" or "1G" (empty = unlimited)
        checksum: Verify files with checksums after transfer
        mode: "copy" (add new/changed files) or "sync" (mirror source, deletes extras)
    """
    if mode not in ("copy", "sync"):
        return _fmt(
            {"ok": False, "error": f"Invalid mode '{mode}'. Use 'copy' or 'sync'."}
        )
    data: dict = {
        "source": source,
        "dest": dest,
        "transfers": transfers,
        "mode": mode,
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
    """Get current transfer progress. Call this after starting a transfer
    to monitor speed, ETA, percentage, active files, and errors.

    Check the 'suggested_action' field in the response for what to do next.
    """
    resp = _get("/api/status")

    if "error" not in resp:
        status = resp.get("status", "")
        errors = resp.get("errors", 0)
        pct = resp.get("pct", 0)

        if status in ("Complete", "Completed"):
            resp["suggested_action"] = "transfer_complete - inform user of final stats"
        elif errors and errors > 0:
            resp["suggested_action"] = "has_errors - call error_log() to investigate"
        elif status in ("Stopped", "Paused"):
            resp["suggested_action"] = (
                "paused - ask user if they want to resume or cancel"
            )
        elif status == "Transferring" or (pct and pct > 0):
            resp["suggested_action"] = "in_progress - wait 10-15s then poll again"
        else:
            resp["suggested_action"] = "idle - no active transfer"

    return _fmt(resp)


@mcp.tool()
def pause_transfer() -> str:
    """Pause the currently running transfer. Only works when a transfer
    is active. The transfer can be resumed later with resume_transfer().

    For permanent cancellation, use stop_transfer() instead.
    """
    return _fmt(_post("/api/pause"))


@mcp.tool()
def resume_transfer() -> str:
    """Resume a paused transfer. Only works when a transfer has been paused."""
    return _fmt(_post("/api/resume"))


@mcp.tool()
def stop_transfer() -> str:
    """Permanently cancel the current transfer. Use this when the user wants
    to abort completely. For temporary pauses (user wants to resume later),
    use pause_transfer() instead.
    """
    return _fmt(_post("/api/pause"))


@mcp.tool()
def change_speed(limit: str) -> str:
    """Change bandwidth limit on the currently running transfer.

    Examples: change_speed("10M"), change_speed("1G"), change_speed("0") for unlimited.
    Only works while a transfer is actively running.

    Args:
        limit: Speed limit like "5M" (5 MB/s), "500K" (500 KB/s), "0" for unlimited
    """
    return _fmt(_post("/api/bwlimit", {"rate": limit}))


@mcp.tool()
def verify_transfer() -> str:
    """Verify integrity of a completed transfer by comparing source and
    destination checksums. Only works after a transfer has finished.
    Call this when the user wants to make sure all files were copied correctly.
    """
    return _fmt(_post("/api/verify"))


@mcp.tool()
def error_log() -> str:
    """View error log for the current or most recent transfer. Call this
    when transfer_status() shows errors > 0, or when the user reports
    a problem with the transfer.
    """
    return _fmt(_get("/api/error-log"))


@mcp.tool()
def transfer_history() -> str:
    """View past transfer history with details of previous runs."""
    return _fmt(_get("/api/history"))


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@mcp.prompt()
def backup(source: str, dest: str) -> str:
    """Create a backup transfer plan from source to destination."""
    return f"""Help me backup files from {source} to {dest}.

Steps:
1. Check server_health()
2. Preview the transfer with preview_transfer("{source}")
3. Show me the file count and total size
4. Ask me to confirm before starting
5. Start with start_transfer("{source}", "{dest}", mode="copy")
6. Monitor progress until complete
7. Run verify_transfer() to confirm integrity"""


@mcp.prompt()
def migrate(source: str, dest: str) -> str:
    """Plan a full migration (sync) between cloud accounts."""
    return f"""Help me migrate files from {source} to {dest} using sync mode.

WARNING: Sync mode will DELETE files at {dest} that don't exist in {source}.

Steps:
1. Check server_health()
2. Preview with preview_transfer("{source}")
3. Show me what will be transferred AND warn about deletion risk
4. Ask me to explicitly confirm I understand sync deletes files
5. Start with start_transfer("{source}", "{dest}", mode="sync")
6. Monitor progress until complete
7. Verify with verify_transfer()"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
