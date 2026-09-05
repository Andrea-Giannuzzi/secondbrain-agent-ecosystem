#!/usr/bin/env python3
from __future__ import annotations
import fcntl, hashlib, json, os, re, uuid
from pathlib import Path
import onnxruntime

# Local embeddings must not submit native runtime telemetry. This also avoids
# the macOS telemetry worker crash observed during isolated release checks.
onnxruntime.disable_telemetry_events()
from qdrant_client import QdrantClient, models

from secondbrain_access import (
    MANIFEST_VERSION,
    access_class_for,
    migrate_access_payloads,
    migration_complete,
    safe_vault_file,
)

VAULT = Path(os.environ.get("SECOND_BRAIN_VAULT", "~/Documents/SecondBrain")).expanduser().resolve()
STATE = Path(os.environ.get("SECOND_BRAIN_SEMANTIC_STATE", "~/Library/Application Support/SecondBrainSemantic")).expanduser().resolve()
DB_PATH = STATE / "qdrant"
MANIFEST_PATH = STATE / "manifest.json"
LOCK_PATH = STATE / "qdrant.lock"
LOG_PATH = VAULT / "_System" / "Logs" / "semantic-indexer.jsonl"
COLLECTION = "secondbrain_v1"
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
INDEX_DIRS = ["10_Projects","20_Areas","30_Knowledge","40_Research","50_Sources"]
CHUNK_TARGET = 1700
CHUNK_OVERLAP = 250

def now_iso():
    import datetime as dt
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")

def log(event, **data):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": now_iso(), "event": event, **data}, ensure_ascii=False) + "\n")

def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()

def load_manifest():
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"version": MANIFEST_VERSION, "files": {}}

def save_manifest(data):
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MANIFEST_PATH)

def checkpoint_manifest(manifest):
    manifest["model"] = MODEL
    manifest["collection"] = COLLECTION
    manifest["updated_at"] = now_iso()
    save_manifest(manifest)

def parse_frontmatter(text):
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    out = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key, raw = key.strip(), raw.strip()
        if not key:
            continue
        if raw.startswith('"') and raw.endswith('"'):
            try:
                out[key] = json.loads(raw)
            except Exception:
                out[key] = raw.strip('"')
        else:
            out[key] = raw
    return out

def title_from_markdown(path, text):
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or path.stem
    return path.stem

def normalize_text(text):
    text = text.replace("\x00", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()

def split_large_paragraph(paragraph, target):
    if len(paragraph) <= target:
        return [paragraph]
    sentences = re.split(r"(?<=[.!?;:])\s+", paragraph)
    chunks, cur = [], ""
    for sent in sentences:
        candidate = (cur + " " + sent).strip() if cur else sent
        if len(candidate) <= target:
            cur = candidate
        else:
            if cur:
                chunks.append(cur)
            if len(sent) <= target:
                cur = sent
            else:
                chunks.extend(sent[i:i+target] for i in range(0, len(sent), target))
                cur = ""
    if cur:
        chunks.append(cur)
    return chunks

def chunk_text(text):
    text = normalize_text(text)
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    expanded = []
    for p in paras:
        expanded.extend(split_large_paragraph(p, CHUNK_TARGET))
    chunks, cur = [], ""
    for p in expanded:
        candidate = p if not cur else cur + "\n\n" + p
        if len(candidate) <= CHUNK_TARGET:
            cur = candidate
        else:
            if cur:
                chunks.append(cur)
                tail = cur[-CHUNK_OVERLAP:] if CHUNK_OVERLAP else ""
                cur = (tail + "\n\n" + p).strip()
            else:
                chunks.append(p[:CHUNK_TARGET])
                cur = p[max(0, CHUNK_TARGET-CHUNK_OVERLAP):]
    if cur:
        chunks.append(cur)
    seen, unique = set(), []
    for c in chunks:
        key = sha256_text(c)
        if key not in seen and len(c.strip()) >= 40:
            seen.add(key)
            unique.append(c.strip())
    return unique

def scan_documents():
    docs = {}
    for dirname in INDEX_DIRS:
        root = VAULT / dirname
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            try:
                path = safe_vault_file(VAULT, path.relative_to(VAULT).as_posix())
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            rel = path.relative_to(VAULT).as_posix()
            fm = parse_frontmatter(text)
            title = title_from_markdown(path, text)
            parts = [("note", rel, text)]
            extracted_rel = fm.get("extracted_text")
            if dirname == "50_Sources" and extracted_rel:
                try:
                    ep = safe_vault_file(VAULT, str(extracted_rel).strip('"'))
                    ep.relative_to(VAULT / "_System/Extracted")
                    et = ep.read_text(encoding="utf-8", errors="replace")
                    parts.append(("extracted_source", ep.relative_to(VAULT).as_posix(), et))
                except (OSError, ValueError):
                    pass
            combined = "\n---PART---\n".join(f"{k}\n{p}\n{t}" for k,p,t in parts)
            docs[rel] = {
                "title": title,
                "folder": dirname,
                "hash": sha256_text(combined),
                "parts": parts,
                "access_class": access_class_for(dirname, fm),
            }
    return docs

def deterministic_id(source_path, content_path, chunk_index, text):
    raw = f"{source_path}|{content_path}|{chunk_index}|{sha256_text(text)}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))

def ensure_collection(client):
    if not client.collection_exists(COLLECTION):
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=models.VectorParams(
                size=client.get_embedding_size(MODEL),
                distance=models.Distance.COSINE
            )
        )

def delete_ids(client, ids):
    if ids:
        client.delete(
            collection_name=COLLECTION,
            points_selector=models.PointIdsList(points=ids),
            wait=True
        )

def upload_document(client, source_path, doc):
    ids, vector_docs, payloads = [], [], []
    global_chunk = 0
    for kind, content_path, text in doc["parts"]:
        for local_idx, chunk in enumerate(chunk_text(text)):
            pid = deterministic_id(source_path, content_path, local_idx, chunk)
            ids.append(pid)
            vector_docs.append(models.Document(text=chunk, model=MODEL))
            payloads.append({
                "source_path": source_path,
                "content_path": content_path,
                "title": doc["title"],
                "folder": doc["folder"],
                "content_kind": kind,
                "chunk_index": global_chunk,
                "text": chunk,
                "access_class": doc["access_class"],
            })
            global_chunk += 1
    if vector_docs:
        client.upload_collection(
            collection_name=COLLECTION,
            vectors=vector_docs,
            ids=ids,
            payload=payloads,
            wait=True
        )
    return ids

def main():
    STATE.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.touch(exist_ok=True)
    with LOCK_PATH.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        manifest = load_manifest()
        previous = manifest.setdefault("files", {})
        current = scan_documents()
        deleted = sorted(set(previous) - set(current))
        changed = sorted(
            p for p, info in current.items()
            if p not in previous or previous[p].get("hash") != info["hash"]
        )
        needs_access_migration = not migration_complete(manifest)
        if not deleted and not changed and not needs_access_migration:
            log("no_changes")
            return 0
        log(
            "index_started",
            changed=len(changed),
            deleted=len(deleted),
            access_migration=needs_access_migration,
            model=MODEL,
        )
        DB_PATH.mkdir(parents=True, exist_ok=True)
        client = QdrantClient(path=str(DB_PATH))
        ensure_collection(client)
        for rel in deleted:
            delete_ids(client, previous.get(rel, {}).get("point_ids", []))
            previous.pop(rel, None)
            checkpoint_manifest(manifest)
            log("removed", source_path=rel)
        for rel in changed:
            delete_ids(client, previous.get(rel, {}).get("point_ids", []))
            ids = upload_document(client, rel, current[rel])
            previous[rel] = {
                "hash": current[rel]["hash"],
                "point_ids": ids,
                "title": current[rel]["title"],
                "folder": current[rel]["folder"],
                "access_class": current[rel]["access_class"],
                "indexed_at": now_iso()
            }
            # Persist progress after every Source. A timeout or interruption then
            # resumes from the next changed file instead of re-embedding the vault.
            checkpoint_manifest(manifest)
            log("indexed", source_path=rel, chunks=len(ids))
        migrated = migrate_access_payloads(
            client=client,
            collection=COLLECTION,
            manifest=manifest,
            vault=VAULT,
            checkpoint=checkpoint_manifest,
        )
        checkpoint_manifest(manifest)
        try:
            client.close()
        except Exception:
            pass
        log("index_completed", changed=len(changed), deleted=len(deleted), access_files_updated=migrated)
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
