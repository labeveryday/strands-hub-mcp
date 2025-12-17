# strands-hub-mcp

Local **MCP server** that exposes data managed by [`strands-hub`](https://github.com/labeveryday/strands-hub) from **S3**:

- **Agent registry**: `registry.json`
- **System prompts**: `system_prompts/<agent_id>/*`
- **Sessions**: `sessions/*` (**read-only**)
- **Metrics**: `metrics/YYYY-MM-DD/*` (**read-only**)

This server **does not hardcode your S3 bucket**. It uses the same environment variables as `strands-hub` (`HubConfig`).

## Prereqs / assumptions

- **`strands-hub` installed**: this server uses `strands-hub`'s `HubConfig` and helpers (registry + prompts).
- **S3 is the source of truth**: `USE_S3=true` is required (this server currently does not run in local-only mode).
- **AWS credentials**: `boto3` must be able to read your AWS creds (e.g., `~/.aws/credentials`, env vars, or IAM role).
- **S3 layout matches `strands-hub` defaults** (unless you've customized config):
  - `registry.json`
  - `metrics/YYYY-MM-DD/<run_id>.json`
  - `system_prompts/<agent_id>/{current.txt, vN.txt, versions.json}`
  - `sessions/session_<...>/agents/<agent_id>/messages/message_N.json` (plus `session.json`, `agent.json`)
- **Local `.agent_hub/` is optional**: it's only used for local prompt caching; it should never be committed (see `.gitignore`).

## Requirements

- Python 3.10+
- AWS credentials available to `boto3`

## Configure

Set the same env vars you use for `strands-hub`:

```bash
export USE_S3=true
export AGENT_HUB_BUCKET=your-bucket-name
export AGENT_HUB_REGION=us-east-1
# optional local dir for caching (prompts)
export AGENT_HUB_LOCAL_DIR=./.agent_hub
```

Tip: if you already have these in a `.env`, load it before starting the MCP server.


## Install (local dev)

If you're developing next to the `strands-hub` repo:

```bash
pip install -e ../strands-hub
pip install -e .
```

## Run

Most MCP clients start servers as a subprocess over stdio.

```bash
strands-hub-mcp
```

## Tool policy

- **sessions**: read-only
- **metrics**: read-only
- **registry**: metadata updates allowed (field allowlist)
- **prompts**: can create **new versions only** (no overwrites); does **not** modify `current.txt`

## Available tools

- `hub_status`
- `registry_list_agents`, `registry_get_agent`, `registry_update_metadata`
- `prompts_get_current`, `prompts_get_version`, `prompts_list_versions`, `prompts_create_version`
- `metrics_list`, `metrics_get`
- `sessions_list`, `sessions_get_session_json`, `sessions_list_agents`, `sessions_get_agent_json`, `sessions_list_messages`, `sessions_get_message_json`, `sessions_get_raw`

## Integrations

### Claude Code (CLI)

Register as a stdio MCP server (uses the same env vars as `strands-hub`):

```bash
claude mcp add --scope user --transport stdio \
  -e USE_S3=true \
  -e AGENT_HUB_BUCKET=your-bucket-name \
  -e AGENT_HUB_REGION=us-east-1 \
  strands-hub -- strands-hub-mcp
```

Check it:

```bash
claude mcp list
claude mcp get strands-hub
```

### Cursor

Add to **project** config at `.cursor/mcp.json` (or global config at `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "strands-hub": {
      "command": "strands-hub-mcp",
      "args": [],
      "env": {
        "USE_S3": "true",
        "AGENT_HUB_BUCKET": "your-bucket-name",
        "AGENT_HUB_REGION": "us-east-1"
      }
    }
  }
}
```

If `strands-hub-mcp` isn't on your PATH, use the Python module form:

```json
{
  "mcpServers": {
    "strands-hub": {
      "command": "python3",
      "args": ["-m", "strands_hub_mcp"],
      "env": {
        "USE_S3": "true",
        "AGENT_HUB_BUCKET": "your-bucket-name",
        "AGENT_HUB_REGION": "us-east-1"
      }
    }
  }
}
```

### Strands Agents

Use the MCP client to pull tools from this server and add them to your agent:

```python
from mcp import stdio_client, StdioServerParameters
from strands import Agent
from strands.tools.mcp import MCPClient

hub_mcp = MCPClient(lambda: stdio_client(
    StdioServerParameters(
        command="strands-hub-mcp",
        args=[],
        # If your Strands version supports it, you can also set autoApprove=[...]
    )
))

with hub_mcp:
    tools = hub_mcp.list_tools_sync()
    agent = Agent(tools=tools)
    # Now the agent can answer questions using your hub data.
```

Tip: set `USE_S3`, `AGENT_HUB_BUCKET`, `AGENT_HUB_REGION` in your shell or load them from a `.env` before constructing the hub client.

## Example questions (optimized)

### Inventory / registry

- "How many agents do I have?"
- "List my agents and group them by tag/environment."
- "When was the last time agent `X` ran, and what was its last run id?"

### Prompts

- "What is the **current** system prompt text for agent `X`?"
- "Which prompt version is marked **current** for agent `X` (from `versions.json`)?"
- "What prompt version did agent `X` use on its **last run**?"
- "Create a new prompt version `vN` for agent `X` with this content, but do not change current."

### Metrics

- "How many tokens did agent `X` generate on **2025-12-16**? (sum output tokens across runs that day)"
- "Over the last 7 days, which agent used the most tokens?"
- "For agent `X`'s last run, how many tool calls were made (total + per tool)?"

### Daily summary

- "Give me an activity summary for **YYYY-MM-DD**: number of runs, total tokens, top tools, and the latest run per agent."

## Notes / caveats

- **Session format** is owned by `strands-agents`; `sessions_get_raw` returns JSON when it can, otherwise raw text.
- **Token totals / tool call totals** are derived from per-run metrics objects; for multi-run totals the assistant will typically `metrics_list` then `metrics_get` and aggregate.
- **Prompt version "running"** is best answered from the metrics record for a specific run (`prompt_version`) rather than from `current.txt`.
