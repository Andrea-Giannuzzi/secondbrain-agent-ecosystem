"""Allowlisted, local-only inventory. Never return commands or config values."""
from __future__ import annotations
import json
import re
import tomllib
from pathlib import Path

SAFE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.:/ -]{0,95}\Z")


def label(value):
    text = str(value or '')
    if not SAFE_NAME.fullmatch(text) or re.search(r'(?i)(token|secret|password|api.?key|bearer|sk-)', text):
        return '[nome omesso]'
    return text


def safe_file(path, root):
    if not path.is_relative_to(root) or any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(root)):
        raise ValueError('unsafe path')
    if path.stat().st_size > 2_000_000:
        raise ValueError('oversized config')
    return path


def agent_inventory(home=None):
    home = Path(home or Path.home()).absolute()
    rows, issues, seen = [], [], set()

    def add(provider, kind, name, origin, status, **extra):
        row = dict(provider=provider, kind=kind, name=label(name), origin=origin,
                   scope='global', status=status, **extra)
        key = (provider, kind, row['name'], origin)
        if key not in seen:
            seen.add(key)
            rows.append(row)

    def read(path):
        safe_file(path, home)
        text = path.read_text(encoding='utf-8')
        return tomllib.loads(text) if path.suffix == '.toml' else json.loads(text)

    def inspect_config(provider, rel):
        path = home / rel
        if not path.exists() and not path.is_symlink():
            return
        try:
            data = read(path)
            if not isinstance(data, dict):
                raise ValueError('object required')
            servers = data.get('mcp_servers', data.get('mcpServers', {}))
            if not isinstance(servers, dict):
                raise ValueError('servers object required')
            for name, cfg in servers.items():
                valid = isinstance(cfg, dict) and any(isinstance(cfg.get(k), str) and cfg[k] for k in ('command', 'url', 'serverUrl'))
                state = 'invalid' if not valid else 'disabled' if cfg.get('enabled') is False or cfg.get('disabled') is True else 'configured'
                add(provider, 'MCP', name, '~/' + rel, state)
        except (OSError, ValueError, TypeError):
            issues.append(dict(provider=provider, origin='~/' + rel, status='invalid'))

    def skills(provider, directory, origin, state='configured', include=None, exclude=None):
        if not directory.exists():
            return
        try:
            if directory.is_symlink() or not directory.is_relative_to(home):
                raise ValueError('unsafe root')
            for path in sorted(directory.glob('*/SKILL.md'))[:500]:
                try:
                    safe_file(path, home)
                    with path.open(encoding='utf-8') as stream:
                        if stream.readline().strip() != '---':
                            raise ValueError('frontmatter required')
                        name, ended = None, False
                        for _ in range(80):
                            line = stream.readline(2048)
                            if line.strip() == '---':
                                ended = True
                                break
                            if line.startswith('name:'):
                                name = line[5:].strip().strip('"\'')
                        if not name or not ended:
                            raise ValueError('invalid frontmatter')
                    # Native path filters affect discovery; show excluded skills as disabled.
                    selected = (not include or any(re.search(pattern, path.parent.name) for pattern in include)) and not any(re.search(pattern, path.parent.name) for pattern in (exclude or []))
                    add(provider, 'Skill', name, origin, state if selected else 'disabled')
                except (OSError, ValueError, UnicodeError):
                    add(provider, 'Skill', path.parent.name, origin, 'invalid')
        except (OSError, ValueError):
            issues.append(dict(provider=provider, origin=origin, status='invalid'))

    for provider, rel in [('codex', '.codex/config.toml'), ('antigravity', '.gemini/config/mcp_config.json')]:
        inspect_config(provider, rel)
    for rel in ('.codex/skills', '.agents/skills'):
        skills('codex', home / rel, '~/' + rel)
    skills('antigravity', home / '.gemini/config/skills', '~/.gemini/config/skills')
    config = home / '.gemini/config/skills.json'
    if config.exists() or config.is_symlink():
        try:
            data = read(config)
            # Only explicitly configured local roots; never follow remote or inherited configs silently.
            for entry in data.get('entries', []):
                raw = entry.get('path', '')
                path = home / raw[2:] if raw.startswith('~/') else Path(raw)
                if not path.is_absolute() or not path.is_relative_to(home) or '..' in path.parts:
                    raise ValueError('unsafe skill root')
                skills('antigravity', path, '~/.gemini/config/skills.json', include=entry.get('include_only'), exclude=entry.get('exclude'))
            if data.get('inherits'):
                issues.append(dict(provider='antigravity', origin='~/.gemini/config/skills.json', status='inherited_not_scanned'))
        except (OSError, ValueError, TypeError, AttributeError):
            issues.append(dict(provider='antigravity', origin='~/.gemini/config/skills.json', status='invalid'))
    for provider, rel in [('codex', '.codex/hooks.json'), ('codex', '.agents/hooks.json'), ('antigravity', '.gemini/config/hooks.json')]:
        path = home / rel
        if not path.exists() and not path.is_symlink():
            continue
        try:
            data = read(path)
            if not isinstance(data, dict):
                raise ValueError('object required')
            for name, cfg in data.items():
                valid = isinstance(cfg, dict)
                events = sorted(set(cfg) & {'PreToolUse', 'PostToolUse', 'PreInvocation', 'PostInvocation', 'Stop'}) if valid else []
                add(provider, 'Hook', name, '~/' + rel, 'invalid' if not valid else 'disabled' if cfg.get('enabled') is False else 'configured', events=events)
        except (OSError, ValueError, TypeError):
            issues.append(dict(provider=provider, origin='~/' + rel, status='invalid'))
    # Plugin configuration is also global. Cache presence alone does not prove enablement.
    for provider, rel, config_rel in [('antigravity', '.gemini/config/plugins', '.gemini/config/config.json'), ('codex', '.codex/plugins/cache', '.codex/config.toml')]:
        base = home / rel
        try:
            enabled = read(home / config_rel).get('plugins', {}) if (home / config_rel).exists() else {}
            manifests = list(base.glob('*/plugin.json')) if provider == 'antigravity' else list(base.glob('*/*/*/.codex-plugin/plugin.json'))
            for manifest in manifests[:200]:
                safe_file(manifest, home)
                root = manifest.parent if provider == 'antigravity' else manifest.parent.parent
                name = root.name if provider == 'antigravity' else root.parent.name
                key = name if provider == 'antigravity' else name + '@' + root.parent.parent.name
                cfg = enabled.get(key)
                state = 'unknown' if not isinstance(cfg, dict) else 'disabled' if cfg.get('enabled') is False else 'configured'
                origin = 'plugin: ' + label(key)
                skills(provider, root / 'skills', origin, state=state)
                for filename in ('mcp_config.json', '.mcp.json'):
                    path = root / filename
                    if not path.exists():
                        continue
                    data = read(path)
                    for mcp_name, mcp_cfg in data.get('mcpServers', {}).items():
                        add(provider, 'MCP', mcp_name, origin, state if isinstance(mcp_cfg, dict) else 'invalid')
                path = root / 'hooks/hooks.json'
                if path.exists():
                    data = read(path)
                    hooks = data.get('hooks', data)
                    for event, handlers in hooks.items():
                        add(provider, 'Hook', event, origin, state if isinstance(handlers, (list, dict)) else 'invalid', events=[label(event)])
        except (OSError, ValueError, TypeError, AttributeError):
            issues.append(dict(provider=provider, origin='~/' + rel, status='invalid'))
    return {'items': rows, 'issues': issues, 'scope': 'global',
            'note': 'Configurazioni locali rilevate, non prova di caricamento nella sessione. La cache dei plugin può includere versioni non caricate; verificare nel client le personalizzazioni di progetto e gli elementi non verificati.'}
