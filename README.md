# strands-hub-mcp

MCP server that exposes [`strands-hub`](https://github.com/labeveryday/strands-hub) data from S3:

- **Registry** - list/update agents
- **Prompts** - get/create prompt versions
- **Sessions** - browse session data (read-only)
- **Metrics** - query run metrics (read-only)

## Prerequisites

- Python 3.10+
- AWS credentials configured (`~/.aws/credentials` or env vars)
- S3 bucket with strands-hub data

## Claude Code Integration

```bash
claude mcp add strands-hub-mcp --scope user \
  -e USE_S3=true \
  -e AGENT_HUB_BUCKET=your-bucket-name \
  -e AGENT_HUB_REGION=us-east-1 \
  -- uv --directory /path/to/strands-hub-mcp run strands-hub-mcp
```

Verify:

```bash
claude mcp list
```

## Available Tools

| Tool | Description |
|------|-------------|
| `hub_status` | Show current hub configuration |
| `registry_list_agents` | List all registered agents |
| `registry_get_agent` | Get agent details |
| `registry_update_metadata` | Update agent metadata |
| `prompts_get_current` | Get current prompt for an agent |
| `prompts_get_version` | Get specific prompt version |
| `prompts_list_versions` | List all prompt versions |
| `prompts_create_version` | Create new prompt version (append-only) |
| `metrics_list` | List metrics by date/agent |
| `metrics_get` | Get specific metrics record |
| `sessions_list` | List all sessions |
| `sessions_get_session_json` | Get session metadata |
| `sessions_list_agents` | List agents in a session |
| `sessions_get_agent_json` | Get agent data from session |
| `sessions_list_messages` | List messages in a session |
| `sessions_get_message_json` | Get specific message |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `USE_S3` | Yes | Must be `true` |
| `AGENT_HUB_BUCKET` | Yes | S3 bucket name |
| `AGENT_HUB_REGION` | Yes | AWS region |

## Author

Built by **Du'An Lightfoot** ([@labeveryday](https://github.com/labeveryday))