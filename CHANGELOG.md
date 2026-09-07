# Changelog

## 0.1.2 — 2026-09-07

- A CAO step no longer decides for itself when a worker has finished. The step
  ends at the FIRST terminal read that looks complete, with none of the stability
  it demands of an idle pane, so a provider that prints anything before it starts
  working was captured mid-thought and torn down: measured at 5s against a
  ten-minute budget on a review that had failed three times in a row. Ruflo and
  the Librarian now run the step without teardown, wait for the worker's own
  out-of-band delivery, and reap the terminal themselves.
- The delivery is watched WHILE the step runs, not after it returns. CAO's
  completion detector fires early on one pane and never on another: a worker
  delivered a finished review at 38s, its pane was never recognised as done, and
  the answer sat unread on disk until the step timed out at 608s. The report is
  the completion signal, so it is watched from the moment the step starts.
- Every provider delivers through that channel, not only Claude, and the
  Librarian's workers deliver their JSON object the same way instead of having it
  read back off a repainting pane.
- Reviews come back as findings rather than prose: one entry per defect with
  file, line, claim, severity, and how the defect is REACHED in normal use. A
  reviewer that graded four findings as data loss, two of which needed a manually
  duplicated tab to trigger, left the author to work that out alone.
- A finished review is never discarded over a format rule. A verdict is read
  through its synonyms, and a review that found nothing to list is recorded as
  such: rejecting either lost a complete answer the worker did not retry.
- Codex is usable again, for three separate reasons that each blocked it alone.
  Its allowed-tools list reaches it verbatim, so it must name codex's real tools
  (`exec_command`); `fs_read`, and later the plausible-looking `shell`, made it
  answer that it could not read the snapshot at all. Its read-only worker profile
  must give every disabled MCP server a transport, or Codex refuses the config on
  the WRITE path it uses to persist a workspace-trust decision and parks on a
  dialog it cannot answer. And the delivery tool is pre-approved, since an MCP
  call that still asks for approval can never get one under `approval_policy =
  "never"`.
- A worker that goes silent is given up on after three minutes instead of holding
  the run for the whole budget. Refusing the pane as a completion signal is not
  refusing it as a heartbeat.
- Team status describes its tasks instead of reproducing them: a four-task team
  answered with 45k characters of reports the coordinator had already read.
- Only review runs through CAO. Independence is worth paying for AFTER the code
  exists, to check it; before it exists there is nothing to be independent of. An
  investigator reached through CAO reads a redacted snapshot with no history, no
  tooling and no memory of the conversation, and its answer arrives after the work
  has already been done waiting for it — nine minutes, on a question the
  coordinator's own subagent answers with the real repository in front of it.
  Investigation stays with the coordinator; the role remains as a task to track
  and is closed like coordinator and executor work.
- Ruflo memory records every finished review, not only the verified successes.
  Verified outcomes still require completed work plus an independent PASS and are
  keyed and tagged apart, so a blocked review can never be read as a pass — but
  the knowledge in a review that found something is no longer thrown away.

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
