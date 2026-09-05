#!/usr/bin/env python3
from __future__ import annotations
import os, json, time, shutil, subprocess, hashlib, re, html, sys, zipfile, xml.etree.ElementTree as ET
from pathlib import Path
import datetime as dt

VAULT=Path(os.environ.get("SECOND_BRAIN_VAULT","~/Documents/SecondBrain")).expanduser().resolve()
DROP=VAULT/"00_Drop"; SOURCES=VAULT/"50_Sources"; ATTACH=VAULT/"60_Attachments"; SYSTEM=VAULT/"_System"
AUTO=SYSTEM/"Automation"; LOGS=SYSTEM/"Logs"; FAILED=SYSTEM/"Failed"; EXTRACTED=SYSTEM/"Extracted"
INDEX=AUTO/"ingest-index.json"; QUEUE=AUTO/"librarian-queue.jsonl"
LIBRARIAN_RUNTIME=SYSTEM/"Librarian"/"Runtime"
sys.path.insert(0,str(LIBRARIAN_RUNTIME))
try:
    from recover_scanned_sources import ocr_pdf, pdf_pages, sample_pages, words
except ImportError:
    ocr_pdf=pdf_pages=sample_pages=words=None
TEXT_EXTS={".md",".txt",".tex",".csv",".json",".yaml",".yml",".html",".htm"}
SUPPORTED=TEXT_EXTS|{".pdf",".docx"}
IGNORED_DIRS={".venv","venv",".git","node_modules","__pycache__",".pytest_cache",".mypy_cache",".ruff_cache",".tox",".nox","site-packages","dist","build"}
IGNORED_FILES={".DS_Store","Thumbs.db"}

def now(): return dt.datetime.now().astimezone()
def iso(): return now().isoformat(timespec="seconds")
def log(event,**data):
    LOGS.mkdir(parents=True,exist_ok=True)
    with (LOGS/"ingestor.jsonl").open("a",encoding="utf-8") as f:
        f.write(json.dumps({"time":iso(),"event":event,**data},ensure_ascii=False)+"\n")

def safe(s):
    s=re.sub(r'[/:\\?*"<>|]',"-",s.strip()); s=re.sub(r"\s+"," ",s).strip(" .-")
    return s[:140] or "Untitled"

def prune_technical():
    # Ignored input belongs to the user. Filtering must never delete it.
    return

def ignored(path):
    try: rel=path.relative_to(DROP)
    except: return True
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        return True
    return path.name in IGNORED_FILES or any(part in IGNORED_DIRS for part in rel.parts)

def stable(path):
    try:
        a=path.stat(); time.sleep(5); b=path.stat()
        return a.st_size==b.st_size and a.st_mtime_ns==b.st_mtime_ns
    except: return False

def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for c in iter(lambda:f.read(1024*1024),b""): h.update(c)
    return h.hexdigest()

def load_index():
    try:return json.loads(INDEX.read_text(encoding="utf-8"))
    except:return {"by_sha256":{}}

def save_index(x):
    INDEX.parent.mkdir(parents=True,exist_ok=True)
    INDEX.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding="utf-8")

def extract(path):
    ext=path.suffix.lower()
    if ext in TEXT_EXTS:
        raw=path.read_text(encoding="utf-8",errors="replace")
        if ext in {".html",".htm"}:
            raw=re.sub(r"(?s)<[^>]+>"," ",raw); raw=html.unescape(raw)
        return raw
    if ext==".pdf":
        tool=shutil.which("pdftotext")
        if not tool: raise RuntimeError("pdftotext not found")
        p=subprocess.run([tool,"-layout",str(path),"-"],capture_output=True,text=True,timeout=180)
        if p.returncode: raise RuntimeError(p.stderr[-1000:])
        text=p.stdout
        if words and (words(text)<70 or len(text)<450):
            total=pdf_pages(path)
            pages=sample_pages(total)
            recovered=ocr_pdf(path,pages,total)
            if words(recovered)>words(text):
                log("pdf_ocr_recovered",file=str(path.relative_to(DROP)),pages=pages,total_pages=total,words=words(recovered))
                return recovered
        return text
    if ext==".docx":
        with zipfile.ZipFile(path) as z: data=z.read("word/document.xml")
        root=ET.fromstring(data); out=[]
        for e in root.iter():
            if e.tag.endswith("}t") and e.text: out.append(e.text)
            elif e.tag.endswith("}p"): out.append("\n")
        return "".join(out)
    raise RuntimeError("unsupported")

def unique(folder,stem,suffix):
    folder.mkdir(parents=True,exist_ok=True); p=folder/f"{stem}{suffix}"; n=2
    while p.exists(): p=folder/f"{stem} ({n}){suffix}"; n+=1
    return p

def metadata(path):
    rel=path.relative_to(DROP); batch=rel.parts[0] if len(rel.parts)>1 else ""
    return batch, rel.as_posix()

def archive(src,batch,rel):
    m=now(); root=ATTACH/f"{m.year:04d}"/f"{m.month:02d}"
    if batch:
        dest=root.joinpath(*(safe(x) for x in Path(rel).parts)); dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists(): dest=unique(dest.parent,dest.stem,dest.suffix)
    else: dest=unique(root,safe(src.stem),src.suffix.lower())
    shutil.move(str(src),str(dest)); return dest

def create_source(name,att,exttxt,digest,batch,origrel):
    note=unique(SOURCES,safe(Path(name).stem),".md")
    relatt=att.relative_to(VAULT).as_posix(); relext=exttxt.relative_to(VAULT).as_posix()
    body=(
      "---\n"
      'type: "source"\nstatus: "unprocessed"\n'
      f'original_name: {json.dumps(name,ensure_ascii=False)}\n'
      f'import_batch: {json.dumps(batch,ensure_ascii=False)}\n'
      f'original_relative_path: {json.dumps(origrel,ensure_ascii=False)}\n'
      f'source_file: {json.dumps(relatt,ensure_ascii=False)}\n'
      f'extracted_text: {json.dumps(relext,ensure_ascii=False)}\n'
      f'sha256: "{digest}"\ningested_at: "{iso()}"\nneeds_librarian: true\n'
      "---\n\n"
      f"# {safe(Path(name).stem)}\n\n## Original file\n\n[[{relatt}]]\n"
    )
    note.write_text(body,encoding="utf-8"); return note

def process(path):
    if ignored(path): return
    batch,origrel=metadata(path)
    if path.suffix.lower() not in SUPPORTED:
        dest=FAILED/"Unsupported"/Path(origrel); dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.move(str(path),str(dest)); log("unsupported",file=origrel); return
    if not stable(path): return
    digest=sha(path); idx=load_index()
    if digest in idx["by_sha256"]:
        path.unlink(); log("duplicate",file=origrel); return
    text=extract(path); att=archive(path,batch,origrel)
    EXTRACTED.mkdir(parents=True,exist_ok=True); ex=EXTRACTED/f"{digest}.txt"; ex.write_text(text,encoding="utf-8")
    note=create_source(att.name,att,ex,digest,batch,origrel)
    idx["by_sha256"][digest]={"source_note":note.relative_to(VAULT).as_posix(),"attachment":att.relative_to(VAULT).as_posix(),"extracted_text":ex.relative_to(VAULT).as_posix(),"import_batch":batch,"original_relative_path":origrel}
    save_index(idx)
    with QUEUE.open("a",encoding="utf-8") as f:
        f.write(json.dumps({"time":iso(),"source_note":note.relative_to(VAULT).as_posix(),"sha256":digest,"import_batch":batch,"status":"pending"},ensure_ascii=False)+"\n")
    log("processed",original_relative_path=origrel,source_note=note.relative_to(VAULT).as_posix(),codex_used=False)

def clean_empty():
    for d in sorted([p for p in DROP.rglob("*") if p.is_dir()],key=lambda p:len(p.parts),reverse=True):
        try:
            if not any(d.iterdir()):
                rel=d.relative_to(DROP).as_posix(); d.rmdir(); log("removed_empty_drop_folder",folder=rel)
        except: pass

def main():
    for p in (DROP,SOURCES,ATTACH,AUTO,LOGS,FAILED,EXTRACTED): p.mkdir(parents=True,exist_ok=True)
    log("service_started",technical_hygiene=True,codex_used=False)
    while True:
        prune_technical()
        for p in sorted([x for x in DROP.rglob("*") if x.is_file() and not ignored(x)]):
            try: process(p)
            except Exception as e: log("error",file=str(p),error=str(e))
        prune_technical(); clean_empty(); time.sleep(5)

if __name__=="__main__": main()
