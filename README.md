# CloudHop MCP Server

MCP server that lets Claude control [CloudHop](https://github.com/husamsoboh-cyber/cloudhop) file transfers. Ask Claude to move files between cloud services and it handles the rest.

## What you can do

```
You: what cloud accounts do I have?
Claude: You have OneDrive (personal), Google Drive (work), and Dropbox.

You: copy everything from OneDrive to Google Drive, skip node_modules
Claude: Started transfer: 1,247 files, 12.3 GB. Excluding node_modules.

You: how's it going?
Claude: 45% done, 5.6 GB transferred, 23 MB/s, ETA ~5 minutes.

You: pause it
Claude: Transfer paused.
```

## Available tools

| Tool | What it does |
|---|---|
| `list_remotes` | Show configured cloud accounts |
| `browse_remote` | Browse files and folders on a remote |
| `preview_transfer` | Show file count and size before starting |
| `start_transfer` | Start a transfer with options (excludes, bandwidth limit, checksums) |
| `transfer_status` | Get progress, speed, ETA, active files, errors |
| `pause_transfer` | Pause the running transfer |
| `resume_transfer` | Resume a paused transfer |
| `transfer_history` | View past transfers |

## Requirements

- [CloudHop](https://github.com/husamsoboh-cyber/cloudhop) running (`cloudhop` command or the app)
- Python 3.10+

## Install

```bash
pip install cloudhop-mcp
```

Or from source:

```bash
git clone https://github.com/husamsoboh-cyber/cloudhop-mcp
cd cloudhop-mcp
pip install -e .
```

## Setup with Claude Code

Add to `~/.claude.json` under `mcpServers`:

```json
{
  "cloudhop": {
    "type": "stdio",
    "command": "cloudhop-mcp",
    "args": [],
    "env": {}
  }
}
```

## Setup with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

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

## Configuration

If CloudHop runs on a non-default port, set the environment variable:

```json
{
  "cloudhop": {
    "type": "stdio",
    "command": "cloudhop-mcp",
    "args": [],
    "env": {
      "CLOUDHOP_PORT": "8788"
    }
  }
}
```

## How it works

CloudHop MCP is a thin wrapper around CloudHop's localhost HTTP API. It doesn't modify CloudHop or add dependencies to it. CloudHop stays simple, offline, and free. The MCP server just translates Claude's requests into HTTP calls to `localhost:8787`.

```
Claude --> MCP Server --> HTTP --> CloudHop --> rclone --> Cloud Providers
```

## License

MIT
