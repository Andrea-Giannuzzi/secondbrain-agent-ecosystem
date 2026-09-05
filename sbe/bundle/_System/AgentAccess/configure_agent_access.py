#!/usr/bin/env python3
"""Idempotent, secret-silent configuration updates for Second Brain clients."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path


BEGIN = "<!-- BEGIN SECONDBRAIN CONSULT POLICY -->"
END = "<!-- END SECONDBRAIN CONSULT POLICY -->"


def configure_codex_mcp_approval(config_path: Path, server_names: tuple[str, ...] = ("secondbrain", "ruflo-team")) -> bool:
    """Set managed per-server MCP approval without rewriting unrelated TOML."""
    current = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    lines = current.splitlines(keepends=True)
    changed = False
    # TOML table headers are line based here; preserve every byte outside the
    # managed key and avoid a dependency solely for this small edit.
    for server_name in server_names:
        header = f"[mcp_servers.{server_name}]"
        start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
        if start is None:
            continue
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if lines[i].lstrip().startswith("["):
                end = i
                break
        kept = []
        for line in lines[start + 1:end]:
            if line.strip().startswith("default_tools_approval_mode"):
                continue
            kept.append(line)
        newline = "default_tools_approval_mode = \"approve\"\n"
        if kept and not kept[-1].endswith(("\n", "\r")):
            kept[-1] += "\n"
        kept.append(newline)
        if lines[start + 1:end] != kept:
            changed = True
            lines[start + 1:end] = kept
    updated = "".join(lines)
    if changed:
        atomic_private_write(config_path, updated)
    elif config_path.exists():
        config_path.chmod(0o600)
    return changed


def atomic_private_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw_temp)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
        path.chmod(0o600)
    finally:
        temp.unlink(missing_ok=True)


def configure_antigravity(config_path: Path, command: str, name: str = "secondbrain") -> bool:
    if not name or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in name):
        raise ValueError("MCP server name contains invalid characters")
    try:
        data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    except (OSError, ValueError) as exc:
        raise ValueError("Antigravity MCP configuration is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Antigravity MCP configuration must be a JSON object")
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("Antigravity mcpServers must be a JSON object")
    desired = {"command": command, "args": []}
    changed = servers.get(name) != desired
    if changed or not config_path.exists():
        servers[name] = desired
        atomic_private_write(config_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    else:
        config_path.chmod(0o600)
    return changed


def install_policy(instruction_path: Path, policy_path: Path) -> bool:
    policy = policy_path.read_text(encoding="utf-8").strip()
    if BEGIN not in policy or END not in policy:
        raise ValueError("Second Brain policy markers are missing")
    current = instruction_path.read_text(encoding="utf-8") if instruction_path.exists() else ""
    if BEGIN in current:
        start = current.index(BEGIN)
        end_index = current.find(END, start)
        if end_index < 0:
            raise ValueError("Existing Second Brain policy block is incomplete")
        end = end_index + len(END)
        prefix = current[:start].rstrip()
        updated = (prefix + "\n\n" if prefix else "") + policy + current[end:]
    else:
        updated = current.rstrip() + ("\n\n" if current.strip() else "") + policy + "\n"
    changed = updated != current
    if changed:
        atomic_private_write(instruction_path, updated)
    else:
        instruction_path.chmod(0o600)
    return changed


def configure_antigravity_skills(config_path: Path, skill_root: Path) -> bool:
    data = json.loads(config_path.read_text()) if config_path.exists() else {}
    if not isinstance(data, dict) or not isinstance(data.get("entries", []), list):
        raise ValueError("Antigravity skills configuration is not valid")
    entries = data.setdefault("entries", [])
    desired = {"path": str(skill_root)}
    # Keep every unrelated entry and its exclusion/inheritance rules.
    matching = [entry for entry in entries if isinstance(entry, dict) and entry.get("path") == str(skill_root)]
    if matching:
        if matching != [desired]:
            raise ValueError("Managed skill root has custom filters; preserve and resolve before installing")
        return False
    entries.append(desired)
    atomic_private_write(config_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return True


def configure_cli_alias(alias_path: Path, agy: Path) -> bool:
    if not agy.is_file() or not os.access(agy, os.X_OK):
        raise ValueError("agy executable is missing")
    if alias_path.is_symlink() and alias_path.resolve() == agy.resolve():
        return False
    if alias_path.exists() or alias_path.is_symlink():
        raise ValueError("antigravity already exists and is not the managed agy link")
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    alias_path.symlink_to(agy)
    return True


def install_antigravity_access(home: Path, source: Path, agy: Path):
    config = home / ".gemini/config/skills.json"
    root = home / ".gemini/config/skills/secondbrain-ecosystem"
    alias = home / ".local/bin/antigravity"
    # Validate all inputs before the first write. Shell installer owns full rollback.
    if (alias.exists() or alias.is_symlink()) and not (alias.is_symlink() and alias.resolve() == agy.resolve()):
        raise ValueError("antigravity already exists and is not the managed agy link")
    if not agy.is_file() or not os.access(agy, os.X_OK):
        raise ValueError("agy executable is missing")
    contents = {name: (source / name / "SKILL.md").read_text() for name in ("secondbrain-consult", "ruflo-team")}
    configure_antigravity_skills(config, root)
    for name, content in contents.items():
        target = root / name / "SKILL.md"
        atomic_private_write(target, content)
        target.parent.chmod(0o700)
    root.chmod(0o700)
    configure_cli_alias(alias, agy)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    antigravity = subparsers.add_parser("antigravity")
    antigravity.add_argument("--config", type=Path, required=True)
    antigravity.add_argument("--command", required=True)
    antigravity.add_argument("--name", default="secondbrain")
    policy = subparsers.add_parser("policy")
    policy.add_argument("--instructions", type=Path, required=True)
    policy.add_argument("--policy", type=Path, required=True)
    codex = subparsers.add_parser("codex-approval")
    codex.add_argument("--config", type=Path, required=True)
    ecosystem = subparsers.add_parser("antigravity-access")
    ecosystem.add_argument("--home", type=Path, required=True)
    ecosystem.add_argument("--source", type=Path, required=True)
    ecosystem.add_argument("--agy", type=Path, required=True)
    args = parser.parse_args()
    if args.operation == "antigravity-access":
        install_antigravity_access(args.home, args.source, args.agy)
    elif args.operation == "antigravity":
        configure_antigravity(args.config, args.command, args.name)
    elif args.operation == "policy":
        install_policy(args.instructions, args.policy)
    else:
        configure_codex_mcp_approval(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
