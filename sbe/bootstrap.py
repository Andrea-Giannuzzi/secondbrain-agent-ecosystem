"""Build private release dependencies before touching global configuration."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault",type=Path)
    parser.add_argument("--yes",action="store_true",help="Confirm providers are logged in and permit local service installation")
    parser.add_argument("--adopt-existing",action="store_true",help="Back up and replace an existing SecondBrain installation's runtime and profiles")
    args=parser.parse_args()
    if platform.system()!="Darwin":
        parser.error("v0.1 supports macOS only")
    if sys.version_info<(3,12):
        parser.error("Install Python 3.12 or later first")
    required=("node","npm","cao","cao-server","tmux","pdftotext","pdfinfo","pdftoppm","tesseract")
    missing=[name for name in required if not shutil.which(name)]
    providers=[name for name,cmd in (("codex","codex"),("antigravity","agy")) if shutil.which(cmd)]
    if missing or not providers:
        parser.error("Missing prerequisites: "+", ".join(missing or ["Codex or agy"])+". See README installation instructions.")
    node=subprocess.run(["node","--version"],capture_output=True,text=True,check=True).stdout.strip()
    if int(node.lstrip("v").split(".")[0])<20:
        parser.error("Node.js 20 or later required")
    raw = args.vault or input("Vault path (new or existing): ").strip()
    if not raw:
        parser.error("Choose a dedicated vault directory")
    vault=Path(raw).expanduser().absolute()
    if vault==Path.home() or vault==Path("/") or not str(vault):
        parser.error("Choose a dedicated vault directory")
    vault.relative_to(Path.home())
    print("Providers detected: "+", ".join(providers)+". Authentication and quota cannot be inferred from installation.")
    if not args.yes and input("Are providers logged in, and may setup register local services and global skills/MCP? [y/N] ").lower()!="y":
        return
    release=Path.home()/"Library/Application Support/SecondBrainEcosystem/releases"/("0.1.0-"+time.strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:6])
    release.mkdir(parents=True,mode=0o700)
    project=Path(__file__).resolve().parents[1]
    # Build tools may create metadata beside pyproject.toml. Keep the reviewed
    # export untouched so the next ownership-checked export remains repeatable.
    build_source=release/"source"
    shutil.copytree(project,build_source,ignore=shutil.ignore_patterns(".git",".export-receipt.json","__pycache__","*.egg-info","build","dist","node_modules"))
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(project/name,release/name)
    stages=[([sys.executable,"-m","venv",str(release/"venv")],"Python environment"),
            ([str(release/"venv/bin/python"),"-m","pip","install","--disable-pip-version-check","--require-hashes","-r",str(project/"requirements.lock")],"Locked Python dependencies"),
            ([str(release/"venv/bin/python"),"-m","pip","install","--disable-pip-version-check","--no-deps",str(build_source)],"Python package"),
            (["npm","--prefix",str(release),"ci","--no-audit","--no-fund"],"Locked Ruflo dependencies")]
    for command,label in stages:
        print(label+"…",flush=True)
        result=subprocess.run(command,capture_output=True,text=True)
        if result.returncode:
            print(label+" failed. Global configuration was not changed. Check dependencies and network locally.",file=sys.stderr)
            return 1
    # Run deployment with the environment that contains the declared dependencies.
    command=[str(release/"venv/bin/python"),"-m","sbe.cli","install","--vault",str(vault)]
    if args.adopt_existing:
        command.append("--adopt-existing")
    result=subprocess.run(command)
    if result.returncode:
        return result.returncode
    print("Installed. Open a new terminal, reload VS Code, then run sbe doctor.")
    print("For a new vault run sbe brain index. Existing indexes were preserved.")
    print("If sbe is not found, add ~/.local/bin to your shell PATH.")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
