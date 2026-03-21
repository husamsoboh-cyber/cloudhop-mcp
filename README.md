[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/cloudhop-mcp.svg)](https://pypi.org/project/cloudhop-mcp/)

# CloudHop MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that connects Claude to [CloudHop](https://github.com/husamsoboh-cyber/cloudhop), letting you control cloud file transfers through natural language.

Instead of clicking through a wizard, you just tell Claude what to move and where.

## What is this?

**CloudHop** is a free, open-source app that copies files between cloud storage services (Google Drive, OneDrive, Dropbox, etc.) using [rclone](https://rclone.org/) as its engine.

**CloudHop MCP** is a bridge that lets Claude talk to CloudHop. It translates your requests into API calls so Claude can start transfers, check progress, pause/resume, and more.

```
You (natural language)
  --> Claude (understands intent)
    --> CloudHop MCP (translates to API calls)
      --> CloudHop (runs on localhost:8787)
        --> rclone (moves the files)
          --> Cloud providers (Google Drive, OneDrive, etc.)
```

The MCP server is a separate package. It does not modify CloudHop or add any dependencies to it. CloudHop stays exactly as it is: simple, offline, free.

## Example conversation

```
You: what cloud accounts do I have in CloudHop?
Claude: You have 3 accounts configured:
  - onedrive (OneDrive)
  - gdrive (Google Drive)
  - dropbox (Dropbox)

You: how big is my OneDrive?
Claude: Scanning... Your OneDrive has 1,247 files totaling 12.3 GB.

You: copy everything to Google Drive, but skip node_modules and .git folders
Claude: Transfer started: OneDrive -> Google Drive
  1,247 files, 12.3 GB, excluding node_modules and .git
  Using 8 parallel transfers, no bandwidth limit.

You: how's it going?
Claude: 45% complete - 5.6 GB transferred at 23 MB/s. ETA ~5 minutes.
  Currently transferring: Photos/vacation-2024/IMG_4521.jpg

You: slow it down to 10 megabytes per second
Claude: I can't change bandwidth on a running transfer, but you can
  pause and restart with a limit. Want me to do that?

You: yeah do it
Claude: Paused. Restarting with 10M bandwidth limit... Transfer resumed.

You: show me my transfer history
Claude: You have 3 past transfers:
  1. OneDrive -> Google Drive (12.3 GB, completed Mar 21)
  2. Dropbox -> OneDrive (3.1 GB, completed Mar 19)
  3. Local -> Google Drive (890 MB, completed Mar 18)
```

## Available tools

The MCP server exposes 8 tools that Claude can call:

### list_remotes

Lists all cloud storage accounts configured in CloudHop.

**Returns:** Remote names (e.g. `gdrive`, `onedrive`), whether rclone is installed, home directory path.

---

### browse_remote

Browse files and folders on a cloud storage account.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `remote` | string | yes | Remote name, e.g. `onedrive` or `gdrive` |
| `path` | string | no | Folder path within the remote (empty = root) |

---

### preview_transfer

Scan a source to see how many files and how much data will be transferred, before actually starting.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `source` | string | yes | Source path, e.g. `onedrive:` or `gdrive:Documents` |

**Returns:** File count and total size. Times out after 90 seconds for very large sources.

---

### start_transfer

Start copying files from one cloud to another.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `source` | string | yes | | Source path, e.g. `onedrive:` or `gdrive:Photos` |
| `dest` | string | yes | | Destination path, e.g. `gdrive:Backup` or `dropbox:Archive` |
| `transfers` | int | no | 8 | Parallel file transfers (1-128) |
| `excludes` | string | no | `""` | Comma-separated patterns to skip, e.g. `node_modules,.git,*.tmp` |
| `bw_limit` | string | no | `""` | Bandwidth limit, e.g. `10M`, `1G`, `500K` (empty = unlimited) |
| `checksum` | bool | no | `false` | Verify files with checksums after transfer |

**Returns:** Success/failure message. Fails if a transfer is already running.

---

### transfer_status

Get real-time progress of the current transfer.

**Returns:** Speed (bytes/sec), bytes transferred, total bytes, ETA, completion percentage, list of files currently being transferred, recently completed files, and any error messages.

---

### pause_transfer

Pause the currently running transfer. The transfer can be resumed later from where it stopped.

---

### resume_transfer

Resume a previously paused transfer.

---

### transfer_history

View all past transfers with their details (source, destination, file count, size).

---

## Prerequisites

1. **CloudHop** installed and running. Install it with any of these methods:

   ```bash
   pip install cloudhop    # PyPI
   brew install cloudhop   # Homebrew (macOS)
   ```

   Or download from [CloudHop Releases](https://github.com/husamsoboh-cyber/cloudhop/releases).

2. **Python 3.10+** (the MCP SDK requires it).

3. **At least one cloud account** configured in CloudHop. Run `cloudhop` and use the wizard to connect your cloud providers.

## Installation

### From PyPI

```bash
pip install cloudhop-mcp
```

### From source

```bash
git clone https://github.com/husamsoboh-cyber/cloudhop-mcp
cd cloudhop-mcp
pip install -e .
```

### Verify installation

```bash
cloudhop-mcp --help
```

Or test that it imports correctly:

```bash
python -c "from cloudhop_mcp.server import mcp; print('OK')"
```

## Setup

### Claude Code

Add the following to your `~/.claude.json` file, inside the `mcpServers` object:

```json
"cloudhop": {
  "type": "stdio",
  "command": "cloudhop-mcp",
  "args": [],
  "env": {}
}
```

Then restart Claude Code. You should see the CloudHop tools available.

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "cloudhop": {
      "command": "cloudhop-mcp",
      "args": []
    }
  }
}
```

Then restart Claude Desktop.

### Custom port

CloudHop defaults to port 8787. If it uses a different port (8788-8791 when 8787 is busy), set the `CLOUDHOP_PORT` environment variable:

```json
"cloudhop": {
  "type": "stdio",
  "command": "cloudhop-mcp",
  "args": [],
  "env": {
    "CLOUDHOP_PORT": "8788"
  }
}
```

## How it works

```
Claude                CloudHop MCP           CloudHop             rclone
  |                      |                     |                    |
  |-- "list accounts" -->|                     |                    |
  |                      |-- GET /api/wizard/status -->|            |
  |                      |<-- {remotes: [...]} --|                  |
  |<-- "You have 3..." --|                     |                    |
  |                      |                     |                    |
  |-- "copy OneDrive     |                     |                    |
  |    to GDrive" ------>|                     |                    |
  |                      |-- POST /api/wizard/start -->|            |
  |                      |   {source, dest, ...}      |            |
  |                      |                     |-- rclone copy ---->|
  |                      |<-- {ok: true} ------|                    |
  |<-- "Transfer started"|                     |                    |
  |                      |                     |                    |
  |-- "progress?" ------>|                     |                    |
  |                      |-- GET /api/status -->|                   |
  |                      |<-- {speed, eta, ...}|                    |
  |<-- "45% done, 5m" --|                     |                    |
```

### Security

- CloudHop only binds to `localhost`. The MCP server connects to `127.0.0.1` only.
- All POST requests use CSRF tokens (automatically handled by the MCP server).
- No data leaves your machine. Files transfer directly between cloud providers.
- The MCP server has zero access to your cloud credentials. It only tells CloudHop what to do; CloudHop and rclone handle authentication.

### What the MCP server does NOT do

- It does not store any credentials or tokens
- It does not modify CloudHop's code or configuration
- It does not communicate with any external server
- It does not run rclone directly (CloudHop does that)
- It does not work without CloudHop running

## Troubleshooting

### "CloudHop not reachable on port 8787"

CloudHop is not running. Start it first:

```bash
cloudhop
```

Or launch the CloudHop app.

### "Could not obtain CSRF token"

CloudHop is running but something is blocking the connection. Check that nothing else is using port 8787:

```bash
lsof -i :8787
```

### Tools don't appear in Claude

1. Make sure the config is in the right file (`~/.claude.json` for Claude Code)
2. Restart Claude Code / Claude Desktop after changing the config
3. Check that `cloudhop-mcp` is on your PATH: `which cloudhop-mcp`

### Transfer fails to start

- Check that you have at least one remote configured: run `cloudhop` and use the wizard
- Make sure no other transfer is already running (use `transfer_status` to check)

## Links

- [CloudHop](https://github.com/husamsoboh-cyber/cloudhop) - the main application
- [MCP Documentation](https://modelcontextprotocol.io/) - Model Context Protocol specification
- [rclone](https://rclone.org/) - the file transfer engine CloudHop uses

## License

MIT License - see [LICENSE](LICENSE) for details.
