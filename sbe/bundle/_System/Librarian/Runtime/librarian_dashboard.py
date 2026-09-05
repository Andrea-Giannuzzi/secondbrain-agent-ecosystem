#!/usr/bin/env python3
"""Local status and control surface for the Second Brain Librarian."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from agent_inventory import agent_inventory
from pathlib import Path
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request


VAULT = Path(os.environ.get("SECOND_BRAIN_VAULT", "~/Documents/SecondBrain")).expanduser().resolve()
SYSTEM = VAULT / "_System" / "Librarian"
ASSETS = SYSTEM / "Dashboard"
SOURCES = VAULT / "50_Sources"
KNOWLEDGE = VAULT / "30_Knowledge"
AREAS = VAULT / "20_Areas"
SERVICE_LOG = VAULT / "_System" / "Logs" / "librarian-service.jsonl"
PROVIDER_CIRCUIT = SYSTEM / "provider-circuit.json"
PROVIDER_CIRCUIT_LOCK = SYSTEM / "provider-circuit.lock"
AI_INVALID_STATE = SYSTEM / "ai-invalid-state.json"
AI_INVALID_LOCK = SYSTEM / "ai-invalid-state.lock"
APP = Path("~/Library/Application Support/SecondBrainLibrarian").expanduser()
RUNS = APP / "ClusterRuns"
ORGANIZER = APP / "brain-cluster-organize"
RUFLO_ROOT = Path("~/Library/Application Support/SecondBrainRuflo").expanduser()
RUFLO_TEAM_STATE = RUFLO_ROOT / "TeamState" / "teams.json"
CODEX_SESSIONS = Path("~/.codex/sessions").expanduser()
ANTIGRAVITY_APP = Path("~/.gemini/antigravity").expanduser()
ANTIGRAVITY_IDE = Path("~/.gemini/antigravity-ide").expanduser()
ANTIGRAVITY_CLI = Path("~/.gemini/antigravity-cli").expanduser()
CAO_BASE = os.environ.get("CAO_BASE_URL", "http://127.0.0.1:9889").rstrip("/")
SERVICE_LABEL = "com.local.secondbrain.librarian"
CAO_LABEL = "com.local.secondbrain.cao"
ACTION_HEADER = "X-SecondBrain-Dashboard"
ECOSYSTEM_PROFILE_PREFIXES = ("librarian_", "ruflo_")
CURRENT_ECOSYSTEM_PROFILES = {
    "librarian_antigravity_cluster_semantic",
    "librarian_antigravity_cluster_semantic_reviewer",
    "librarian_antigravity_memory",
    "librarian_codex_cluster_semantic",
    "librarian_codex_cluster_semantic_reviewer",
    "ruflo_antigravity_readonly_worker",
    "ruflo_codex_readonly_worker",
}
RUFLO_ROLE_RESPONSIBILITIES = {
    "coordinator": "Scompone l'obiettivo e coordina le fasi usando il provider della sessione VS Code; l'altro provider può subentrare con un handoff registrato.",
    "investigator": "Esamina una copia redatta dei file e passa automaticamente all'altro provider se il primo non è disponibile.",
    "executor": "È l'unico writer e usa lo stesso provider della sessione VS Code; cambia proprietario soltanto tramite handoff.",
    "reviewer": "Preferisce il provider opposto al writer e, se non disponibile, usa il writer marcando la revisione come fallback degradato.",
}
QUOTA_ERROR_MARKERS = (
    "quota", "usage limit", "rate limit", "insufficient_quota",
    "too many requests", "http 429", "status 429",
)


def allowed_dashboard_origins(host, port):
    return {
        f"http://{host}:{port}",
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"http://legend.localhost:{port}",
    }


def now():
    return dt.datetime.now().astimezone()


def iso():
    return now().isoformat(timespec="seconds")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False, dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp",
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def parse_frontmatter(text: str):
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
        elif raw.casefold() in {"true", "false"}:
            values[key] = raw.casefold() == "true"
        else:
            values[key] = raw
    return values


def parse_time(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value or ""))
        return parsed.replace(tzinfo=now().tzinfo) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def time_key(value):
    parsed = parse_time(value)
    return parsed.timestamp() if parsed else 0


def ecosystem_profile(name):
    return isinstance(name, str) and name.startswith(ECOSYSTEM_PROFILE_PREFIXES)


def source_snapshot():
    invalid = read_json(AI_INVALID_STATE, {"sources": {}}).get("sources", {})
    pending = []
    backoff = []
    total = 0
    linked = 0
    unlinked_breakdown = {
        "technical_reference": 0,
        "low_information": 0,
        "sensitive_reference": 0,
        "administrative": 0,
        "pending": 0,
        "other": 0,
    }
    for note in sorted(SOURCES.glob("*.md")):
        total += 1
        text = note.read_text(encoding="utf-8", errors="replace")
        metadata = parse_frontmatter(text)
        has_knowledge_link = "[[30_Knowledge/" in text
        if has_knowledge_link:
            linked += 1
        is_pending = (
            metadata.get("needs_librarian") is True
            or str(metadata.get("status", "")).strip('"') == "unprocessed"
        )
        if not has_knowledge_link:
            disposition = str(metadata.get("librarian_disposition", "") or "").strip('"')
            key = "pending" if is_pending else disposition
            if key not in unlinked_breakdown:
                key = "other"
            unlinked_breakdown[key] += 1
        if not is_pending:
            continue
        source_note = note.relative_to(VAULT).as_posix()
        item = {
            "source_note": source_note,
            "title": note.stem,
            "batch": str(metadata.get("import_batch", "") or ""),
        }
        entry = invalid.get(source_note)
        if isinstance(entry, dict):
            digest = hashlib.sha256(note.read_bytes()).hexdigest()
            retry_at = parse_time(entry.get("next_retry_at"))
            if entry.get("source_sha256") == digest and retry_at and now() < retry_at:
                item.update({
                    "next_retry_at": retry_at.isoformat(timespec="seconds"),
                    "failures": int(entry.get("consecutive_failures", 0) or 0),
                    "error": (entry.get("errors") or [""])[0],
                })
                backoff.append(item)
                continue
        pending.append(item)
    return {
        "total": total,
        "organized": total - len(pending) - len(backoff),
        "linked": linked,
        "unlinked": total - linked,
        "pending_ready": pending,
        "backoff": backoff,
        "unlinked_breakdown": unlinked_breakdown,
    }


def launch_agent(label: str):
    process = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
        text=True, capture_output=True, timeout=5,
    )
    if process.returncode:
        return {"installed": False, "state": "missing", "runs": None, "last_exit_code": None}
    text = process.stdout
    state = "unknown"
    runs = None
    last_exit = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("state =") and state == "unknown":
            state = line.split("=", 1)[1].strip()
        elif line.startswith("runs ="):
            try:
                runs = int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("last exit code ="):
            last_exit = line.split("=", 1)[1].strip()
    return {"installed": True, "state": state, "runs": runs, "last_exit_code": last_exit}


def cao_get(path: str, default):
    try:
        with urllib.request.urlopen(CAO_BASE + path, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return default


def recent_runs(limit=12):
    manifests = sorted(RUNS.glob("*/manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    rows = []
    for path in manifests[:limit]:
        value = read_json(path, {})
        rows.append({
            "time": value.get("updated_at") or value.get("created_at"),
            "status": value.get("status", "UNKNOWN"),
            "cluster": value.get("cluster_title") or value.get("cluster_id") or path.parent.name,
            "provider": value.get("last_provider"),
            "sources": len(value.get("source_notes") or []),
        })
    return rows


def librarian_provider_calls(limit=60):
    manifests = sorted(
        RUNS.glob("*/manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    calls = []
    for path in manifests[:limit]:
        value = read_json(path, {})
        observed_at = value.get("updated_at") or value.get("created_at")
        subject = value.get("cluster_title") or value.get("cluster_id") or path.parent.name
        for attempt in value.get("provider_attempts") or []:
            provider = attempt.get("provider")
            if provider not in {"antigravity", "codex"}:
                continue
            calls.append({
                "provider": provider,
                "status": attempt.get("status", "unknown"),
                "observed_at": observed_at,
                "source": "librarian",
                "subject": str(subject)[:180],
                "profile": attempt.get("profile"),
                "error": attempt.get("error"),
            })
    return calls


def _latest_file_activity(root: Path, patterns: tuple[str, ...]):
    """Return metadata for the newest matching file without reading its contents."""
    if not root.is_dir():
        return None
    latest = None
    for pattern in patterns:
        try:
            candidates = root.rglob(pattern)
            for path in candidates:
                try:
                    if not path.is_file() or path.is_symlink():
                        continue
                    modified = path.stat().st_mtime
                except OSError:
                    continue
                if latest is None or modified > latest[0]:
                    latest = (modified, path)
        except OSError:
            continue
    if latest is None:
        return None
    modified, path = latest
    return {
        "observed_at": dt.datetime.fromtimestamp(modified).astimezone().isoformat(timespec="seconds"),
        "artifact": path.name[:180],
    }


def general_provider_activity():
    """Observe provider use from file metadata only; never inspect prompts or outputs."""
    definitions = (
        ("codex", CODEX_SESSIONS, ("*.jsonl",), "Sessione Codex aggiornata", "App / VS Code / CLI"),
        ("antigravity", ANTIGRAVITY_CLI, ("history.jsonl", "log/*.log"), "Sessione Antigravity aggiornata", "CLI"),
        ("antigravity", ANTIGRAVITY_APP, ("conversations/*.db", "log/*.log"), "Sessione Antigravity aggiornata", "App / estensione VS Code"),
        ("antigravity", ANTIGRAVITY_IDE, ("conversations/*.db", "log/*.log"), "Sessione Antigravity aggiornata", "IDE"),
    )
    activity = []
    for provider, root, patterns, subject, client in definitions:
        newest = _latest_file_activity(root, patterns)
        if not newest:
            continue
        activity.append({
            "provider": provider,
            "status": "observed",
            "observed_at": newest["observed_at"],
            "source": "general",
            "subject": subject,
            "profile": client,
            "client": client,
            "artifact": newest["artifact"],
            "privacy": "metadata_only",
        })
    activity.sort(key=lambda item: time_key(item.get("observed_at")), reverse=True)
    return activity


def quota_error(error):
    return bool(re.search(r"(?i)\b(?:insufficient_quota|rate_limit_exceeded|quota[_ ]exhausted|quota (?:reached|exceeded|exhausted)|usage limit (?:reached|exceeded)|rate limit (?:reached|exceeded)|too many requests|(?:http|status|error)\s*429)\b", str(error or "")))


def provider_observations(installed, extra_calls=None, general_activity=None):
    """Report orchestrated results and general metadata-only activity separately."""
    observations = {
        name: {
            "installed": bool(is_installed),
            "status": "unknown" if is_installed else "not_installed",
            "observed_at": None,
            "error": None,
            "quota_exhausted": False,
            "client_activity": [],
            "last_orchestrated_call": None,
            "last_quota_check": {"status": "unknown", "observed_at": None, "source": None},
        }
        for name, is_installed in installed.items()
    }
    calls = librarian_provider_calls() + list(extra_calls or [])
    calls.sort(key=lambda item: time_key(item.get("observed_at")), reverse=True)
    unresolved = {name for name, value in observations.items() if value["installed"]}
    for call in calls:
        name = call.get("provider")
        if name not in unresolved:
            continue
        error = call.get("error")
        status = call.get("status", "unknown")
        observations[name].update({
            "status": status,
            "observed_at": call.get("observed_at"),
            "error": error,
            "quota_exhausted": status == "unavailable" and quota_error(error),
            "source": call.get("source"),
            "subject": call.get("subject"),
            "profile": call.get("profile"),
        })
        observations[name]["last_orchestrated_call"] = dict(call)
        observations[name]["last_quota_check"] = {
            "status": "usable" if status == "completed" else "exhausted" if observations[name]["quota_exhausted"] else "unknown",
            "observed_at": call.get("observed_at"), "source": call.get("source"),
        }
        unresolved.remove(name)
    # A network failure is not a quota measurement. Keep the latest conclusive check.
    checked = set()
    for call in calls:
        provider = call.get("provider")
        if provider not in observations or provider in checked:
            continue
        status = call.get("status")
        quota_status = "usable" if status == "completed" else "exhausted" if status == "unavailable" and quota_error(call.get("error")) else None
        if quota_status:
            observations[provider]["last_quota_check"] = {"status": quota_status,
                "observed_at": call.get("observed_at"), "source": call.get("source"), "team_id": call.get("team_id")}
            checked.add(provider)
    for activity in sorted(general_activity or [], key=lambda item: time_key(item.get("observed_at")), reverse=True):
        name = activity.get("provider")
        if name in observations:
            observations[name]["client_activity"].append({
                "client": activity.get("client") or activity.get("profile"),
                "observed_at": activity.get("observed_at"), "source": "general", "privacy": "metadata_only",
            })
            if observations[name].get("general_activity"):
                continue
            observations[name]["general_activity"] = {
                "observed_at": activity.get("observed_at"),
                "source": activity.get("source"),
                "subject": activity.get("subject"),
                "privacy": activity.get("privacy"),
            }
    return observations


def profile_system(name: str) -> str:
    return "Ruflo Team" if name.startswith("ruflo_") else "Librarian"


def profile_responsibility(name: str) -> str:
    if name.startswith("ruflo_"):
        return "Investiga o revisiona file in una copia isolata e redatta; non può modificare il progetto."
    if name.endswith("_cluster_semantic_reviewer"):
        return "Corregge una proposta semantica del Librarian che non ha superato la validazione deterministica."
    if name.endswith("_cluster_semantic"):
        return "Propone la semantica minima di un cluster del Second Brain da un workspace in sola lettura."
    if name.endswith("_memory"):
        return "Consulta la memoria Ruflo a supporto del Librarian senza scrivere contenuti canonici."
    if "materializer" in name:
        return "Profilo storico di materializzazione, sostituito dall'esecutore Python deterministico."
    if "planner" in name:
        return "Profilo storico di pianificazione semantica, conservato nel registro CAO ma non selezionato dal runtime attuale."
    if "cluster" in name:
        return "Profilo storico del precedente flusso per cluster; non e selezionato dal runtime attuale."
    return "Profilo storico del Librarian; resta installato in CAO ma non e selezionato dal runtime attuale."


def profile_function(name: str) -> str:
    if name.startswith("ruflo_"):
        return "Investigatore o revisore"
    if name.endswith("_cluster_semantic_reviewer"):
        return "Revisore semantico"
    if name.endswith("_cluster_semantic"):
        return "Librarian semantico"
    if name.endswith("_memory"):
        return "Specialista memoria"
    return "Profilo storico"


def reconciled_attempt_status(task, attempt):
    status = attempt.get("status", "unknown")
    if status != "unavailable":
        return status
    # A successful later attempt must never rewrite an earlier failed attempt.
    error = str(attempt.get("error") or "")
    if (error.startswith("Provider technical failure:")
            and re.search(r"(?im)^\s*VERDICT:\s*(PASS|BLOCKED)\s*$", error)
            and not re.search(r"CAO returned status (error|failed|timeout|timed_out)", error)):
        return "completed"
    return status


def ruflo_team_snapshot():
    """Return bounded team, role and task telemetry without invoking Ruflo or AI."""
    value = read_json(RUFLO_TEAM_STATE, {"teams": {}})
    teams = value.get("teams", {}) if isinstance(value, dict) else {}
    if not isinstance(teams, dict):
        return {
            "available": False, "active_teams": 0, "active_tasks": 0,
            "running_tasks": 0, "paused_quota": 0, "registered_agents": 0,
            "teams": [], "tasks": [], "agents": [], "provider_calls": [],
        }
    active = [team for team in teams.values() if isinstance(team, dict) and team.get("status") == "active"]
    tasks = [task for team in active for task in (team.get("tasks", {}) or {}).values() if isinstance(task, dict)]
    ordered = sorted(
        (team for team in teams.values() if isinstance(team, dict)),
        key=lambda item: time_key(item.get("updated_at")), reverse=True,
    )[:12]
    rows = []
    task_rows = []
    agent_rows = []
    provider_calls = []
    for team in ordered:
        team_tasks = [task for task in (team.get("tasks", {}) or {}).values() if isinstance(task, dict)]
        team_tasks.sort(key=lambda item: time_key(item.get("updated_at")), reverse=True)
        agents = team.get("agents", {}) or {}
        project_path = str(team.get("project_path", ""))
        writer_provider = team.get("writer_provider")
        coordinator_provider = team.get("coordinator_provider") or writer_provider
        reviewer_provider = team.get("reviewer_provider")
        unavailable_providers = team.get("unavailable_providers") or {}
        if not isinstance(unavailable_providers, dict):
            unavailable_providers = {}
        available_providers = [provider for provider in ("codex", "antigravity") if provider not in unavailable_providers]
        provider_separation = bool(
            writer_provider and coordinator_provider == writer_provider
            and reviewer_provider and writer_provider != reviewer_provider
        )
        rows.append({
            "team_id": team.get("team_id", "unknown"),
            "objective": str(team.get("objective", ""))[:240],
            "project": Path(project_path).name if project_path else "—",
            "status": team.get("status", "unknown"),
            "tasks": len(team_tasks),
            "open_tasks": sum(task.get("status") in {"pending", "in_progress", "review", "blocked"} for task in team_tasks),
            "circuit": (team.get("circuit", {}) or {}).get("state", "unknown"),
            "writer_provider": writer_provider,
            "coordinator_provider": coordinator_provider,
            "reviewer_provider": reviewer_provider,
            "provider_separation": provider_separation,
            "degraded_failover": bool(team.get("degraded_failover")),
            "last_review_mode": team.get("last_review_mode"),
            "last_handoff": team.get("last_handoff"),
            "unavailable_providers": sorted(unavailable_providers),
            "created_at": team.get("created_at"),
            "updated_at": team.get("updated_at"),
        })
        for task in team_tasks:
            role = task.get("role", "unknown")
            attempts = task.get("provider_attempts") or []
            assigned_provider = task.get("current_provider") or task.get("provider")
            if task.get("status") != "completed" and assigned_provider in unavailable_providers:
                assigned_provider = None
            if not assigned_provider and role in {"coordinator", "executor"}:
                assigned_provider = writer_provider if writer_provider in available_providers else None
            if not assigned_provider and role == "reviewer":
                assigned_provider = reviewer_provider if reviewer_provider in available_providers else next(iter(available_providers), None)
            if not assigned_provider and role == "investigator":
                assigned_provider = "codex o antigravity" if len(available_providers) == 2 else next(iter(available_providers), None)
            task_rows.append({
                "team_id": team.get("team_id", "unknown"),
                "task_id": task.get("task_id", "unknown"),
                "role": role,
                "agent_id": agents.get(role),
                "description": str(task.get("description", ""))[:360],
                "priority": task.get("priority", "normal"),
                "status": task.get("status", "unknown"),
                "progress": int(task.get("progress", 0) or 0),
                "provider": assigned_provider,
                "reviewed_writer_provider": task.get("reviewed_writer_provider"),
                "review_independent": task.get("review_independent"),
                "review_mode": task.get("review_mode"),
                "worker_verdict": task.get("worker_verdict"),
                "fallback_used": any(attempt.get("status") == "unavailable" for attempt in attempts),
                "updated_at": task.get("updated_at"),
                "result_excerpt": " ".join(str(task.get("result", "")).split())[:240],
            })
            for attempt in attempts:
                provider = attempt.get("provider")
                if provider not in {"antigravity", "codex"}:
                    continue
                provider_calls.append({
                    "provider": provider,
                    "status": reconciled_attempt_status(task, attempt),
                    "recorded_status": attempt.get("status", "unknown"),
                    "observed_at": attempt.get("ended_at") or attempt.get("started_at") or task.get("updated_at"),
                    "source": "ruflo",
                    "subject": str(task.get("description", ""))[:180],
                    "profile": attempt.get("profile"),
                    "team_id": team.get("team_id"),
                    "task_id": task.get("task_id"),
                    "error": None if reconciled_attempt_status(task, attempt) == "completed" else attempt.get("error"),
                })
        for role, agent_id in agents.items():
            role_tasks = [task for task in team_tasks if task.get("role") == role]
            current = next((task for task in role_tasks if task.get("status") == "in_progress"), None)
            current = current or next((task for task in role_tasks if task.get("status") in {"pending", "review", "blocked"}), None)
            if team.get("status") == "stopped":
                status = "stopped"
            elif current and current.get("status") == "in_progress":
                status = "working"
            elif current and current.get("status") == "blocked":
                status = "blocked"
            elif current:
                status = "assigned"
            else:
                status = "idle"
            assigned_provider = (current or {}).get("current_provider") or (current or {}).get("provider")
            if assigned_provider in unavailable_providers:
                assigned_provider = None
            if not assigned_provider and role in {"coordinator", "executor"}:
                assigned_provider = writer_provider if writer_provider in available_providers else None
            if not assigned_provider and role == "reviewer":
                assigned_provider = reviewer_provider if reviewer_provider in available_providers else next(iter(available_providers), None)
            if not assigned_provider and role == "investigator":
                assigned_provider = "codex o antigravity" if len(available_providers) == 2 else next(iter(available_providers), None)
            if role in {"coordinator", "executor"}:
                provider_rule = "Provider della sessione VS Code; dopo handoff il provider esaurito resta escluso"
            elif role == "reviewer":
                provider_rule = "Preferisce un provider diverso dall'autore; non interroga provider esclusi dall'handoff"
            else:
                provider_rule = "Fallback automatico tra i provider ancora disponibili"
            agent_rows.append({
                "team_id": team.get("team_id", "unknown"),
                "agent_id": agent_id,
                "role": role,
                "status": status,
                "task": str((current or {}).get("description", ""))[:240],
                "provider": assigned_provider,
                "eligible_providers": available_providers,
                "provider_rule": provider_rule,
                "responsibility": RUFLO_ROLE_RESPONSIBILITIES.get(role, "Ruolo registrato nel team Ruflo."),
                "updated_at": (current or {}).get("updated_at") or team.get("updated_at"),
            })
    task_rows.sort(key=lambda item: time_key(item.get("updated_at")), reverse=True)
    agent_rows.sort(key=lambda item: (item.get("status") != "working", -time_key(item.get("updated_at"))))
    provider_calls.sort(key=lambda item: time_key(item.get("observed_at")), reverse=True)
    return {
        "available": RUFLO_TEAM_STATE.is_file(),
        "active_teams": len(active),
        "active_tasks": sum(task.get("status") in {"pending", "in_progress", "review", "blocked"} for task in tasks),
        "running_tasks": sum(task.get("status") == "in_progress" for task in tasks),
        "paused_quota": sum((team.get("circuit", {}) or {}).get("state") == "PAUSED_QUOTA" for team in active),
        "teams": rows,
        "tasks": task_rows[:48],
        "agents": agent_rows[:48],
        "provider_calls": provider_calls[:48],
        "registered_agents": sum(len((team.get("agents", {}) or {})) for team in active),
        "retained_teams": len(teams),
    }


def cao_fleet_snapshot(raw_profiles):
    """Read installed ecosystem profiles and recent CAO terminals."""
    sessions = [item for item in cao_get("/sessions", []) if isinstance(item, dict)]
    relevant_sessions = [item for item in sessions if ecosystem_profile(item.get("agent_profile"))][:24]
    terminals = []
    for session in relevant_sessions:
        name = session.get("name") or session.get("id")
        if not name:
            continue
        path = "/sessions/" + urllib.parse.quote(str(name), safe="") + "/terminals"
        for terminal in cao_get(path, []):
            if not isinstance(terminal, dict):
                continue
            terminals.append({
                "terminal_id": terminal.get("id"),
                "session": name,
                "profile": terminal.get("agent_profile") or session.get("agent_profile"),
                "provider": terminal.get("provider"),
                "status": session.get("status") or terminal.get("status") or "unknown",
                "last_active": terminal.get("last_active"),
                "workspace": Path(str(terminal.get("working_directory") or session.get("working_directory") or "")).parent.name,
            })
    terminals.sort(key=lambda item: time_key(item.get("last_active")), reverse=True)
    active_states = {"running", "working", "busy", "initializing", "starting"}
    profiles = []
    builtin_profiles = []
    for item in raw_profiles:
        name = item.get("name")
        if not ecosystem_profile(name):
            builtin_profiles.append({"name": name, "system": "CAO integrato" if item.get("source") == "built-in" else "CAO aggiuntivo",
                "lifecycle": "builtin", "status": "configured" if item.get("loadable") else "invalid",
                "role": item.get("role") or "worker", "provider": "secondo configurazione",
                "function": "Profilo generico", "responsibility": "Profilo registrato in CAO; non selezionato dalla pipeline corrente."})
            continue
        activity = next((terminal for terminal in terminals if terminal.get("profile") == name), None)
        provider = (activity or {}).get("provider")
        if not provider:
            provider = "antigravity" if "antigravity" in name else "codex" if "codex" in name else "local"
        profiles.append({
            "name": name,
            "description": str(item.get("description", ""))[:240],
            "system": profile_system(name),
            "responsibility": profile_responsibility(name),
            "function": profile_function(name),
            "role": item.get("role") or "worker",
            "provider": provider,
            "loadable": bool(item.get("loadable")),
            "source": item.get("source"),
            "lifecycle": "current" if name in CURRENT_ECOSYSTEM_PROFILES else "legacy",
            "status": "historical" if name not in CURRENT_ECOSYSTEM_PROFILES else "working" if activity and activity.get("status") in active_states else "configured" if item.get("loadable") else "invalid",
            "last_active": (activity or {}).get("last_active"),
        })
    profiles.sort(key=lambda item: (
        item.get("status") != "working", item.get("lifecycle") != "current", item.get("name", ""),
    ))
    return {
        "profiles": profiles,
        "builtin_profiles": builtin_profiles,
        "terminals": terminals[:24],
        "active_terminals": sum(item.get("status") in active_states for item in terminals),
        "current_profiles": sum(item.get("lifecycle") == "current" for item in profiles),
        "legacy_profiles": sum(item.get("lifecycle") == "legacy" for item in profiles),
    }


def last_service_events(limit=8):
    try:
        lines = SERVICE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append({
            "time": value.get("time"),
            "event": value.get("event"),
            "batch": value.get("batch"),
            "returncode": value.get("returncode"),
        })
    return events


def dashboard_status():
    circuit = read_json(PROVIDER_CIRCUIT, {"state": "unknown"})
    sources = source_snapshot()
    service = launch_agent(SERVICE_LABEL)
    cao_agent = launch_agent(CAO_LABEL)
    health = cao_get("/health", {})
    installed_providers = {
        item.get("name"): bool(item.get("installed"))
        for item in cao_get("/agents/providers", [])
    }
    ruflo_teams = ruflo_team_snapshot()
    raw_profiles = [item for item in cao_get("/agents/profiles", []) if isinstance(item, dict)]
    cao_fleet = cao_fleet_snapshot(raw_profiles)
    general_activity = general_provider_activity()
    provider_calls = librarian_provider_calls() + ruflo_teams.get("provider_calls", []) + general_activity
    provider_calls.sort(key=lambda item: time_key(item.get("observed_at")), reverse=True)
    provider_state = provider_observations({
        "antigravity": installed_providers.get("antigravity_cli", False),
        "codex": installed_providers.get("codex", False),
    }, ruflo_teams.get("provider_calls", []), general_activity)
    if circuit.get("state") in {"open", "half_open"}:
        overall = "paused"
    elif health.get("status") != "ok":
        overall = "attention"
    elif service.get("state") == "running":
        overall = "working"
    elif sources["pending_ready"]:
        overall = "queued"
    else:
        overall = "ready"
    active_workers = (1 if service.get("state") == "running" else 0) + ruflo_teams["running_tasks"]
    return {
        "generated_at": iso(),
        "overall": overall,
        "sources": sources,
        "knowledge_count": len(list(KNOWLEDGE.rglob("*.md"))),
        "area_count": len(list(AREAS.rglob("*.md"))),
        "service": service,
        "cao": {"health": health.get("status") == "ok", "launch_agent": cao_agent},
        "providers": {
            **provider_state,
            "primary": "antigravity",
            "fallback": "codex",
            "calls": provider_calls[:24],
        },
        "circuit": circuit,
        "ruflo": {
            "installed": (RUFLO_ROOT / "node_modules/.bin/ruflo").is_file(),
            "memory_db": (RUFLO_ROOT / "data/memory.db").is_file(),
            "role": "consultive_memory",
            "teams": ruflo_teams,
        },
        "agents": {
            "profiles": cao_fleet["profiles"],
            "builtin_profiles": cao_fleet["builtin_profiles"],
            "configured_profiles": [item["name"] for item in cao_fleet["profiles"]],
            "configured_count": len(cao_fleet["profiles"]),
            "current_profile_count": cao_fleet["current_profiles"],
            "legacy_profile_count": cao_fleet["legacy_profiles"],
            "active_workers": active_workers,
            "max_parallel_ai_workers": 1,
            "execution_model": "sequential_role_pipeline",
            "cao_terminals": cao_fleet["terminals"],
        },
        "agent_configuration": agent_inventory(),
        "runs": recent_runs(),
        "service_events": last_service_events(),
    }


def kickstart_service():
    process = subprocess.run(
        ["launchctl", "kickstart", f"gui/{os.getuid()}/{SERVICE_LABEL}"],
        text=True, capture_output=True, timeout=15,
    )
    if process.returncode:
        raise RuntimeError((process.stderr or process.stdout or "launchctl failed").strip())


def reset_circuit():
    process = subprocess.run(
        [str(ORGANIZER), "--reset-provider-circuit"],
        text=True, capture_output=True, timeout=30,
    )
    if process.returncode:
        raise RuntimeError((process.stderr or process.stdout or "reset failed")[-1200:])


def pause_circuit():
    at = now()
    PROVIDER_CIRCUIT_LOCK.touch(exist_ok=True)
    with PROVIDER_CIRCUIT_LOCK.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = read_json(PROVIDER_CIRCUIT, {})
        state.update({
            "version": "provider-circuit-1.0",
            "state": "open",
            "opened_at": at.isoformat(timespec="seconds"),
            "next_probe_at": "2099-01-01T00:00:00+01:00",
            "probe_started_at": None,
            "manual_pause": True,
            "last_errors": [{"provider": "dashboard", "status": "paused", "error": "Paused by user"}],
            "updated_at": at.isoformat(timespec="seconds"),
        })
        atomic_json(PROVIDER_CIRCUIT, state)


def retry_source(source_note: str | None):
    AI_INVALID_LOCK.touch(exist_ok=True)
    with AI_INVALID_LOCK.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = read_json(AI_INVALID_STATE, {"version": "ai-invalid-state-1.0", "sources": {}})
        sources = state.setdefault("sources", {})
        if source_note:
            if source_note not in sources:
                raise ValueError("Source is not in AI-invalid backoff")
            sources.pop(source_note)
        else:
            sources.clear()
        state["updated_at"] = iso()
        atomic_json(AI_INVALID_STATE, state)
    kickstart_service()


class Handler(BaseHTTPRequestHandler):
    server_version = "SecondBrainDashboard/1.0"

    def log_message(self, format, *args):
        return

    def send_bytes(self, status, data: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, value):
        self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        routes = {
            "/": (ASSETS / "index.html", "text/html; charset=utf-8"),
            "/dashboard.css": (ASSETS / "dashboard.css", "text/css; charset=utf-8"),
            "/dashboard.js": (ASSETS / "dashboard.js", "text/javascript; charset=utf-8"),
        }
        if self.path == "/api/status":
            self.send_json(200, dashboard_status())
            return
        route = routes.get(self.path)
        if not route:
            self.send_json(404, {"error": "not found"})
            return
        path, content_type = route
        try:
            self.send_bytes(200, path.read_bytes(), content_type)
        except OSError:
            self.send_json(404, {"error": "asset missing"})

    def do_POST(self):
        if self.headers.get(ACTION_HEADER) != "1":
            self.send_json(403, {"error": "local dashboard header required"})
            return
        origin = self.headers.get("Origin")
        expected_origins = allowed_dashboard_origins(*self.server.server_address[:2])
        if origin and origin not in expected_origins:
            self.send_json(403, {"error": "invalid origin"})
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0") or 0), 8192)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/run-now":
                kickstart_service()
            elif self.path == "/api/pause":
                pause_circuit()
            elif self.path == "/api/resume":
                reset_circuit()
                kickstart_service()
            elif self.path == "/api/retry-invalid":
                source_note = body.get("source_note")
                if source_note is not None and not isinstance(source_note, str):
                    raise ValueError("invalid source_note")
                retry_source(source_note)
            else:
                self.send_json(404, {"error": "not found"})
                return
            self.send_json(200, {"ok": True})
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            self.send_json(400, {"error": str(exc)[-1200:]})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Dashboard may bind only to a loopback address")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Second Brain dashboard: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
