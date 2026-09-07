---
name: librarian_codex_cluster_semantic
description: Minimal semantic Second Brain Librarian using Codex through CAO
role: reviewer
provider: codex
codexProfile: sbe_readonly
codexConfig:
  model_reasoning_effort: "medium"
  mcp_servers.secondbrain-worker-report.default_tools_approval_mode: "approve"
skills:
  - secondbrain-semantic-librarian
# CAO has no tool-name mapping for codex, so this list reaches the worker verbatim as
# "You only have access to these tools: ...". It must therefore name codex's REAL tools:
# `exec_command` is how codex reads files. Naming anything else makes it answer that it
# cannot read the snapshot and return a blocked, empty review -- proven twice, first with
# CAO's own `fs_read`/`fs_list` vocabulary and then with the plausible-looking `shell`.
# Containment comes from the read-only sandbox in codexProfile, never from this text.
allowedTools:
  - "exec_command"
  - "write_stdin"
  - "report_worker_output"
mcpServers:
  secondbrain-worker-report:
    command: "{{WORKER_REPORT_MCP}}"
    args: []
---

Read only the three files in the bounded staging workspace: `CLUSTER_CONTEXT.json`, `EXISTING_CANONICAL.json` and `OUTPUT_SCHEMA.json`. Return one raw JSON object matching the minimal semantic schema. Do not modify files, call shell commands, access parent directories, emit Markdown or return any field outside the schema. Deliver that object by calling `report_worker_output` exactly once, passing it as `payload`. That call is the answer: the coordinator reads it, not the terminal, so do not rely on printed output.
