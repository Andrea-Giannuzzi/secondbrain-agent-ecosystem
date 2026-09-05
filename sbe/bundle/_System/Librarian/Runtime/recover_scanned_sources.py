#!/usr/bin/env python3
"""Recover useful local text from image-only PDF Sources with bounded OCR."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


VAULT = Path(os.environ.get("SECOND_BRAIN_VAULT", "~/Documents/SecondBrain")).expanduser().resolve()
SOURCES = VAULT / "50_Sources"
BACKUPS = VAULT / "_System" / "Librarian" / "Backups"
QUEUE = VAULT / "_System" / "Automation" / "librarian-queue.jsonl"


def iso():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


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
        raw = raw.strip()
        if raw.startswith('"') and raw.endswith('"'):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = raw[1:-1]
        values[key.strip()] = raw
    return values


def words(text: str):
    return len(re.findall(r"\b[\wÀ-ÿ'-]+\b", text))


def pdf_pages(path: Path):
    process = subprocess.run(["pdfinfo", str(path)], text=True, capture_output=True, timeout=30)
    if process.returncode:
        raise RuntimeError((process.stderr or "pdfinfo failed")[-500:])
    match = re.search(r"^Pages:\s+(\d+)", process.stdout, re.M)
    if not match:
        raise RuntimeError("pdfinfo returned no page count")
    return int(match.group(1))


def sample_pages(total: int, full_threshold=40, long_limit=16):
    if total <= 0:
        return []
    if total <= full_threshold:
        return list(range(1, total + 1))
    fixed = {1, 2, 3, 4, 5, 6, total - 1, total}
    slots = max(0, long_limit - len(fixed))
    if slots:
        fixed.update(round(1 + index * (total - 1) / (slots + 1)) for index in range(1, slots + 1))
    return sorted(page for page in fixed if 1 <= page <= total)[:long_limit]


def ocr_pdf(path: Path, pages: list[int], total: int):
    chunks = []
    with tempfile.TemporaryDirectory(prefix="secondbrain-ocr-") as temporary:
        root = Path(temporary)
        for page in pages:
            prefix = root / "page"
            render = subprocess.run(
                ["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile", "-gray", "-r", "150", "-png", str(path), str(prefix)],
                text=True, capture_output=True, timeout=180,
            )
            image = prefix.with_suffix(".png")
            if render.returncode or not image.is_file():
                continue
            result = subprocess.run(
                ["tesseract", str(image), "stdout", "-l", "eng"],
                text=True, capture_output=True, timeout=180,
            )
            content = result.stdout.strip() if result.returncode == 0 else ""
            if content:
                chunks.append(f"--- OCR page {page}/{total} ---\n{content}")
    return "\n\n".join(chunks).strip() + "\n" if chunks else ""


def update_frontmatter(text: str, values: dict):
    end = text.find("\n---\n", 4) if text.startswith("---\n") else -1
    if end < 0:
        raise ValueError("Source has no valid frontmatter")
    lines = text[4:end].splitlines()
    desired = {
        key: json.dumps(value, ensure_ascii=False)
        for key, value in values.items()
        if value is not None
    }
    removed = {key for key, value in values.items() if value is None}
    seen = set()
    output = []
    for line in lines:
        key = line.split(":", 1)[0].strip() if ":" in line else ""
        if key in removed:
            continue
        if key in desired:
            output.append(f"{key}: {desired[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in desired.items():
        if key not in seen:
            output.append(f"{key}: {value}")
    return "---\n" + "\n".join(output) + "\n---\n" + text[end + 5:]


def candidates():
    output = []
    for note in sorted(SOURCES.glob("*.md")):
        note_text = note.read_text(encoding="utf-8", errors="replace")
        metadata = frontmatter(note_text)
        if metadata.get("librarian_disposition") != "low_information":
            continue
        source = (VAULT / str(metadata.get("source_file", ""))).resolve()
        extracted = (VAULT / str(metadata.get("extracted_text", ""))).resolve()
        if VAULT not in source.parents or VAULT not in extracted.parents:
            continue
        if source.suffix.casefold() != ".pdf" or not source.is_file() or not extracted.is_file():
            continue
        old_text = extracted.read_text(encoding="utf-8", errors="replace")
        if words(old_text) >= 70 and len(old_text) >= 450:
            continue
        output.append((note, note_text, source, extracted, old_text))
    return output


def recover(item, full_threshold, long_limit):
    note, note_text, source, extracted, old_text = item
    total = pdf_pages(source)
    pages = sample_pages(total, full_threshold, long_limit)
    recovered = ocr_pdf(source, pages, total)
    return {
        "note": note,
        "note_text": note_text,
        "source": source,
        "extracted": extracted,
        "old_text": old_text,
        "text": recovered,
        "pages": pages,
        "total": total,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--full-threshold", type=int, default=40)
    parser.add_argument("--long-limit", type=int, default=16)
    args = parser.parse_args()
    items = candidates()
    print(f"Image-only PDF candidates: {len(items)}", flush=True)
    if args.preview:
        for note, _, source, _, old_text in items:
            print(f"- {note.name}: {source.name}, old words={words(old_text)}")
        return 0
    if not items:
        return 0

    backup = BACKUPS / (dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-ocr-recovery")
    improved = []
    failures = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
        futures = {pool.submit(recover, item, args.full_threshold, args.long_limit): item[0] for item in items}
        for future in as_completed(futures):
            note = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                failures.append({"source_note": note.relative_to(VAULT).as_posix(), "error": str(exc)})
                print(f"FAILED {note.name}: {exc}", flush=True)
                continue
            new_words = words(result["text"])
            old_words = words(result["old_text"])
            if new_words <= old_words:
                print(f"NO TEXT {note.name}: {new_words} words", flush=True)
                continue
            for path in (result["note"], result["extracted"]):
                target = backup / path.relative_to(VAULT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            result["extracted"].write_text(result["text"], encoding="utf-8")
            method = "tesseract-full" if len(result["pages"]) == result["total"] else "tesseract-sampled"
            updated_note = update_frontmatter(result["note_text"], {
                "extraction_method": method,
                "extraction_pages": ",".join(map(str, result["pages"])),
                "extraction_total_pages": result["total"],
                "extraction_updated_at": iso(),
                "status": "unprocessed",
                "needs_librarian": True,
                "librarian_disposition": None,
                "librarian_processed_at": None,
                "librarian_disposition_reason": None,
            })
            result["note"].write_text(updated_note, encoding="utf-8")
            metadata = frontmatter(updated_note)
            QUEUE.parent.mkdir(parents=True, exist_ok=True)
            with QUEUE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "time": iso(),
                    "source_note": result["note"].relative_to(VAULT).as_posix(),
                    "sha256": metadata.get("sha256", ""),
                    "import_batch": metadata.get("import_batch", ""),
                    "status": "pending",
                    "reason": "ocr_recovered",
                }, ensure_ascii=False) + "\n")
            improved.append({
                "source_note": result["note"].relative_to(VAULT).as_posix(),
                "old_words": old_words,
                "new_words": new_words,
                "pages": result["pages"],
                "total_pages": result["total"],
                "method": method,
            })
            print(f"RECOVERED {result['note'].name}: {old_words} -> {new_words} words", flush=True)

    backup.mkdir(parents=True, exist_ok=True)
    (backup / "manifest.json").write_text(json.dumps({
        "operation": "bounded local OCR recovery for image-only PDF Sources",
        "created_at": iso(),
        "improved": improved,
        "failures": failures,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Recovered: {len(improved)}; failed: {len(failures)}; backup: {backup.relative_to(VAULT)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
