"""strands-hub-mcp: MCP server exposing strands-hub data in S3.

Design goals:
- Follow strands-hub configuration (HubConfig env vars)
- Sessions + metrics are read-only
- Registry updates are constrained to metadata
- Prompts are append-only: create new versions, never overwrite, and do not change current

S3 layout assumptions (based on strands-hub defaults + observed sessions layout):
- registry.json
- metrics/YYYY-MM-DD/<run_id>.json
- system_prompts/<agent_id>/{current.txt,vN.txt,versions.json}
- sessions/session_<agent>_<timestamp>/{session.json,agents/<agent_id>/agent.json,agents/<agent_id>/messages/message_N.json}
"""

from __future__ import annotations

import json
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from strands_hub import AgentRegistry, HubConfig, S3PromptManager, get_config

try:
    from mcp.server.fastmcp import FastMCP
except Exception as e:  # pragma: no cover
    raise RuntimeError("Missing MCP python package. Install with: pip install 'mcp>=1.0.0'") from e


mcp = FastMCP("strands-hub")


def _cfg() -> HubConfig:
    cfg = get_config()
    if not cfg.use_s3:
        raise RuntimeError("This MCP server currently requires USE_S3=true")
    return cfg


def _s3_client(cfg: HubConfig):
    return boto3.client("s3", region_name=cfg.region)


def _ensure_trailing_slash(prefix: str) -> str:
    return prefix if prefix.endswith("/") else prefix + "/"


def _sessions_root(cfg: HubConfig) -> str:
    # HubConfig.sessions_prefix is documented as having no trailing slash.
    return _ensure_trailing_slash(cfg.sessions_prefix)


def _s3_get_text(cfg: HubConfig, key: str) -> str:
    s3 = _s3_client(cfg)
    obj = s3.get_object(Bucket=cfg.bucket, Key=key)
    return obj["Body"].read().decode("utf-8")


def _s3_get_json(cfg: HubConfig, key: str) -> Any:
    return json.loads(_s3_get_text(cfg, key))


def _s3_put_text(cfg: HubConfig, key: str, content: str, content_type: str):
    s3 = _s3_client(cfg)
    s3.put_object(
        Bucket=cfg.bucket,
        Key=key,
        Body=content.encode("utf-8"),
        ContentType=content_type,
    )


def _s3_exists(cfg: HubConfig, key: str) -> bool:
    s3 = _s3_client(cfg)
    try:
        s3.head_object(Bucket=cfg.bucket, Key=key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def _list_common_prefixes(
    cfg: HubConfig,
    prefix: str,
    limit: int = 100,
    continuation_token: str | None = None,
) -> dict:
    """List "folders" (CommonPrefixes) directly under a prefix."""
    s3 = _s3_client(cfg)
    kwargs: dict[str, Any] = {
        "Bucket": cfg.bucket,
        "Prefix": _ensure_trailing_slash(prefix),
        "Delimiter": "/",
        "MaxKeys": max(1, min(limit, 1000)),
    }
    if continuation_token:
        kwargs["ContinuationToken"] = continuation_token

    resp = s3.list_objects_v2(**kwargs)
    prefixes = [p["Prefix"] for p in resp.get("CommonPrefixes", []) if "Prefix" in p]

    return {
        "prefixes": prefixes,
        "next_continuation_token": resp.get("NextContinuationToken"),
        "is_truncated": bool(resp.get("IsTruncated")),
    }


@mcp.tool()
def hub_status() -> dict:
    """Return the effective hub configuration (sanitized) used by this server."""
    cfg = _cfg()
    return {
        "use_s3": cfg.use_s3,
        "bucket": cfg.bucket,
        "region": cfg.region,
        "sessions_prefix": cfg.sessions_prefix,
        "metrics_prefix": cfg.metrics_prefix,
        "prompts_prefix": cfg.prompts_prefix,
        "registry_key": cfg.registry_key,
    }


# -----------------
# Registry
# -----------------


@mcp.tool()
def registry_list_agents(tag: str | None = None) -> list[dict]:
    """List registered agents (optionally filtered by tag)."""
    return AgentRegistry().list_agents(tag=tag)


@mcp.tool()
def registry_get_agent(agent_id: str) -> dict | None:
    """Get a single agent registry entry."""
    return AgentRegistry().get_agent(agent_id)


@mcp.tool()
def registry_update_metadata(
    agent_id: str,
    description: str | None = None,
    tags: list[str] | None = None,
    repo_url: str | None = None,
    owner: str | None = None,
    environment: str | None = None,
    model_id: str | None = None,
) -> dict | None:
    """Update allowlisted metadata fields for an existing agent."""
    return AgentRegistry().update_agent(
        agent_id=agent_id,
        description=description,
        tags=tags,
        repo_url=repo_url,
        owner=owner,
        environment=environment,
        model_id=model_id,
    )


# -----------------
# Prompts
# -----------------


@mcp.tool()
def prompts_get_current(agent_id: str, force_refresh: bool = False) -> str:
    """Get the current system prompt content for an agent (cached locally by strands-hub)."""
    return S3PromptManager(agent_id=agent_id).get_current(force_refresh=force_refresh)


@mcp.tool()
def prompts_get_version(agent_id: str, version: str) -> str:
    """Get a specific system prompt version content for an agent."""
    return S3PromptManager(agent_id=agent_id).get_version(version=version)


@mcp.tool()
def prompts_list_versions(agent_id: str) -> dict:
    """List prompt versions for an agent from S3 (versions.json if present)."""
    cfg = _cfg()
    manifest_key = f"{cfg.prompts_prefix}{agent_id}/versions.json"
    if _s3_exists(cfg, manifest_key):
        return _s3_get_json(cfg, manifest_key)
    return {"versions": [], "current": None}


@mcp.tool()
def prompts_create_version(
    agent_id: str,
    version: str,
    content: str,
    note: str | None = None,
) -> dict:
    """Create a new prompt version (append-only). Does NOT modify current.txt.

    - Fails if the version already exists.
    - Updates versions.json without changing its current pointer.
    """
    cfg = _cfg()

    if not version:
        raise ValueError("version is required")

    version_key = f"{cfg.prompts_prefix}{agent_id}/{version}.txt"
    if _s3_exists(cfg, version_key):
        raise RuntimeError(f"Prompt version already exists: {version}")

    _s3_put_text(cfg, version_key, content, content_type="text/plain")

    manifest_key = f"{cfg.prompts_prefix}{agent_id}/versions.json"
    if _s3_exists(cfg, manifest_key):
        manifest = _s3_get_json(cfg, manifest_key)
        if "versions" not in manifest or not isinstance(manifest["versions"], list):
            manifest["versions"] = []
        if "current" not in manifest:
            manifest["current"] = None
    else:
        manifest = {"versions": [], "current": None}

    if any(v.get("version") == version for v in manifest["versions"]):
        raise RuntimeError(f"Prompt version already listed in manifest: {version}")

    manifest["versions"].append({"version": version, "note": note, "created_at": time.time()})

    _s3_put_text(cfg, manifest_key, json.dumps(manifest, indent=2), content_type="application/json")

    return {"ok": True, "version_key": version_key, "manifest_key": manifest_key}


# -----------------
# Metrics (read-only)
# -----------------


@mcp.tool()
def metrics_list(
    date_prefix: str | None = None,
    agent_id: str | None = None,
    limit: int = 100,
    continuation_token: str | None = None,
) -> dict:
    """List metrics objects in S3."""
    cfg = _cfg()
    s3 = _s3_client(cfg)

    prefix = cfg.metrics_prefix
    if date_prefix:
        prefix = f"{prefix}{date_prefix}/"

    kwargs: dict[str, Any] = {
        "Bucket": cfg.bucket,
        "Prefix": prefix,
        "MaxKeys": max(1, min(limit, 1000)),
    }
    if continuation_token:
        kwargs["ContinuationToken"] = continuation_token

    resp = s3.list_objects_v2(**kwargs)
    keys = [o["Key"] for o in resp.get("Contents", [])]
    if agent_id:
        keys = [k for k in keys if k.split("/")[-1].startswith(f"{agent_id}_")]

    return {
        "keys": keys,
        "next_continuation_token": resp.get("NextContinuationToken"),
        "is_truncated": bool(resp.get("IsTruncated")),
    }


@mcp.tool()
def metrics_get(s3_key: str) -> dict:
    """Fetch a metrics JSON object by S3 key."""
    cfg = _cfg()
    if not s3_key.startswith(cfg.metrics_prefix):
        raise ValueError("s3_key must be under metrics prefix")
    return _s3_get_json(cfg, s3_key)


# -----------------
# Sessions (read-only)
# -----------------


@mcp.tool()
def sessions_list(limit: int = 100, continuation_token: str | None = None) -> dict:
    """List session IDs (prefixes) under `sessions/`.

    Uses S3 CommonPrefixes (Delimiter="/") so it matches `aws s3 ls s3://.../sessions/`.
    """
    cfg = _cfg()
    root = _sessions_root(cfg)

    resp = _list_common_prefixes(cfg, root, limit=limit, continuation_token=continuation_token)
    session_ids = [p[len(root) :].rstrip("/") for p in resp["prefixes"] if p.startswith(root)]

    return {
        "session_ids": session_ids,
        "session_prefixes": resp["prefixes"],
        "next_continuation_token": resp["next_continuation_token"],
        "is_truncated": resp["is_truncated"],
    }


@mcp.tool()
def sessions_get_session_json(session_id: str) -> dict:
    """Fetch and parse `sessions/<session_id>/session.json`."""
    cfg = _cfg()
    key = f"{_sessions_root(cfg)}{session_id}/session.json"
    return _s3_get_json(cfg, key)


@mcp.tool()
def sessions_list_agents(session_id: str, limit: int = 100, continuation_token: str | None = None) -> dict:
    """List agent IDs under `sessions/<session_id>/agents/`."""
    cfg = _cfg()
    agents_prefix = f"{_sessions_root(cfg)}{session_id}/agents/"

    resp = _list_common_prefixes(cfg, agents_prefix, limit=limit, continuation_token=continuation_token)
    agent_ids = [p[len(agents_prefix) :].rstrip("/") for p in resp["prefixes"] if p.startswith(agents_prefix)]

    return {
        "agent_ids": agent_ids,
        "agent_prefixes": resp["prefixes"],
        "next_continuation_token": resp["next_continuation_token"],
        "is_truncated": resp["is_truncated"],
    }


@mcp.tool()
def sessions_get_agent_json(session_id: str, agent_id: str = "agent_default") -> dict:
    """Fetch and parse `sessions/<session_id>/agents/<agent_id>/agent.json`."""
    cfg = _cfg()
    key = f"{_sessions_root(cfg)}{session_id}/agents/{agent_id}/agent.json"
    return _s3_get_json(cfg, key)


@mcp.tool()
def sessions_list_messages(
    session_id: str,
    agent_id: str = "agent_default",
    limit: int = 200,
    continuation_token: str | None = None,
) -> dict:
    """List message keys under `sessions/<session_id>/agents/<agent_id>/messages/`."""
    cfg = _cfg()
    s3 = _s3_client(cfg)

    prefix = f"{_sessions_root(cfg)}{session_id}/agents/{agent_id}/messages/"

    kwargs: dict[str, Any] = {
        "Bucket": cfg.bucket,
        "Prefix": prefix,
        "MaxKeys": max(1, min(limit, 1000)),
    }
    if continuation_token:
        kwargs["ContinuationToken"] = continuation_token

    resp = s3.list_objects_v2(**kwargs)
    keys = [o["Key"] for o in resp.get("Contents", [])]

    return {
        "keys": sorted(keys),
        "next_continuation_token": resp.get("NextContinuationToken"),
        "is_truncated": bool(resp.get("IsTruncated")),
    }


@mcp.tool()
def sessions_get_message_json(session_id: str, message_key: str, agent_id: str = "agent_default") -> dict:
    """Fetch and parse a message JSON object.

    message_key may be either a basename like `message_0.json` or a full S3 key.
    """
    cfg = _cfg()
    base = f"{_sessions_root(cfg)}{session_id}/agents/{agent_id}/messages/"
    key = message_key if message_key.startswith(base) else base + message_key.lstrip("/")

    if not key.startswith(base):
        raise ValueError("message_key must be under the session messages prefix")

    return _s3_get_json(cfg, key)


@mcp.tool()
def sessions_get_raw(s3_key: str) -> dict:
    """Fetch a session object by S3 key and return parsed JSON when possible.

    This is intentionally "raw" because session format is owned by `strands-agents`.
    """
    cfg = _cfg()
    prefix = _sessions_root(cfg)
    if not s3_key.startswith(prefix):
        raise ValueError("s3_key must be under sessions prefix")

    text = _s3_get_text(cfg, s3_key)
    try:
        return {"s3_key": s3_key, "json": json.loads(text)}
    except Exception:
        return {"s3_key": s3_key, "text": text}


def main():  # pragma: no cover
    mcp.run()
