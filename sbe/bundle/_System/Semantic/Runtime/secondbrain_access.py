#!/usr/bin/env python3
"""Access classification and metadata migration for the semantic index."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Callable


MANIFEST_VERSION = 2
ACCESS_SCHEMA_VERSION = 1
ACCESS_CLASSES = frozenset({"canonical", "evidence", "restricted"})
CANONICAL_FOLDERS = frozenset({"10_Projects", "20_Areas", "30_Knowledge", "40_Research"})


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def parse_frontmatter(text: str) -> dict[str, object]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    values: dict[str, object] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key, raw = key.strip(), raw.strip()
        if not key:
            continue
        if raw.startswith('"') and raw.endswith('"'):
            try:
                values[key] = json.loads(raw)
            except (ValueError, TypeError):
                values[key] = raw.strip('"')
        elif raw.casefold() in {"true", "false"}:
            values[key] = raw.casefold() == "true"
        else:
            values[key] = raw
    return values


def access_class_for(folder: str, metadata: dict[str, object] | None = None) -> str:
    if folder in CANONICAL_FOLDERS:
        return "canonical"
    if folder == "50_Sources":
        disposition = str((metadata or {}).get("librarian_disposition", "")).strip('"').casefold()
        return "evidence" if disposition == "knowledge" else "restricted"
    return "restricted"


def safe_vault_file(vault: Path, source_path: str) -> Path:
    """Resolve a regular vault file without following any symlink component."""
    relative = Path(source_path)
    if not source_path or relative.is_absolute() or ".." in relative.parts or "\\" in source_path:
        raise ValueError("Invalid relative vault path")
    base = vault.resolve()
    current = base
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PermissionError("Symlink paths are not readable")
    resolved = current.resolve(strict=True)
    resolved.relative_to(base)
    if not resolved.is_file():
        raise ValueError("Vault path is not a regular file")
    return resolved


def classify_file(vault: Path, source_path: str, folder: str | None = None) -> str:
    # The current file and its path are authoritative, not a stale manifest folder.
    try:
        note = safe_vault_file(vault, source_path)
    except (OSError, ValueError):
        return "restricted"
    if note.suffix.casefold() != ".md":
        return "restricted"
    folder = source_path.split("/", 1)[0]
    metadata: dict[str, object] = {}
    if folder == "50_Sources":
        try:
            metadata = parse_frontmatter(note.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return "restricted"
    return access_class_for(folder, metadata)


def migration_complete(manifest: dict[str, object]) -> bool:
    migration = manifest.get("access_migration")
    files = manifest.get("files")
    if not isinstance(migration, dict) or not isinstance(files, dict):
        return False
    if manifest.get("version") != MANIFEST_VERSION:
        return False
    if migration.get("schema_version") != ACCESS_SCHEMA_VERSION or migration.get("complete") is not True:
        return False
    return all(
        isinstance(entry, dict) and entry.get("access_class") in ACCESS_CLASSES
        for entry in files.values()
    )


def migrate_access_payloads(
    client: object,
    collection: str,
    manifest: dict[str, object],
    vault: Path,
    checkpoint: Callable[[dict[str, object]], None],
) -> int:
    """Classify existing point IDs in place. Safe to resume after interruption."""
    if migration_complete(manifest):
        return 0
    files = manifest.setdefault("files", {})
    if not isinstance(files, dict):
        raise ValueError("Invalid semantic manifest: files must be an object")
    manifest["version"] = MANIFEST_VERSION
    manifest["access_migration"] = {
        "schema_version": ACCESS_SCHEMA_VERSION,
        "complete": False,
        "updated_at": now_iso(),
    }
    checkpoint(manifest)
    updated = 0
    for source_path in sorted(files):
        entry = files[source_path]
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid semantic manifest entry: {source_path}")
        desired = classify_file(vault, source_path, str(entry.get("folder") or ""))
        point_ids = entry.get("point_ids", [])
        if not isinstance(point_ids, list):
            raise ValueError(f"Invalid point IDs in semantic manifest: {source_path}")
        if entry.get("access_class") != desired:
            if point_ids:
                client.set_payload(
                    collection_name=collection,
                    payload={"access_class": desired},
                    points=point_ids,
                    wait=True,
                )
            entry["access_class"] = desired
            updated += 1
            checkpoint(manifest)
    manifest["access_migration"] = {
        "schema_version": ACCESS_SCHEMA_VERSION,
        "complete": True,
        "updated_at": now_iso(),
    }
    checkpoint(manifest)
    return updated
