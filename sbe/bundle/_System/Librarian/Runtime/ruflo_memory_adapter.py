#!/usr/bin/env python3
"""Small, deterministic Ruflo memory boundary for Second Brain metadata."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import subprocess
import re

DEFAULT_ROOT = Path("~/Library/Application Support/SecondBrainRuflo").expanduser()
DEFAULT_BIN = DEFAULT_ROOT / "node_modules" / ".bin" / "ruflo"
DEFAULT_DB = DEFAULT_ROOT / "data" / "memory.db"
NAMESPACE = "secondbrain-librarian"
MAX_QUERY = 500
MAX_TEXT = 4000
MAX_RESULTS = 8
LOCK_PATH = DEFAULT_ROOT / "memory-adapter.lock"


class RufloUnavailable(RuntimeError):
    pass


def _bin() -> Path:
    return Path(os.environ.get("SECOND_BRAIN_RUFLO_BIN", str(DEFAULT_BIN))).expanduser()


def _db() -> Path:
    return Path(os.environ.get("SECOND_BRAIN_RUFLO_DB", str(DEFAULT_DB))).expanduser()


def _json_from_cli_output(raw: str):
    """Decode the final JSON value after Ruflo's informational prefix."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for offset, char in enumerate(raw):
        if char not in "[{":
            continue
        try:
            value, end = decoder.raw_decode(raw, offset)
        except json.JSONDecodeError:
            continue
        if not raw[end:].strip():
            return value
    raise RufloUnavailable("Ruflo returned non-JSON output")


def _run(args: list[str], timeout: int = 30, success_marker: str | None = None):
    binary = _bin()
    if not binary.exists():
        raise RufloUnavailable(f"Ruflo binary not found: {binary}")
    if _db().name != "memory.db":
        raise RufloUnavailable("Ruflo 3.38.21 requires the memory database filename memory.db")
    env = os.environ.copy()
    env["CLAUDE_FLOW_DB_PATH"] = str(_db())
    env["CLAUDE_FLOW_MEMORY_PATH"] = str(_db().parent)
    env["RUFLO_MEMORY_SCAN_ON_WRITE"] = "1"
    env["RUFLO_DAEMON_AUTOSTART"] = "0"
    try:
        process = subprocess.run(
            [str(binary), *args],
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            cwd=str(DEFAULT_ROOT),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RufloUnavailable(str(exc)) from exc
    if process.returncode:
        raise RufloUnavailable((process.stderr or process.stdout)[-1200:])
    raw = (process.stdout or "").strip()
    if success_marker and success_marker in raw:
        return {"ok": True}
    if not raw:
        return None
    return _json_from_cli_output(raw)


def _mcp_exec(tool: str, params: dict, timeout: int = 45):
    """Call one whitelisted Ruflo coordination tool without starting a daemon."""
    if tool not in {"swarm_init", "swarm_shutdown", "agent_spawn", "task_create", "task_update", "task_complete"}:
        raise RufloUnavailable(f"Ruflo coordination tool is not allowed: {tool}")
    result = _run([
        "mcp", "exec", "-t", tool, "-p",
        json.dumps(params, ensure_ascii=False, separators=(",", ":")),
    ], timeout=timeout)
    if not isinstance(result, dict) or result.get("success") is False:
        raise RufloUnavailable(f"Ruflo {tool} returned an invalid result")
    return result


def _safe_id(value: object, label: str) -> str:
    value = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise RufloUnavailable(f"Ruflo returned an unsafe {label}")
    return value


def recall(query: str, limit: int = MAX_RESULTS):
    query = str(query)[:MAX_QUERY]
    limit = max(1, min(int(limit), MAX_RESULTS))
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        result = _run([
            "memory", "search", "--query", query, "--namespace", NAMESPACE,
            "--limit", str(limit), "--format", "json",
        ])
    return result if isinstance(result, (list, dict)) else []


def store(key: str, value: dict, *, kind: str = "episode"):
    payload = json.dumps({"kind": kind, **value}, ensure_ascii=False, separators=(",", ":"))
    payload = payload[:MAX_TEXT]
    _db().parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _run([
            "memory", "store", "--key", str(key)[:200], "--value", payload,
            "--namespace", NAMESPACE,
        ], success_marker="[OK] Data stored successfully")


def committed_episode(cluster_id: str, transaction_id: str, concepts: list[str]):
    return store(
        f"cluster:{cluster_id}",
        {
            "cluster_id": cluster_id,
            "transaction_id": transaction_id,
            "concepts": [str(item)[:120] for item in concepts[:20]],
        },
        kind="committed_cluster",
    )


def begin_semantic_escalation(cluster_id: str, errors: list[str]):
    """Register a bounded review swarm only after deterministic validation fails."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        swarm = _mcp_exec("swarm_init", {
            "topology": "hierarchical", "maxAgents": 2, "strategy": "specialized",
            "config": {"purpose": "secondbrain-semantic-review", "autopilot": False},
        })
        swarm_id = _safe_id(swarm.get("swarmId"), "swarm id")
        suffix = re.sub(r"[^A-Za-z0-9_-]", "-", cluster_id)[-60:] or "cluster"
        reviewer_id = f"librarian-reviewer-{suffix}"
        agent = _mcp_exec("agent_spawn", {
            "agentType": "reviewer", "agentId": reviewer_id, "swarmId": swarm_id,
            "config": {"readOnly": True, "execution": "cao"}, "model": "inherit",
            "task": "Correct deterministically invalid minimal semantics",
        })
        reviewer_id = _safe_id(agent.get("agentId"), "agent id")
        task = _mcp_exec("task_create", {
            "type": "bugfix",
            "description": f"Review semantic validation for {cluster_id}"[:1000],
            "priority": "normal", "assignTo": [reviewer_id],
            "tags": ["secondbrain-librarian", "semantic-review", f"errors:{min(len(errors), 99)}"],
        })
        return {
            "swarm_id": swarm_id,
            "reviewer_id": reviewer_id,
            "task_id": _safe_id(task.get("taskId"), "task id"),
            "status": "registered",
        }


def finish_semantic_escalation(escalation: dict, success: bool, detail: str = ""):
    """Close a registered review task; this never stores uncommitted semantics."""
    if not isinstance(escalation, dict):
        return None
    swarm_id = _safe_id(escalation.get("swarm_id"), "swarm id")
    task_id = _safe_id(escalation.get("task_id"), "task id")
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if success:
            _mcp_exec("task_complete", {
                "taskId": task_id,
                "result": {"validated": True, "detail": str(detail)[:500]},
            })
        else:
            _mcp_exec("task_update", {"taskId": task_id, "status": "failed", "progress": 100})
        return _mcp_exec("swarm_shutdown", {"swarmId": swarm_id, "graceful": True})
