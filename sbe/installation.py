"""Transactional macOS deployment; user data and AI credentials are never copied."""
from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.request

BUNDLE = Path(__file__).parent / "bundle" / "_System"
LABELS = ("cao", "dashboard", "librarian", "ingestor.recursive")
FOLDERS = ("00_Drop", "10_Projects", "20_Areas", "30_Knowledge", "40_Research", "50_Sources", "60_Attachments")


class InstallationError(ValueError):
    """A fixed, credential-free message safe to show to the operator."""


def managed_path(home, raw):
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise InstallationError("Invalid managed file record")
    path.relative_to(home)
    no_link_parents(path)
    return path


def root(home):
    return home / "Library/Application Support/SecondBrainEcosystem"


def no_link_parents(path):
    if any(p.is_symlink() for p in path.parents):
        raise ValueError("Refusing a path below a symlink")


def image(path):
    no_link_parents(path)
    if path.is_symlink():
        return {"link": os.readlink(path)}
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError("Managed target is not a regular file")
    return {"data": base64.b64encode(path.read_bytes()).decode(), "mode": path.stat().st_mode & 0o777}


def restore(path, value):
    no_link_parents(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if value is None:
        path.unlink(missing_ok=True)
        return
    if "link" in value:
        path.unlink(missing_ok=True)
        path.symlink_to(value["link"])
        return
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(base64.b64decode(value["data"]))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, value["mode"])
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class Transaction:
    def __init__(self, home, adopt=False):
        self.home = home
        self.adopt = adopt
        self.base = root(home)
        self.ledger = self.base / "installed.json"
        no_link_parents(self.ledger)
        if self.ledger.is_symlink():
            raise InstallationError("Symlinked installation ledger is not supported")
        self.old = json.loads(self.ledger.read_text()) if self.ledger.exists() else {"files": {}}
        for raw in self.old["files"]:
            managed_path(home, raw)
        self.files = dict(self.old["files"])
        self.previous_jobs = self.old.get("previous_jobs", [])
        self.claude_mcp = dict(self.old.get("claude_mcp", {}))
        self.claude_bin = self.old.get("claude_bin")
        self.claude_trust = dict(self.old.get("claude_trust", {}))
        self.before = {}
        self.backup = self.base / "Backups" / (time.strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:8])
        self.backup.mkdir(parents=True, mode=0o700)
        self.committed = False

    def write(self, path, data=None, mode=0o600, link=None, merge=False, protected=False):
        path = managed_path(self.home, path.absolute())
        desired = {"link": str(link)} if link is not None else {"data": base64.b64encode(data).decode(), "mode": mode}
        current = image(path)
        key = str(path)
        previous = self.files.get(key)
        if protected and not previous and current is not None and current != desired:
            raise InstallationError("An unmanaged terminal command already exists; rename it yourself before installing. It has not been replaced.")
        if path.is_symlink() and not (previous or (link is not None and current == desired)):
            raise ValueError("Unmanaged symlink conflict")
        if previous and current != previous["after"] and current != desired and not merge:
            raise ValueError("Managed file changed locally; preserve and resolve before updating")
        if not previous and current is not None and current != desired and not merge and not self.adopt:
            if current.get("data") == desired.get("data") and "data" in current:
                pass  # Content is identical; normalize managed file permissions.
            else:
                raise ValueError("Unmanaged file conflict; preserve and resolve before installing")
        if key not in self.before:
            self.before[key] = current
            restore(self.backup / "rollback.json", {"data": base64.b64encode(json.dumps(self.before).encode()).decode(), "mode": 0o600})
        restore(path, desired)
        self.files[key] = {"before": previous["before"] if previous else current, "after": desired}

    def rollback(self):
        for path, value in reversed(list(self.before.items())):
            restore(Path(path), value)

    def commit(self):
        restore(self.ledger, {"data": base64.b64encode(json.dumps({"files": self.files, "previous_jobs": self.previous_jobs, "claude_mcp": self.claude_mcp, "claude_bin": self.claude_bin, "claude_trust": self.claude_trust}, indent=2).encode()).decode(), "mode": 0o600})
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, kind, value, tb):
        if not self.committed:
            self.rollback()


@contextlib.contextmanager
def install_lock(home):
    base = root(home)
    no_link_parents(base / "install.lock")
    if (base/"install.lock").is_symlink():
        raise InstallationError("Symlinked installation lock is not supported")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (base / "install.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def safe_run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=kwargs.pop("timeout", 60), **kwargs)
    if result.returncode:
        # Native clients can print MCP keys in errors. Never forward output.
        raise RuntimeError(f"{Path(str(args[0])).name}: command failed (exit {result.returncode}); inspect locally")
    return result


def check_started_services(domain, labels, timeout=15):
    """A loaded LaunchAgent is not evidence that its process started."""
    deadline = time.monotonic()+timeout
    while True:
        try:
            for label in labels:
                output=safe_run(["launchctl","print",domain+"/"+label]).stdout
                if label.endswith(".librarian"):
                    exit_code=re.search(r"last exit code = (\d+)",output)
                    if exit_code and int(exit_code.group(1)):
                        raise RuntimeError("Librarian exited unsuccessfully")
                elif not re.search(r"state = running\b",output):
                    raise RuntimeError("Service process is not running")
            for url in ("http://127.0.0.1:9889/health","http://127.0.0.1:8765/api/status"):
                with urllib.request.urlopen(url,timeout=2) as response:
                    if response.status != 200:
                        raise RuntimeError("Local service health check failed")
            return
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            if time.monotonic() >= deadline:
                raise InstallationError("Local services did not become healthy; installation was rolled back. Check the private service logs.") from None
            time.sleep(0.25)


def wrapper(python, module, env):
    lines = ["#!/bin/sh", "set -eu", "umask 077"]
    lines += [f"export {key}={shlex.quote(str(value))}" for key, value in env.items()]
    lines.append(f"exec {shlex.quote(str(python))} {shlex.quote(str(module))} \"$@\"")
    return ("\n".join(lines)+"\n").encode()


def claude_mcp_command(home, name):
    """Read one user-scope Claude MCP registration without rewriting the file."""
    path = home/".claude.json"
    try:
        if path.is_symlink():
            raise ValueError("Symlinked Claude configuration is not supported")
        data = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        raise ValueError("Claude configuration is unreadable") from None
    server = (data.get("mcpServers") or {}).get(name) if isinstance(data, dict) else None
    if server is None:
        return None
    if not isinstance(server, dict) or server.get("url") or server.get("type") not in (None, "stdio"):
        raise ValueError("Existing MCP uses a different transport")
    return server.get("command")


def write_claude_config(config, data):
    """Replace ~/.claude.json atomically, preserving its private mode."""
    mode = os.stat(config).st_mode & 0o777 if config.exists() else 0o600
    fd, raw = tempfile.mkstemp(dir=str(config.parent), prefix=".claude.json.sbe.")
    temp = Path(raw)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, config)
    finally:
        temp.unlink(missing_ok=True)


def grant_claude_workspace_trust(home, roots):
    """Pre-accept Claude Code's workspace trust dialog for the CAO snapshot roots.

    CAO confirms that dialog with a bare Enter while "No, exit" is still the
    selected option, so a worker launched in a directory Claude has never seen
    exits at once and the run only fails later on the initialization timeout.
    Trust is recorded per directory and inherited by subdirectories, so trusting
    the two roots that hold generated snapshots covers every future task with no
    per-task write. It grants no tool permission: that stays with the profile's
    permissionMode and its disallowed tools. Returns each root's previous value
    so uninstall can put it back.
    """
    config = home/".claude.json"
    if config.is_symlink():
        raise ValueError("Symlinked Claude configuration is not supported")
    try:
        data = json.loads(config.read_text()) if config.exists() else {}
    except (OSError, ValueError):
        raise ValueError("Claude configuration is unreadable") from None
    if not isinstance(data, dict) or not isinstance(data.setdefault("projects", {}), dict):
        raise ValueError("Claude projects must be a JSON object")
    previous = {}
    for root in roots:
        key = str(Path(root).resolve())
        entry = data["projects"].setdefault(key, {})
        if not isinstance(entry, dict):
            raise ValueError("Claude project entry must be a JSON object")
        previous[key] = entry.get("hasTrustDialogAccepted")
        entry["hasTrustDialogAccepted"] = True
    if all(value is True for value in previous.values()):
        return {}
    write_claude_config(config, data)
    return previous


def restore_claude_trust(home, previous):
    """Put each root back to the trust value it had before installation."""
    if not previous:
        return
    config = home/".claude.json"
    try:
        data = json.loads(config.read_text()) if config.exists() else {}
        projects = data.get("projects") if isinstance(data, dict) else None
        if not isinstance(projects, dict):
            return
    except (OSError, ValueError):
        return
    for key, value in previous.items():
        entry = projects.get(key)
        if not isinstance(entry, dict):
            continue
        if value is None:
            entry.pop("hasTrustDialogAccepted", None)
            if not entry:
                projects.pop(key, None)
        else:
            entry["hasTrustDialogAccepted"] = value
    # A configuration that had no project map before must not keep an empty one.
    if not projects:
        data.pop("projects", None)
    write_claude_config(config, data)


def write_policy_block(tx, instructions, policy):
    if instructions.is_symlink():
        raise ValueError("Symlinked global instructions")
    old = instructions.read_text() if instructions.exists() else ""
    begin, end = "<!-- BEGIN SECONDBRAIN CONSULT POLICY -->", "<!-- END SECONDBRAIN CONSULT POLICY -->"
    if begin in old:
        start = old.index(begin)
        finish = old.find(end, start)
        if finish < 0:
            raise ValueError("Incomplete managed policy")
        updated = old[:start]+policy+old[finish+len(end):]
    else:
        updated = old.rstrip()+"\n\n"+policy+"\n"
    tx.write(instructions, updated.encode(), merge=True)


def configure_clients(tx, providers, commands, bundle, executables):
    import tomlkit
    home = tx.home
    claude_undo = []
    claude_trust_undo = {}
    if "codex" in providers:
        cfg = home / ".codex/config.toml"
        if cfg.is_symlink():
            raise ValueError("Symlinked Codex config is not supported")
        data = tomlkit.parse(cfg.read_text()) if cfg.exists() else tomlkit.document()
        servers = data.setdefault("mcp_servers", {})
        for name, command in commands.items():
            # Preserve unrelated per-server settings but require local managed transport.
            row = servers.setdefault(name, {})
            for key in ("url", "http_headers", "bearer_token_env_var"):
                if key in row:
                    raise ValueError("Existing MCP uses a different transport")
            row["command"] = str(command)
            row["args"] = []
            row["enabled"] = True
            row["default_tools_approval_mode"] = "approve"
        tx.write(cfg, tomlkit.dumps(data).encode(), merge=True)
        profile = 'model_provider = "openai"\nsandbox_mode = "read-only"\napproval_policy = "never"\n'
        # Disable inherited MCPs in CAO workers, including project-write tools.
        for name in servers:
            profile += "\n[mcp_servers."+json.dumps(name)+"]\nenabled = false\n"
        tx.write(home/".codex/sbe_readonly.config.toml", profile.encode())
        for skill in ("ruflo-team", "secondbrain-consult"):
            tx.write(home/".codex/skills"/skill/"SKILL.md", (bundle/"AgentAccess/Skills"/skill/"SKILL.md").read_bytes())
    if "antigravity" in providers:
        cfg = home / ".gemini/config/mcp_config.json"
        if cfg.is_symlink():
            raise ValueError("Symlinked Antigravity config is not supported")
        data = json.loads(cfg.read_text()) if cfg.exists() else {}
        if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
            raise ValueError("Invalid Antigravity MCP configuration")
        for name, command in commands.items():
            data.setdefault("mcpServers", {})[name] = {"command":str(command), "args":[]}
        tx.write(cfg, (json.dumps(data, indent=2)+"\n").encode(), merge=True)
        registry = home/".gemini/config/skills.json"
        if registry.is_symlink():
            raise ValueError("Symlinked skill registry")
        reg = json.loads(registry.read_text()) if registry.exists() else {}
        if not isinstance(reg, dict) or not isinstance(reg.get("entries", []), list):
            raise ValueError("Invalid skill registry")
        skill_root = home/".gemini/config/skills/secondbrain-ecosystem"
        target = {"path":str(skill_root)}
        if any(isinstance(x,dict) and x.get("path")==str(skill_root) and x!=target for x in reg.get("entries",[])):
            raise ValueError("Managed skills have custom filters")
        if target not in reg.setdefault("entries", []):
            reg["entries"].append(target)
        tx.write(registry, (json.dumps(reg,indent=2)+"\n").encode(), merge=True)
        for name in ("ruflo-team", "secondbrain-consult"):
            tx.write(skill_root/name/"SKILL.md", (bundle/"AgentAccess/Skills"/name/"SKILL.md").read_bytes())
        write_policy_block(tx, home/".gemini/GEMINI.md", (bundle/"AgentAccess/antigravity-secondbrain-policy.md").read_text().strip())
        tx.write(home/".local/bin/antigravity", link=executables["agy"], protected=True)
    if "claude" in providers:
        for name in ("ruflo-team", "secondbrain-consult"):
            tx.write(home/".claude/skills"/name/"SKILL.md", (bundle/"AgentAccess/Skills"/name/"SKILL.md").read_bytes())
        write_policy_block(tx, home/".claude/CLAUDE.md", (bundle/"AgentAccess/antigravity-secondbrain-policy.md").read_text().strip())
        # ~/.claude.json also carries the user's project history and is rewritten by
        # any running session, so registration is left to the Claude CLI. The
        # previous command is recorded in the ledger for uninstall.
        env = os.environ | {"HOME": str(home)}
        tx.claude_bin = str(executables["claude"])
        for name, command in commands.items():
            previous = claude_mcp_command(home, name)
            if previous == str(command):
                continue
            if previous is not None:
                safe_run([executables["claude"], "mcp", "remove", "-s", "user", name], env=env)
            safe_run([executables["claude"], "mcp", "add", "-s", "user", name, "--", str(command)], env=env)
            tx.claude_mcp.setdefault(name, previous)
            claude_undo.append((name, previous))
        support = home/"Library/Application Support"
        claude_trust_undo = grant_claude_workspace_trust(home, [
            support/"SecondBrainRuflo/TeamRuns", support/"SecondBrainLibrarian/ClusterRuns",
        ])
        for key, value in claude_trust_undo.items():
            tx.claude_trust.setdefault(key, value)
    # The trust map is split like the MCP one: the returned dict is what THIS run
    # granted and a rollback must undo, while the ledger keeps the value from
    # before the first install for uninstall to restore.
    return claude_undo, claude_trust_undo


def restore_claude_mcp(claude, entries, home):
    """Undo user-scope Claude MCP registrations recorded for this home."""
    env = os.environ | {"HOME": str(home)}
    for name, previous in reversed(list(entries)):
        if not claude:
            return
        subprocess.run([claude,"mcp","remove","-s","user",name],capture_output=True,env=env)
        if previous:
            subprocess.run([claude,"mcp","add","-s","user",name,"--",previous],capture_output=True,env=env)


def make_plists(home, vault, python, targets, executables, env):
    logs = vault/"_System/Logs"
    jobs = {
        "cao": [executables["cao-server"], "--agents-dir", str(home/".aws/cli-agent-orchestrator/agent-store"), "--host", "127.0.0.1", "--port", "9889"],
        "dashboard": [str(targets["librarian"]/"dashboard")],
        "librarian": [str(targets["librarian"]/"librarian-service")],
        "ingestor.recursive": [str(targets["automation"]/"ingestor")],
    }
    result = {}
    for name, args in jobs.items():
        label = "com.local.secondbrain."+name
        value = {"Label":label, "ProgramArguments":args, "WorkingDirectory":str(vault),
                 "EnvironmentVariables":{**env, "HOME":str(home)}, "RunAtLoad":True,
                 "ThrottleInterval":30, "StandardOutPath":str(logs/(name+".stdout.log")),
                 "StandardErrorPath":str(logs/(name+".stderr.log"))}
        if name=="librarian":
            value.update(StartInterval=60, WatchPaths=[str(vault/"_System/Automation/librarian-queue.jsonl")])
        else:
            value["KeepAlive"] = {"SuccessfulExit":False}
        result[label] = plistlib.dumps(value)
    return result


def deploy(home, vault, python, providers, executables, bundle=BUNDLE, services=True, fail_after=None, adopt=False):
    home, vault, python = home.absolute(), vault.absolute(), python.absolute()
    no_link_parents(vault / "_System")
    vault.relative_to(home)  # Keep every managed deployment within the selected home.
    support = home/"Library/Application Support"
    targets = {"librarian":support/"SecondBrainLibrarian", "semantic":support/"SecondBrainSemantic",
               "ruflo":support/"SecondBrainRuflo", "automation":support/"SecondBrainAutomation"}
    release = python.parents[2]
    env = {"SECOND_BRAIN_VAULT":str(vault), "SECOND_BRAIN_SEMANTIC_STATE":str(targets["semantic"]),
           "SECOND_BRAIN_RUFLO_ROOT":str(targets["ruflo"]), "SECOND_BRAIN_RUFLO_BIN":str(release/"node_modules/.bin/ruflo"),
           "PATH":os.pathsep.join(dict.fromkeys([str(python.parent),*[str(Path(x).parent) for x in executables.values()],str(home/".local/bin"),"/opt/homebrew/bin","/usr/local/bin","/usr/bin","/bin","/usr/sbin","/sbin"])),
           "CAO_BASE_URL":"http://127.0.0.1:9889", "CAO_HEALTH_URL":"http://127.0.0.1:9889/health"}
    plists = make_plists(home,vault,python,targets,executables,env)
    domain = f"gui/{os.getuid()}"
    started = []
    previous_jobs = []
    claude_undo = []
    claude_trust_undo = {}
    with install_lock(home):
        with Transaction(home, adopt=adopt) as tx:
            try:
                if services:
                    for label in plists:
                        p = home/"Library/LaunchAgents"/(label+".plist")
                        active = subprocess.run(["launchctl","print",domain+"/"+label],capture_output=True).returncode==0
                        if active:
                            if not p.exists():
                                raise RuntimeError("Running service has no restorable plist")
                            previous_jobs.append(p)
                            safe_run(["launchctl","bootout",domain+"/"+label])
                if not tx.ledger.exists():
                    tx.previous_jobs = [str(p) for p in previous_jobs]
                for name in FOLDERS:
                    (vault/name).mkdir(parents=True, exist_ok=True)
                for name in ("Logs","Automation","Extracted","Librarian"):
                    (vault/"_System"/name).mkdir(parents=True,exist_ok=True)
                groups = (("Librarian/Runtime","librarian"),("Semantic/Runtime","semantic"),("Ruflo/Runtime","ruflo"),("Ingestor/Runtime","automation"))
                for source, target in groups:
                    dest = targets[target]/"team-runtime" if target=="ruflo" else targets[target]
                    for file in (bundle/source).glob("*.py"):
                        if not file.name.startswith("test_"):
                            tx.write(dest/file.name,file.read_bytes(),merge=False)
                for target in (targets["semantic"],targets["ruflo"]/"team-runtime"):
                    tx.write(target/"secondbrain_redaction.py",(bundle/"Librarian/Runtime/secondbrain_redaction.py").read_bytes())
                for file in (bundle/"Librarian/Dashboard").iterdir():
                    if file.suffix in {".html",".css",".js"}:
                        tx.write(vault/"_System/Librarian/Dashboard"/file.name,file.read_bytes())
                for file in (bundle/"Librarian").glob("*.schema.json"):
                    tx.write(vault/"_System/Librarian"/file.name,file.read_bytes())
                # Ingestor imports OCR utilities from the deployed runtime.
                env["PYTHONPATH"] = str(targets["librarian"])
                wrappers = {
                    targets["semantic"]/"brain-index":targets["semantic"]/"brain_index.py",
                    targets["semantic"]/"brain-search":targets["semantic"]/"brain_search.py",
                    targets["semantic"]/"secondbrain-mcp":targets["semantic"]/"secondbrain_mcp.py",
                    targets["ruflo"]/"team-runtime/ruflo-team-mcp":targets["ruflo"]/"team-runtime/ruflo_team_mcp.py",
                    targets["ruflo"]/"team-runtime/worker-report-mcp":targets["ruflo"]/"team-runtime/worker_report_mcp.py",
                    targets["librarian"]/"brain-cluster-organize":targets["librarian"]/"brain_cluster_organizer.py",
                    targets["librarian"]/"brain-cluster-execute":targets["librarian"]/"brain_cluster_executor.py",
                    targets["librarian"]/"librarian-service":targets["librarian"]/"secondbrain_librarian_service.py",
                    targets["librarian"]/"dashboard":targets["librarian"]/"librarian_dashboard.py",
                    targets["automation"]/"ingestor":targets["automation"]/"secondbrain_ingestor_recursive.py",
                }
                for path,module in wrappers.items():
                    tx.write(path,wrapper(python,module,env),mode=0o700)
                commands = {"secondbrain":targets["semantic"]/"secondbrain-mcp","ruflo-team":targets["ruflo"]/"team-runtime/ruflo-team-mcp"}
                claude_undo,claude_trust_undo=configure_clients(tx,providers,commands,bundle,executables)
                for source in (bundle/"Ruflo/CAOProfiles",bundle/"Librarian/CAOProfiles"):
                    for profile in source.glob("*.md"):
                        if profile.name=="README.md" or not any(p in profile.name for p in providers):
                            continue
                        content=profile.read_text().replace("codexProfile: cao_librarian_readonly","codexProfile: sbe_readonly")
                        content=content.replace('"{{RUFLO_BIN}}"',json.dumps(env["SECOND_BRAIN_RUFLO_BIN"]))
                        content=content.replace('"{{RUFLO_DB}}"',json.dumps(str(targets["ruflo"]/"data/memory.db")))
                        content=content.replace('"{{RUFLO_MEMORY_ROOT}}"',json.dumps(str(targets["ruflo"]/"data")))
                        content=content.replace('"{{WORKER_REPORT_MCP}}"',json.dumps(str(targets["ruflo"]/"team-runtime/worker-report-mcp")))
                        for folder in ("agent-store","agent-context"):
                            tx.write(home/".aws/cli-agent-orchestrator"/folder/profile.name,content.encode())
                # CAO reads skill packages from its registered global skill area.
                for file in (bundle/"Librarian/Skills").rglob("*"):
                    if file.is_file():
                        tx.write(home/".aws/cli-agent-orchestrator/skills"/file.relative_to(bundle/"Librarian/Skills"),file.read_bytes())
                for label, data in plists.items():
                    tx.write(home/"Library/LaunchAgents"/(label+".plist"),data)
                settings={"vault":str(vault),"python":str(python),"providers":providers,"version":"0.1.1","env":env}
                tx.write(root(home)/"config.json",(json.dumps(settings,indent=2)+"\n").encode())
                cli = ("#!/bin/sh\nexec "+shlex.quote(str(python))+" -m sbe.cli \"$@\"\n").encode()
                tx.write(home/".local/bin/sbe",cli,mode=0o700,protected=True)
                if fail_after:
                    raise RuntimeError("Simulated deployment failure")
                if services:
                    for label in plists:
                        safe_run(["launchctl","bootstrap",domain,str(home/"Library/LaunchAgents"/(label+".plist"))])
                        started.append(label)
                    # Catch immediate startup failures before committing configuration.
                    check_started_services(domain, started)
                tx.commit()
            except BaseException:
                for label in reversed(started):
                    subprocess.run(["launchctl","bootout",domain+"/"+label],capture_output=True)
                restore_claude_mcp(executables.get("claude"), claude_undo, home)
                restore_claude_trust(home, claude_trust_undo)
                tx.rollback()
                tx.committed=True  # Rollback already performed before old services restart.
                for p in previous_jobs:
                    subprocess.run(["launchctl","bootstrap",domain,str(p)],capture_output=True)
                raise
    return {"installed":True,"providers":providers,"index":"preserved; run sbe brain index to initialize a new vault"}


def uninstall(home, services=True, fail_after=None):
    """Restore initial managed files only if unchanged since installation."""
    with install_lock(home):
        ledger=root(home)/"installed.json"
        no_link_parents(ledger)
        if ledger.is_symlink():
            raise InstallationError("Symlinked installation ledger is not supported")
        if not ledger.exists():
            return {"uninstalled":True,"already_absent":True}
        data=json.loads(ledger.read_text())
        # Fail before touching services/configs if the user changed any managed file.
        for raw, row in data["files"].items():
            p=managed_path(home,raw)
            if image(p)!=row["after"]:
                raise ValueError("Managed file has user changes; uninstall stopped without changes")
        domain = f"gui/{os.getuid()}"
        stopped, restarted = [], []
        # Persist recovery material before touching any file or service.
        backup = root(home)/"Backups"/("uninstall-"+uuid.uuid4().hex)
        backup.mkdir(parents=True, mode=0o700)
        restore(backup/"installed.json", image(ledger))
        try:
            if services:
                for name in LABELS:
                    label = "com.local.secondbrain."+name
                    if subprocess.run(["launchctl","print",domain+"/"+label],capture_output=True).returncode==0:
                        safe_run(["launchctl","bootout",domain+"/"+label])
                        stopped.append(label)
            for index,(raw,row) in enumerate(reversed(list(data["files"].items()))):
                restore(Path(raw),row["before"])
                if fail_after is not None and index == fail_after:
                    raise RuntimeError("Simulated uninstall failure")
            if services:
                for raw in data.get("previous_jobs", []):
                    p=Path(raw)
                    p.relative_to(home/"Library/LaunchAgents")
                    if str(p) not in data["files"]:
                        raise InstallationError("Invalid original service record")
                    safe_run(["launchctl","bootstrap",domain,str(p)])
                    restarted.append(p.stem)
            restore_claude_mcp(data.get("claude_bin") or shutil.which("claude"), list(data.get("claude_mcp", {}).items()), home)
            restore_claude_trust(home, data.get("claude_trust", {}))
            ledger.unlink()
        except BaseException:
            for label in reversed(restarted):
                subprocess.run(["launchctl","bootout",domain+"/"+label],capture_output=True)
            for raw,row in data["files"].items():
                restore(Path(raw),row["after"])
            for label in stopped:
                safe_run(["launchctl","bootstrap",domain,str(home/"Library/LaunchAgents"/(label+".plist"))])
            raise
        return {"uninstalled":True,"data":"preserved","backups":"preserved"}
