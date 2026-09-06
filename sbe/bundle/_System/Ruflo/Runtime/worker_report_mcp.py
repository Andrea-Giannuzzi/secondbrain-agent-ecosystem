#!/usr/bin/env python3
"""Out-of-band delivery channel for a CAO read-only worker's finished report.

CAO decides that a worker's turn has ended by reading the shape of its terminal
pane, so a still-streaming answer is sometimes captured and torn down
mid-sentence. The result is a truncated report that the bridge must refuse,
because a partial review can never be recorded as an independent verification.

This server removes the terminal from the critical path: the worker delivers its
report by calling the single tool below, which is an atomic transfer, so the
answer is already durable while the pane is still rendering. Everything the
worker may do stays otherwise unchanged — writes, shell, subagents and network
remain blocked by its profile, and the destination path is derived here from the
run's own staging directory rather than accepted from the caller.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from mcp.server.mcpserver import MCPServer


MAX_REPORT_CHARS = 20_000
REPORT_NAME = "worker-report.json"

INSTRUCTIONS = """Deliver the finished investigation or review by calling report_worker_result exactly once. The tool call is the answer itself: the report text passed to it is what the coordinator reads, so do not rely on anything printed to the terminal."""

server = MCPServer("secondbrain-worker-report", instructions=INSTRUCTIONS)


def _report_path() -> Path:
    """Destination beside the staged snapshot, never a caller-supplied path.

    The worker runs with the staged workspace as its working directory, so the
    run's own directory is its parent. Writing there keeps the snapshot itself
    exactly as the bridge staged it.
    """
    workspace = Path.cwd().resolve()
    if workspace.name != "workspace":
        raise RuntimeError("The worker is not running in a staged snapshot")
    return workspace.parent / REPORT_NAME


@server.tool()
def report_worker_result(verdict: str, report: str) -> dict[str, object]:
    """Deliver the finished report. Call this exactly once; it is the answer."""
    verdict = str(verdict).strip().upper()
    if verdict not in {"PASS", "BLOCKED"}:
        raise ValueError("verdict must be PASS or BLOCKED")
    report = str(report)
    truncated = len(report) > MAX_REPORT_CHARS
    payload = {
        "version": "worker-report-1.0",
        "verdict": verdict,
        "report": report[:MAX_REPORT_CHARS],
        "truncated": truncated,
    }
    path = _report_path()
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False, dir=str(path.parent),
        prefix=f".{path.name}.", suffix=".tmp",
    ) as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(payload, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return {"delivered": True, "verdict": verdict, "truncated": truncated}


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
