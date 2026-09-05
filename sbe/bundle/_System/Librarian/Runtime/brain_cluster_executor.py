#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

VAULT = Path(os.environ.get(
    "SECOND_BRAIN_VAULT", "~/Documents/SecondBrain"
)).expanduser().resolve()

SYSTEM = VAULT / "_System" / "Librarian"
TRANSACTIONS = SYSTEM / "Transactions"
LOCK_PATH = SYSTEM / "executor.lock"
AUDIT_PATH = SYSTEM / "executor.jsonl"

REL_START = "<!-- librarian:relationships:start -->"
REL_END = "<!-- librarian:relationships:end -->"


def now():
    return dt.datetime.now().astimezone()


def iso():
    return now().isoformat(timespec="seconds")


def stamp():
    return now().strftime("%Y%m%d-%H%M%S")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(event, **data):
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"time": iso(), "component": "cluster-executor", "event": event, **data}
    with AUDIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def canon(rel):
    rp = Path(rel)

    if rp.is_absolute() or ".." in rp.parts:
        raise ValueError(f"unsafe path: {rel}")

    p = (VAULT / rp).resolve()

    if VAULT not in p.parents:
        raise ValueError(f"path escapes vault: {rel}")

    return p


def ensure_allowed(rel):
    if not rel.endswith(".md"):
        raise ValueError(f"Markdown target required: {rel}")

    if not rel.startswith(
        ("20_Areas/", "30_Knowledge/", "50_Sources/")
    ):
        raise ValueError(
            f"canonical write outside allowed prefixes: {rel}"
        )


def wiki_target(rel):
    return rel[:-3] if rel.endswith(".md") else rel


def line_for(other, relation):
    return (
        f"- [[{wiki_target(other)}|{Path(other).stem}]] "
        f"— `{relation}`"
    )


def inverse(relation):
    return {
        "source_for": "supported_by",
        "supports": "supported_by",
        "derives": "derived_from",
        "part_of": "contains",
        "extends": "extended_by",
        "generalizes": "specializes",
        "specializes": "generalizes",
        "uses": "used_by",
        "contradicts": "contradicts",
        "related_to": "related_to",
    }.get(relation, "related_to")


def managed_links(text, lines):
    existing_lines = []
    preserved_lines = []
    if REL_START in text and REL_END in text:
        a = text.index(REL_START) + len(REL_START)
        b = text.index(REL_END, a)
        for line in text[a:b].splitlines():
            stripped = line.strip()
            if not stripped or stripped == "## Related":
                continue
            if stripped.startswith("- [["):
                existing_lines.append(stripped)
            else:
                preserved_lines.append(line.rstrip())

    lines = sorted(dict.fromkeys(existing_lines + list(lines)))

    if not lines:
        return text

    block = (
        REL_START + "\n"
        "## Related\n\n"
        + ("\n".join(preserved_lines) + "\n" if preserved_lines else "")
        + "\n".join(lines)
        + "\n"
        + REL_END
    )

    if REL_START in text and REL_END in text:
        a = text.index(REL_START)
        b = text.index(REL_END, a) + len(REL_END)

        return (
            text[:a].rstrip()
            + "\n\n"
            + block
            + "\n"
            + text[b:].lstrip()
        )

    return text.rstrip() + "\n\n" + block + "\n"


def append_update(text, update):
    body = update.get(
        "append_markdown", ""
    ).strip()

    if not body:
        return text

    marker = hashlib.sha256(
        (
            update["target_note"]
            + "\n"
            + body
        ).encode()
    ).hexdigest()[:16]

    token = f"<!-- librarian:update:{marker} -->"

    if token in text:
        return text

    summary = update.get("summary", "").strip()

    section = (
        token
        + "\n"
        + f"## Librarian Addition — "
        f"{now().date().isoformat()}\n\n"
    )

    if summary:
        section += summary + "\n\n"

    section += body + "\n"

    return text.rstrip() + "\n\n" + section


def append_moc_update(text, update):
    payload = json.dumps(
        update,
        sort_keys=True,
        ensure_ascii=False
    )

    marker = hashlib.sha256(
        payload.encode()
    ).hexdigest()[:16]

    token = f"<!-- librarian:moc-update:{marker} -->"

    if token in text:
        return text

    section = (
        token
        + "\n"
        + f"## Librarian Update — "
        f"{now().date().isoformat()}\n\n"
    )

    desc = update.get("description", "").strip()

    if desc:
        section += desc + "\n\n"

    for note in update.get(
        "member_notes", []
    ):
        section += (
            f"- [[{wiki_target(note)}|"
            f"{Path(note).stem}]]\n"
        )

    return text.rstrip() + "\n\n" + section


def update_source_status(text, disposition, reason, knowledge_notes=None):
    if not text.startswith("---\n"):
        return text

    end = text.find("\n---\n", 4)

    if end < 0:
        return text

    desired = {
        "status": '"organized"',
        "needs_librarian": "false",
        "librarian_disposition": json.dumps(
            disposition, ensure_ascii=False
        ),
        "librarian_processed_at": json.dumps(
            iso()
        ),
        "librarian_disposition_reason": json.dumps(
            reason[:500], ensure_ascii=False
        ),
    }

    lines = text[4:end].splitlines()
    out = []
    seen = set()

    for line in lines:
        if ":" in line:
            key = line.split(":", 1)[0].strip()

            if key in desired:
                out.append(
                    f"{key}: {desired[key]}"
                )
                seen.add(key)
                continue

        out.append(line)

    for key, value in desired.items():
        if key not in seen:
            out.append(f"{key}: {value}")

    updated = (
        "---\n"
        + "\n".join(out)
        + "\n---\n"
        + text[end + 5:]
    )

    knowledge_notes = sorted(set(knowledge_notes or []))
    if disposition == "knowledge" and knowledge_notes:
        status = (
            "Organized by the Librarian.\n\n"
            "Knowledge:\n"
            + "\n".join(f"- [[{wiki_target(note)}|{Path(note).stem}]]" for note in knowledge_notes)
        )
    elif disposition == "reference":
        status = "Organized as a reference; no reusable concept was identified."
    elif disposition == "administrative":
        status = "Organized as administrative material."
    elif disposition == "low_information":
        status = "Organized as low-information material."
    elif disposition == "sensitive_reference":
        status = "Organized locally as a sensitive reference; its content was not sent to the semantic provider."
    else:
        status = "Organized by the Librarian."

    pattern = re.compile(r"(?ms)^## Status\s*\n.*?(?=^## |\Z)")
    replacement = f"## Status\n\n{status}\n\n"
    if pattern.search(updated):
        return pattern.sub(replacement, updated, count=1)
    return updated.rstrip() + "\n\n" + replacement


def verify_source_snapshot(manifest, vault=VAULT):
    if manifest.get("draft_version") != "cluster-2.1":
        return
    expected = manifest.get("source_sha256")
    source_notes = manifest.get("source_notes", [])
    if not isinstance(expected, dict) or set(expected) != set(source_notes):
        raise RuntimeError("Draft Source snapshot is incomplete.")
    for rel, digest in expected.items():
        path = (vault / rel).resolve()
        if vault not in path.parents or not path.is_file():
            raise RuntimeError(f"Draft Source missing: {rel}")
        if sha(path) != digest:
            raise RuntimeError(f"Draft Source changed after semantic review: {rel}")


def preflight(draft):
    manifest = json.loads(
        (draft / "manifest.json").read_text(
            encoding="utf-8"
        )
    )

    if manifest.get("status") != "CLUSTER_DRAFT_VALID":
        raise RuntimeError(
            "Draft is not CLUSTER_DRAFT_VALID."
        )

    verify_source_snapshot(manifest)

    patches = json.loads(
        (draft / "patches.json").read_text(
            encoding="utf-8"
        )
    )

    if patches.get("non_destructive") is not True:
        raise RuntimeError(
            "patches.json is not non_destructive."
        )

    if patches.get("version") not in {"cluster-1.0", "cluster-2.0"}:
        raise RuntimeError("Unsupported cluster draft version.")

    desired = {}
    new_root = draft / "new"

    if new_root.exists():
        for p in new_root.rglob("*.md"):
            rel = p.relative_to(
                new_root
            ).as_posix()

            ensure_allowed(rel)

            dest = canon(rel)

            if dest.exists():
                raise RuntimeError(
                    f"new target already exists: {rel}"
                )

            desired[rel] = p.read_text(
                encoding="utf-8"
            )

    for u in patches.get(
        "knowledge_updates", []
    ):
        rel = u["target_note"]
        ensure_allowed(rel)
        if not rel.startswith("30_Knowledge/"):
            raise RuntimeError(f"Knowledge update outside 30_Knowledge: {rel}")
        p = canon(rel)

        if not p.exists():
            raise RuntimeError(
                f"update target missing: {rel}"
            )

        base = desired.get(
            rel,
            p.read_text(
                encoding="utf-8",
                errors="replace"
            )
        )

        desired[rel] = append_update(
            base, u
        )

    for u in patches.get(
        "moc_updates", []
    ):
        rel = u["target_note"]
        ensure_allowed(rel)
        if not rel.startswith("20_Areas/"):
            raise RuntimeError(f"MOC update outside 20_Areas: {rel}")
        p = canon(rel)

        if not p.exists():
            raise RuntimeError(
                f"MOC target missing: {rel}"
            )

        base = desired.get(
            rel,
            p.read_text(
                encoding="utf-8",
                errors="replace"
            )
        )

        desired[rel] = append_moc_update(
            base, u
        )

    rel_lines = {}

    planned_new = set(desired)

    for link in patches.get(
        "ensure_links", []
    ):
        src = link["from"]
        dst = link["to"]

        for endpoint in (src, dst):
            ensure_allowed(endpoint)
            p = canon(endpoint)

            if not p.exists() and endpoint not in planned_new:
                raise RuntimeError(
                    f"link endpoint missing: {endpoint}"
                )

        relation = link["relation"]

        rel_lines.setdefault(
            src, []
        ).append(
            line_for(dst, relation)
        )

        rel_lines.setdefault(
            dst, []
        ).append(
            line_for(src, inverse(relation))
        )

    for rel, lines in rel_lines.items():
        p = canon(rel)

        base = desired.get(
            rel,
            p.read_text(
                encoding="utf-8",
                errors="replace"
            )
            if p.exists()
            else ""
        )

        desired[rel] = managed_links(
            base, lines
        )

    for s in patches.get(
        "source_statuses", []
    ):
        rel = s["source_note"]
        ensure_allowed(rel)
        if not rel.startswith("50_Sources/"):
            raise RuntimeError(f"Source status outside 50_Sources: {rel}")
        p = canon(rel)

        if not p.exists():
            raise RuntimeError(
                f"Source missing: {rel}"
            )

        base = desired.get(
            rel,
            p.read_text(
                encoding="utf-8",
                errors="replace"
            )
        )

        desired[rel] = update_source_status(
            base,
            s["disposition"],
            s["reason"],
            s.get("knowledge_notes", [])
        )

    # remove no-ops
    result = {}

    for rel, text in desired.items():
        p = canon(rel)

        if p.exists():
            old = p.read_text(
                encoding="utf-8",
                errors="replace"
            )
            if old == text:
                continue

        result[rel] = text

    return manifest, patches, result


def write_transaction(draft, desired):
    tx_id = (
        f"{stamp()}-cluster-"
        f"{draft.name[:70]}"
    )

    tx = TRANSACTIONS / tx_id
    tx.mkdir(parents=True, exist_ok=False)

    staged = tx / "staged"
    backups = tx / "backups"

    records = []

    for rel, text in desired.items():
        sp = staged / rel
        sp.parent.mkdir(
            parents=True, exist_ok=True
        )
        sp.write_text(
            text, encoding="utf-8"
        )

        dest = canon(rel)

        if dest.exists():
            bp = backups / rel
            bp.parent.mkdir(
                parents=True, exist_ok=True
            )
            shutil.copy2(dest, bp)

            records.append({
                "path": rel,
                "existed_before": True,
                "before_sha256": sha(dest),
                "backup": str(
                    bp.relative_to(tx)
                ),
            })
        else:
            records.append({
                "path": rel,
                "existed_before": False,
                "before_sha256": None,
                "backup": None,
            })

    receipt = {
        "transaction_id": tx_id,
        "status": "PREPARED",
        "created_at": iso(),
        "draft_dir": str(draft),
        "files": records,
    }

    (tx / "receipt.json").write_text(
        json.dumps(
            receipt, ensure_ascii=False, indent=2
        ),
        encoding="utf-8"
    )

    applied = []
    records_by_path = {record["path"]: record for record in records}

    try:
        order = sorted(
            desired,
            key=lambda rel: (
                0 if rel.startswith("20_Areas/")
                else 1 if rel.startswith("30_Knowledge/")
                else 2,
                rel
            )
        )

        for rel in order:
            dest = canon(rel)
            record = records_by_path[rel]
            if record["existed_before"]:
                if not dest.exists() or sha(dest) != record["before_sha256"]:
                    raise RuntimeError(f"concurrent change detected before replace: {rel}")
            elif dest.exists():
                raise RuntimeError(f"concurrent create detected before replace: {rel}")
            dest.parent.mkdir(
                parents=True, exist_ok=True
            )

            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                dir=str(dest.parent),
                prefix=".cluster-librarian-",
                suffix=".tmp",
            ) as f:
                f.write(
                    (staged / rel).read_bytes()
                )
                tmp = Path(f.name)

            os.replace(tmp, dest)
            applied.append(rel)

        for rec in receipt["files"]:
            rec["after_sha256"] = sha(
                canon(rec["path"])
            )

        receipt["status"] = "APPLIED"
        receipt["applied_at"] = iso()
        receipt["applied_files"] = applied

        (tx / "receipt.json").write_text(
            json.dumps(
                receipt,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

    except Exception:
        for rec in reversed(records):
            if rec["path"] not in applied:
                continue

            dest = canon(rec["path"])

            if rec["existed_before"]:
                shutil.copy2(
                    tx / rec["backup"],
                    dest
                )
            elif dest.exists():
                dest.unlink()

        receipt["status"] = "ROLLED_BACK_DURING_APPLY"
        receipt["rolled_back_at"] = iso()

        (tx / "receipt.json").write_text(
            json.dumps(
                receipt,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )
        raise

    return tx_id, tx, applied


def run_locked(args):
    draft = Path(args.draft).expanduser().resolve()
    manifest, patches, desired = preflight(draft)

    print("CLUSTER_PREFLIGHT_VALID")
    print(json.dumps({
        "cluster_id": manifest["cluster_id"],
        "sources": len(manifest["source_notes"]),
        "canonical_write_count": len(desired),
        "creates": [rel for rel in desired if not canon(rel).exists()],
        "modifies": [rel for rel in desired if canon(rel).exists()],
    }, ensure_ascii=False, indent=2))

    if not args.apply:
        audit("dry_run_valid", draft=str(draft), cluster_id=manifest["cluster_id"], canonical_writes=len(desired))
        print("Dry-run only. Canonical writes: 0")
        return 0

    tx_id, tx, applied = write_transaction(draft, desired)
    manifest["status"] = "APPLIED"
    manifest["applied_at"] = iso()
    manifest["transaction_id"] = tx_id
    manifest["canonical_writes"] = len(applied)
    (draft / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    audit(
        "applied",
        draft=str(draft),
        cluster_id=manifest["cluster_id"],
        transaction_id=tx_id,
        files=applied,
    )
    print("APPLIED")
    print(f"Transaction: {tx_id}")
    print(f"Receipt: {tx / 'receipt.json'}")
    print(f"Canonical writes: {len(applied)}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", required=True)
    ap.add_argument(
        "--apply",
        action="store_true"
    )
    args = ap.parse_args()

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.touch(exist_ok=True)
    with LOCK_PATH.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return run_locked(args)
        except Exception as exc:
            audit("failed", draft=str(Path(args.draft).expanduser()), error=str(exc))
            raise


if __name__ == "__main__":
    raise SystemExit(main())
