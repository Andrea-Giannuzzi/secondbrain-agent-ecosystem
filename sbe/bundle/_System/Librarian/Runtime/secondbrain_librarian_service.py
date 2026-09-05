#!/usr/bin/env python3
"""Run the transactional Librarian for every pending ingestion batch."""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request


VAULT = Path(os.environ.get("SECOND_BRAIN_VAULT", "~/Documents/SecondBrain")).expanduser().resolve()
SOURCES = VAULT / "50_Sources"
QUEUE = VAULT / "_System" / "Automation" / "librarian-queue.jsonl"
SYSTEM = VAULT / "_System" / "Librarian"
LOCK = SYSTEM / "librarian-service.lock"
PROVIDER_CIRCUIT = SYSTEM / "provider-circuit.json"
AI_INVALID_STATE = SYSTEM / "ai-invalid-state.json"
LOG = VAULT / "_System" / "Logs" / "librarian-service.jsonl"
ORGANIZER = Path(
    os.environ.get(
        "SECOND_BRAIN_LIBRARIAN_ORGANIZER",
        "~/Library/Application Support/SecondBrainLibrarian/brain-cluster-organize",
    )
).expanduser()
CAO_HEALTH = os.environ.get("CAO_HEALTH_URL", "http://127.0.0.1:9889/health")
DEBOUNCE_SECONDS = int(os.environ.get("SECOND_BRAIN_LIBRARIAN_DEBOUNCE", "20"))
PROCESS_TIMEOUT = int(os.environ.get("SECOND_BRAIN_LIBRARIAN_TIMEOUT", "14400"))


def iso():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def log(event: str, **values):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"time": iso(), "event": event, **values}, ensure_ascii=False) + "\n")


def frontmatter(text: str):
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    values = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key, raw = key.strip(), raw.strip()
        if raw.startswith('"') and raw.endswith('"'):
            try:
                values[key] = json.loads(raw)
            except json.JSONDecodeError:
                values[key] = raw[1:-1]
        elif raw.lower() in {"true", "false"}:
            values[key] = raw.lower() == "true"
        else:
            values[key] = raw
    return values


def pending_batches(source_root: Path = SOURCES):
    invalid_state = ai_invalid_sources()
    batches = {}
    for note in sorted(source_root.glob("*.md")):
        metadata = frontmatter(note.read_text(encoding="utf-8", errors="replace"))
        pending = metadata.get("needs_librarian") is True or str(metadata.get("status", "")).strip('"') == "unprocessed"
        if not pending:
            continue
        entry = None
        if invalid_state:
            try:
                source_note = note.relative_to(VAULT).as_posix()
            except ValueError:
                source_note = (Path("50_Sources") / note.relative_to(source_root)).as_posix()
            entry = invalid_state.get(source_note)
        if ai_invalid_entry_waiting(note, entry):
            continue
        batch = str(metadata.get("import_batch", "") or "")
        ingested = str(metadata.get("ingested_at", "") or "")
        batches[batch] = max(batches.get(batch, ""), ingested)
    return [batch for batch, _ in sorted(batches.items(), key=lambda item: (item[1], item[0]))]


def ai_invalid_sources():
    if not AI_INVALID_STATE.exists():
        return {}
    try:
        value = json.loads(AI_INVALID_STATE.read_text(encoding="utf-8"))
        sources = value.get("sources", {})
        return sources if isinstance(sources, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def ai_invalid_entry_waiting(note: Path, entry, at=None):
    if not isinstance(entry, dict):
        return False
    fingerprint = hashlib.sha256(note.read_bytes()).hexdigest()
    if entry.get("source_sha256") != fingerprint:
        return False
    at = at or dt.datetime.now().astimezone()
    try:
        retry_at = dt.datetime.fromisoformat(str(entry.get("next_retry_at", "")))
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=at.tzinfo)
        return at < retry_at
    except (TypeError, ValueError):
        return False


def cao_ready():
    try:
        with urllib.request.urlopen(CAO_HEALTH, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def queue_is_settled():
    return not QUEUE.exists() or time.time() - QUEUE.stat().st_mtime >= DEBOUNCE_SECONDS


def provider_circuit_waiting(at=None):
    at = at or dt.datetime.now().astimezone()
    if not PROVIDER_CIRCUIT.exists():
        return False, {}
    try:
        state = json.loads(PROVIDER_CIRCUIT.read_text(encoding="utf-8"))
        if state.get("state") not in {"open", "half_open"}:
            return False, state
        next_probe = dt.datetime.fromisoformat(str(state.get("next_probe_at", "")))
        if next_probe.tzinfo is None:
            next_probe = next_probe.replace(tzinfo=at.tzinfo)
        return at < next_probe, state
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        # The organizer owns recovery of malformed state and will permit one probe.
        return False, {}


def run_batch(batch: str):
    command = [str(ORGANIZER), "--apply"]
    if batch:
        command.extend(["--batch", batch])
    else:
        command.append("--unbatched")
    process = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=PROCESS_TIMEOUT,
        env=os.environ.copy(),
        cwd=str(VAULT),
    )
    log(
        "batch_finished",
        batch=batch,
        returncode=process.returncode,
        stdout=(process.stdout or "")[-6000:],
        stderr=(process.stderr or "")[-6000:],
    )
    return process.returncode


def run_batches(batches):
    failed = []
    paused = False
    for batch in batches:
        returncode = run_batch(batch)
        if returncode == 75:
            failed.append(batch)
            paused = True
            break
        if returncode != 0:
            failed.append(batch)
    return failed, paused


def main():
    SYSTEM.mkdir(parents=True, exist_ok=True)
    LOCK.touch(exist_ok=True)
    with LOCK.open("r+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("already_running")
            return 0
        if not queue_is_settled():
            log("debouncing", seconds=DEBOUNCE_SECONDS)
            return 0
        batches = pending_batches()
        if not batches:
            log("idle")
            return 0
        waiting, circuit = provider_circuit_waiting()
        if waiting:
            log(
                "provider_circuit_waiting",
                consecutive_dual_failures=circuit.get("consecutive_dual_failures"),
                next_probe_at=circuit.get("next_probe_at"),
            )
            return 75
        if not ORGANIZER.is_file():
            log("organizer_missing", path=str(ORGANIZER))
            return 69
        if not cao_ready():
            log("cao_unavailable", url=CAO_HEALTH)
            return 75
        log("run_started", batches=batches)
        failed, paused = run_batches(batches)
        remaining = pending_batches()
        log("run_finished", failed_batches=failed, remaining_batches=remaining, provider_paused=paused)
        if paused:
            return 75
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
