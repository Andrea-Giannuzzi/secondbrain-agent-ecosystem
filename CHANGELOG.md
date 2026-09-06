# Changelog

## 0.1.1 — 2026-09-06

- Claude Code joins Codex and Antigravity as a full provider: CAO worker and
  Librarian profiles, client skills, policy and MCP registration through its own
  CLI. Role assignment follows the session provider — Antigravity investigates
  when it does not coordinate, and the reasoning provider that did not write the
  code reviews it — with fallback to the next candidate and an explicitly degraded
  same-author review. The Librarian rotates the head of its provider chain and
  starts its correction pass from a provider that did not produce the semantics.
- Setup pre-accepts Claude Code's workspace trust dialog for the two snapshot
  roots, which CAO cannot answer for itself, and uninstall restores the previous
  state. Worker end markers are read from a captured terminal pane, which
  re-wraps them and appends a teardown epilogue, while a captured echo of the
  prompt's own instruction is still refused.
- Claude workers deliver their report through a bundled single-tool MCP server
  instead of the terminal pane, so a capture that CAO ends mid-answer no longer
  loses a finished review. The tool is authorized per run inside the staged
  snapshot and writes only to a path derived from that run. An incomplete
  capture now retries the same worker once before the role degrades, and a
  quota failure still moves on immediately.
- Codex workers no longer stall for the whole step timeout on a workspace trust
  prompt they cannot answer: the staging directory is declared trusted for the
  duration of the step and the declaration is withdrawn afterwards, in both the
  team bridge and the unattended Librarian.

## 0.1.0 — 2026-09-06


- Initial macOS distribution candidate of local document ingestion, Librarian,
  semantic search, bounded Ruflo/CAO teams and dashboard.
- Positive-allowlist export from private canonical sources.
- Guided bootstrap, safe diagnostics, transactional deployment and conservative
  uninstall, preserving indexes, queues and private audit.
- Technical Drop files are ignored without deletion; relation titles are redacted.
- Canonical and evidence searches recheck current file access; indexing rejects
  external symlinks and extracted-text paths outside the designated directory.
- Python and Node dependencies are locked; command conflicts and interrupted
  uninstall/export preserve previous files. ONNX runtime telemetry is disabled.
- Ruflo initialization and memory operations share one configured database
  directory; existing databases are not reinitialized or moved.
