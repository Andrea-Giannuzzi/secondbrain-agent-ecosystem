#!/usr/bin/env python3
"""Read-only, fail-closed access to the canonical Second Brain index and graph."""
from __future__ import annotations

import fcntl
import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import onnxruntime

onnxruntime.disable_telemetry_events()
from qdrant_client import QdrantClient, models

from secondbrain_access import CANONICAL_FOLDERS, classify_file, migration_complete, safe_vault_file

try:
    from secondbrain_redaction import redact_sensitive
except ImportError:
    _vault_for_import = Path(
        os.environ.get("SECOND_BRAIN_VAULT", "~/Documents/SecondBrain")
    ).expanduser().resolve()
    import sys

    sys.path.insert(0, str(_vault_for_import / "_System/Librarian/Runtime"))
    from secondbrain_redaction import redact_sensitive


VAULT = Path(os.environ.get("SECOND_BRAIN_VAULT", "~/Documents/SecondBrain")).expanduser().resolve()
STATE = Path(
    os.environ.get("SECOND_BRAIN_SEMANTIC_STATE", "~/Library/Application Support/SecondBrainSemantic")
).expanduser().resolve()
DB_PATH = STATE / "qdrant"
MANIFEST_PATH = STATE / "manifest.json"
LOCK_PATH = STATE / "qdrant.lock"
COLLECTION = "secondbrain_v1"
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MAX_SEARCH_RESULTS = 10
MAX_NOTE_CHARS = 20_000
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_PROCESS_LOCK = threading.RLock()


def _title(path: Path, text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return redact_sensitive(line[2:].strip() or path.stem)
    return redact_sensitive(path.stem)


def _note_type(path: str) -> str:
    folder = path.split("/", 1)[0]
    return {
        "10_Projects": "project",
        "20_Areas": "area",
        "30_Knowledge": "knowledge",
        "40_Research": "research",
        "50_Sources": "source_evidence",
    }.get(folder, "unknown")


def _bounded_limit(limit: object, default: int, maximum: int = MAX_SEARCH_RESULTS) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def _load_manifest() -> dict[str, object]:
    try:
        value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Semantic index manifest is unavailable") from exc
    if not migration_complete(value):
        raise RuntimeError("Semantic access migration is incomplete; search is disabled")
    return value


@contextmanager
def _locked_client() -> Iterator[QdrantClient]:
    if not DB_PATH.exists():
        raise RuntimeError("Semantic index is unavailable")
    STATE.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.touch(exist_ok=True)
    with _PROCESS_LOCK, LOCK_PATH.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        _load_manifest()
        client = QdrantClient(path=str(DB_PATH))
        try:
            if not client.collection_exists(COLLECTION):
                raise RuntimeError("Semantic collection is unavailable")
            yield client
        finally:
            try:
                client.close()
            except Exception:
                pass


def _snippet(value: object, length: int) -> tuple[str, bool]:
    text = re.sub(r"\s+", " ", redact_sensitive(value)).strip()
    return (text[:length], len(text) > length)


def _search(query: str, access_class: str, limit: int, snippet_chars: int) -> dict[str, object]:
    query = str(query or "").strip()
    if not query:
        raise ValueError("query must not be empty")
    limit = _bounded_limit(limit, 6 if access_class == "canonical" else 4)
    condition = models.Filter(
        must=[
            models.FieldCondition(
                key="access_class",
                match=models.MatchValue(value=access_class),
            )
        ]
    )
    with _locked_client() as client:
        points = client.query_points(
            collection_name=COLLECTION,
            query=models.Document(text=query, model=MODEL),
            query_filter=condition,
            limit=min(100, max(limit * 8, limit)),
            with_payload=True,
        ).points
    results: list[dict[str, object]] = []
    seen: set[str] = set()
    for point in points:
        payload = point.payload or {}
        path = str(payload.get("source_path", ""))
        if not path or path in seen or payload.get("access_class") != access_class:
            continue
        expected_folder = path.split("/", 1)[0]
        if access_class == "canonical" and expected_folder not in CANONICAL_FOLDERS:
            continue
        if access_class == "evidence" and expected_folder != "50_Sources":
            continue
        if classify_file(VAULT, path) != access_class:
            continue
        snippet, truncated = _snippet(payload.get("text", ""), snippet_chars)
        public_path = redact_sensitive(path) if access_class == "evidence" else path
        public_title = redact_sensitive(payload.get("title", ""))
        results.append(
            {
                "path": public_path,
                "title": public_title,
                "type": _note_type(path),
                "score": round(float(point.score), 6),
                "snippet": snippet,
                "truncated": truncated,
            }
        )
        seen.add(path)
        if len(results) >= limit:
            break
    return {"query": query, "results": results, "truncated": len(points) > len(results)}


def search_second_brain(query: str, limit: int = 6) -> dict[str, object]:
    """Search Projects, Areas, Knowledge, and Research only."""
    return _search(query, "canonical", limit, 700)


def search_second_brain_evidence(query: str, limit: int = 4) -> dict[str, object]:
    """Search short, redacted excerpts of Source evidence classified as knowledge."""
    return _search(query, "evidence", limit, 500)


def _safe_canonical_note(raw_path: str) -> tuple[Path, str]:
    value = str(raw_path or "").strip().replace("\\", "/")
    relative = Path(value)
    if not value or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("path must be a relative canonical Markdown path")
    if not relative.suffix:
        relative = relative.with_suffix(".md")
    elif relative.suffix.casefold() != ".md":
        raise PermissionError("Only Markdown notes are readable")
    if not relative.parts or relative.parts[0] not in CANONICAL_FOLDERS:
        raise PermissionError("path is outside the readable canonical folders")
    try:
        resolved = safe_vault_file(VAULT, relative.as_posix())
    except (OSError, ValueError) as exc:
        raise ValueError("canonical note does not exist") from exc
    if not resolved.is_file():
        raise ValueError("canonical note does not exist")
    return resolved, resolved.relative_to(VAULT).as_posix()


def read_second_brain_note(path: str) -> dict[str, object]:
    resolved, relative = _safe_canonical_note(path)
    content = redact_sensitive(resolved.read_text(encoding="utf-8", errors="replace"))
    truncated = len(content) > MAX_NOTE_CHARS
    return {
        "path": relative,
        "title": _title(resolved, content),
        "type": _note_type(relative),
        "score": None,
        "content": content[:MAX_NOTE_CHARS],
        "truncated": truncated,
    }


def _canonical_inventory() -> tuple[dict[str, Path], dict[str, list[str]]]:
    by_path: dict[str, Path] = {}
    by_stem: dict[str, list[str]] = {}
    for folder in sorted(CANONICAL_FOLDERS):
        root = VAULT / folder
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            try:
                _safe_canonical_note(path.relative_to(VAULT).as_posix())
            except (ValueError, OSError):
                continue
            rel = path.relative_to(VAULT).as_posix()
            by_path[rel.removesuffix(".md")] = path
            by_stem.setdefault(path.stem.casefold(), []).append(rel)
    return by_path, by_stem


def _resolve_wikilink(target: str, by_path: dict[str, Path], by_stem: dict[str, list[str]]) -> str | None:
    clean = target.strip().replace("\\", "/").removesuffix(".md")
    if clean in by_path:
        return clean + ".md"
    matches = by_stem.get(Path(clean).name.casefold(), [])
    return matches[0] if len(matches) == 1 else None


def related_second_brain_notes(path: str, limit: int = 12) -> dict[str, object]:
    resolved, relative = _safe_canonical_note(path)
    limit = _bounded_limit(limit, 12, 12)
    by_path, by_stem = _canonical_inventory()
    relations: dict[str, set[str]] = {}
    current_text = resolved.read_text(encoding="utf-8", errors="replace")
    for target in WIKILINK.findall(current_text):
        linked = _resolve_wikilink(target, by_path, by_stem)
        if linked and linked != relative:
            relations.setdefault(linked, set()).add("wikilink")
    for source_no_suffix, source_path in by_path.items():
        source_rel = source_no_suffix + ".md"
        if source_rel == relative:
            continue
        try:
            source_text = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for target in WIKILINK.findall(source_text):
            if _resolve_wikilink(target, by_path, by_stem) == relative:
                relations.setdefault(source_rel, set()).add("backlink")
                break
    ordered = sorted(relations.items(), key=lambda item: item[0].casefold())
    output: list[dict[str, object]] = []
    for related_path, kinds in ordered[:limit]:
        related_file = VAULT / related_path
        text = related_file.read_text(encoding="utf-8", errors="replace")
        output.append(
            {
                "path": related_path,
                "title": _title(related_file, text),
                "type": _note_type(related_path),
                "score": None,
                "snippet": ", ".join(sorted(kinds)),
                "truncated": False,
            }
        )
    return {
        "path": relative,
        "title": _title(resolved, current_text),
        "type": _note_type(relative),
        "relations": output,
        "truncated": len(ordered) > limit,
    }
