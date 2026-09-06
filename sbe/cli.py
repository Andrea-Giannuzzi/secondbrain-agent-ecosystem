"""Small explicit commands for the existing local ecosystem."""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request
import webbrowser

from .installation import BUNDLE, InstallationError, deploy, root, uninstall


def config():
    p=root(Path.home())/"config.json"
    if p.is_symlink():
        raise ValueError("Symlinked SBE configuration")
    return json.loads(p.read_text())


def api(path="/api/status",post=False):
    req=urllib.request.Request("http://127.0.0.1:8765"+path,data=b"{}" if post else None,
        headers={"X-SecondBrain-Dashboard":"1","Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=15) as response:
        return json.load(response)


def module(name):
    cfg=config()
    for key,value in cfg["env"].items():
        if key.startswith("SECOND_BRAIN_") or key in {"CAO_BASE_URL","CAO_HEALTH_URL"}:
            os.environ[key]=value
    for path in ("Librarian/Runtime","Semantic/Runtime","Ruflo/Runtime","Ingestor/Runtime"):
        sys.path.insert(0,str(BUNDLE/path))
    return importlib.import_module(name)


def doctor():
    rows=[]
    for name,cmd in (("codex","codex"),("antigravity","agy"),("claude","claude"),("CAO","cao"),("Node.js","node")):
        binary=shutil.which(cmd)
        version=None
        if binary:
            try:
                r=subprocess.run([binary,"--version"],capture_output=True,text=True,timeout=10)
                match=re.search(r"(?<![0-9])\d+\.\d+\.\d+\b",r.stdout)
                version=match.group() if match else None
            except (OSError,subprocess.TimeoutExpired):
                pass
        rows.append({"name":name,"installed":bool(binary),"version":version,"authentication":"not_verified","quota":"not_verified"})
    # Read only known server names/status, never print native MCP list output.
    for provider,path,table in (("codex",Path.home()/".codex/config.toml","mcp_servers"),("antigravity",Path.home()/".gemini/config/mcp_config.json","mcpServers"),("claude",Path.home()/".claude.json","mcpServers")):
        try:
            if path.is_symlink():
                raise ValueError()
            if provider=="codex":
                import tomllib
                data=tomllib.loads(path.read_text())
            else:
                data=json.loads(path.read_text())
            servers=data[table]
            for name in ("secondbrain","ruflo-team"):
                row=servers.get(name)
                rows.append({"provider":provider,"name":name,"status":"configured" if isinstance(row,dict) and row.get("enabled",True) else "absent_or_disabled"})
        except (OSError,ValueError,KeyError,TypeError):
            rows.append({"provider":provider,"configuration":"unavailable_or_invalid"})
    try:
        status=api()
        rows.append({"name":"dashboard","responding":True,"librarian":status.get("overall","unknown")})
    except Exception:
        rows.append({"name":"dashboard","responding":False})
    return {"checks":rows,"privacy":"Only allowlisted names, versions and states are shown"}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="group",required=True)
    sub.add_parser("doctor")
    install=sub.add_parser("install",help="Internal deployment after bootstrap")
    install.add_argument("--vault",type=Path,required=True)
    install.add_argument("--adopt-existing",action="store_true")
    sub.add_parser("uninstall")
    dashboard=sub.add_parser("dashboard"); dashboard.add_argument("action",choices=["open","status"])
    librarian=sub.add_parser("librarian"); librarian.add_argument("action",choices=["run","status","pause","resume"])
    brain=sub.add_parser("brain"); brain.add_argument("action",choices=["index","search","evidence","read","related"]); brain.add_argument("value",nargs="?")
    team=sub.add_parser("team"); team.add_argument("action",choices=["list","status"]); team.add_argument("id",nargs="?")
    args=parser.parse_args()
    try:
        if args.group=="doctor":
            result=doctor()
        elif args.group=="install":
            if sys.platform!="darwin":
                raise ValueError("macOS required")
            executables={x:shutil.which(x) for x in ("codex","agy","claude","cao","cao-server","node","tmux")}
            providers=[p for p,c in (("codex","codex"),("antigravity","agy"),("claude","claude")) if executables[c]]
            if not providers or any(not executables[x] for x in ("cao","cao-server","node","tmux")):
                raise ValueError("Missing prerequisites")
            result=deploy(Path.home(),args.vault,Path(sys.executable),providers,{k:v for k,v in executables.items() if v},adopt=args.adopt_existing)
        elif args.group=="uninstall":
            result=uninstall(Path.home())
        elif args.group=="dashboard":
            if args.action=="open":
                webbrowser.open("http://legend.localhost:8765")
                result={"url":"http://legend.localhost:8765"}
            else:
                result=api()
        elif args.group=="librarian":
            if args.action=="status":
                data=api(); result={k:data.get(k) for k in ("overall","service","sources","circuit")}
            else:
                result=api({"run":"/api/run-now","pause":"/api/pause","resume":"/api/resume"}[args.action],True)
        elif args.group=="brain":
            if args.action=="index":
                cfg=config()
                child=subprocess.run([str(Path.home()/"Library/Application Support/SecondBrainSemantic/brain-index")])
                return child.returncode
            if not args.value:
                parser.error("brain command requires a query or note path")
            store=module("secondbrain_store")
            func={"search":store.search_second_brain,"evidence":store.search_second_brain_evidence,"read":store.read_second_brain_note,"related":store.related_second_brain_notes}[args.action]
            result=func(args.value)
        else:
            bridge=module("ruflo_team_bridge")
            if args.action=="status" and not args.id:
                parser.error("team status requires TEAM_ID")
            result=bridge.list_ruflo_teams() if args.action=="list" else bridge.get_ruflo_team_status(args.id)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except Exception as exc:
        # Do not expose native provider output, config values, or stack locals.
        message = str(exc) if isinstance(exc, InstallationError) else "Operation failed; configuration and runtime may need attention. Run sbe doctor. No provider output is included."
        print(json.dumps({"error":type(exc).__name__,"message":message}),file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
