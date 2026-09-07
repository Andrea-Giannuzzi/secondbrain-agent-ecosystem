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
MAX_FINDINGS = 50
MAX_FIELD_CHARS = 600
REPORT_NAME = "worker-report.json"
SEVERITIES = ("high", "medium", "low")
# A finished review must never be thrown away over the word chosen for its
# verdict. Observed: a worker returned two well-formed findings under
# `verdict: "FAIL"`, the strict check rejected the call, and the model did not
# try again -- the whole review was lost to a synonym. The distinction is
# binary, so anything that unambiguously means one side is taken as that side.
VERDICT_SYNONYMS = {
    "PASS": "PASS", "PASSED": "PASS", "OK": "PASS", "APPROVED": "PASS",
    "APPROVE": "PASS", "CLEAN": "PASS", "NO BLOCKER": "PASS", "SUCCESS": "PASS",
    "BLOCKED": "BLOCKED", "BLOCK": "BLOCKED", "FAIL": "BLOCKED",
    "FAILED": "BLOCKED", "FAILURE": "BLOCKED", "REJECT": "BLOCKED",
    "REJECTED": "BLOCKED",
}
# Every finding must say how the defect is REACHED, not only how bad it would be.
# A review that graded four findings as data loss, two of which needed a manually
# duplicated tab to trigger, left the author to work out on their own which ones
# mattered. Reachability is the reviewer's to state, so it is required here.
REQUIRED_FIELDS = ("file", "claim", "reachability", "severity")

INSTRUCTIONS = """Deliver the finished investigation or review by calling report_worker_result exactly once. The tool call is the answer itself: what you pass to it is what the coordinator reads, so do not rely on anything printed to the terminal. Put each concrete defect in `findings`, one entry per defect, each stating the file, the line, the claim, how the defect is reached in normal use, and its severity. Use `report` for the summary of what you checked, not for the defects themselves."""

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


def _clean_findings(findings: object, verdict: str) -> list[dict[str, object]]:
    """Validate the findings into the shape the coordinator acts on, item by item.

    Rejecting an incomplete finding here is deliberate: the tool result goes back
    to the worker, which can then supply what it left out. Accepting it silently
    would push the same gap on to the author, who is the one person who cannot
    fill it in.
    """
    if findings is None:
        findings = []
    if not isinstance(findings, list):
        raise ValueError("findings must be a list of objects")
    # A BLOCKED verdict with nothing in findings is a poor review, not an invalid
    # one: a reviewer that could not inspect the snapshot at all has exactly that
    # to report, and rejecting the call threw the whole answer away -- the same
    # way the strict verdict check once did. The emptiness is not hidden: it
    # travels to the coordinator as findings: 0, which is visible and judgeable.
    if len(findings) > MAX_FINDINGS:
        raise ValueError(f"at most {MAX_FINDINGS} findings; report the rest as a summary")
    cleaned = []
    for index, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            raise ValueError(f"finding {index} must be an object")
        missing = [key for key in REQUIRED_FIELDS if not str(finding.get(key, "")).strip()]
        if missing:
            raise ValueError(
                f"finding {index} is missing {', '.join(missing)}. Every finding needs "
                "file (relative path), claim (what is wrong, one sentence), reachability "
                "(how the defect is reached in normal use), and severity "
                f"({'/'.join(SEVERITIES)})."
            )
        severity = str(finding["severity"]).strip().lower()
        if severity not in SEVERITIES:
            raise ValueError(f"finding {index}: severity must be one of {'/'.join(SEVERITIES)}")
        entry = {
            "id": f"f{index}",
            "file": str(finding["file"]).strip()[:MAX_FIELD_CHARS],
            "claim": str(finding["claim"]).strip()[:MAX_FIELD_CHARS],
            "reachability": str(finding["reachability"]).strip()[:MAX_FIELD_CHARS],
            "severity": severity,
        }
        line = finding.get("line")
        if str(line or "").strip():
            entry["line"] = str(line).strip()[:40]
        evidence = finding.get("evidence")
        if str(evidence or "").strip():
            entry["evidence"] = str(evidence).strip()[:MAX_FIELD_CHARS]
        cleaned.append(entry)
    return cleaned


@server.tool()
def report_worker_result(
    verdict: str, report: str, findings: list | None = None
) -> dict[str, object]:
    """Deliver the finished report. Call this exactly once; it is the answer.

    `findings` carries the concrete defects, one entry per defect, each with
    file, line, claim, reachability and severity. `report` is the summary of
    what was checked.
    """
    raw = " ".join(str(verdict).split()).upper()
    verdict = VERDICT_SYNONYMS.get(raw, "")
    if not verdict:
        raise ValueError("verdict must be PASS (no blocker) or BLOCKED (a concrete blocker exists)")
    cleaned = _clean_findings(findings, verdict)
    report = str(report)
    truncated = len(report) > MAX_REPORT_CHARS
    payload = {
        "version": "worker-report-2.0",
        "verdict": verdict,
        "report": report[:MAX_REPORT_CHARS],
        "findings": cleaned,
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
    return {
        "delivered": True, "verdict": verdict,
        "findings": len(cleaned), "truncated": truncated,
    }


MAX_PAYLOAD_CHARS = 200_000


@server.tool()
def report_worker_output(payload: dict) -> dict[str, object]:
    """Deliver a finished JSON result. Call this exactly once; it is the answer.

    The Librarian's workers answer with a schema-shaped object rather than a
    verdict, so they deliver it here instead of printing it: a pane capture that
    ends mid-object fails the schema and costs a whole re-run.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded) > MAX_PAYLOAD_CHARS:
        raise ValueError(f"payload exceeds {MAX_PAYLOAD_CHARS} characters")
    path = _report_path()
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False, dir=str(path.parent),
        prefix=f".{path.name}.", suffix=".tmp",
    ) as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump({"version": "worker-output-1.0", "payload": payload}, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return {"delivered": True, "characters": len(encoded)}


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
