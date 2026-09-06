# Architecture

## Ownership

The private vault stores original documents, canonical Markdown, ingestion metadata and transaction audit. Qdrant and Ruflo memory are derived state. The public code checkout contains only runtime sources and synthetic examples. Installer backups of global configuration remain private under `~/Library/Application Support/SecondBrainEcosystem/Backups`.

The current Claude, Codex or Antigravity session is coordinator and sole writer of its project and declares itself as `writer_provider`. Ruflo records coordinator, investigator, executor/writer and reviewer roles. The two providers that are not writing split investigation and review: Antigravity is the preferred investigator whenever it does not coordinate, and the reasoning provider that did not write the code reviews it, so a Claude session gets a Codex reviewer, a Codex session a Claude reviewer, and an Antigravity session a Claude reviewer with Codex investigating. Those heads are preferences; an unavailable provider falls through to the next candidate and review degrades to the author only when no independent provider remains. Only investigator/reviewer tasks run through CAO on bounded redacted snapshots. Profiles express worker behavior, not four concurrent writer processes. Seven Librarian profiles and three Ruflo worker profiles are available; only those for installed providers are registered.

Read-only CAO policy, client sandboxing and snapshot isolation are separate controls. A prompt alone is not an operating-system sandbox. The Codex worker profile uses `read-only`, disables inherited global MCPs, and cannot approve new permissions. The Claude worker profiles set an explicit `permissionMode`, which keeps CAO from launching the client with `--dangerously-skip-permissions`, and their allowed tools compile to hard `--disallowedTools` for every write, shell, subagent and network tool. Because CAO answers Claude's workspace trust dialog with a bare Enter while its selected option is still "No, exit", setup pre-accepts that dialog for the two roots holding generated snapshots; subdirectories inherit it, so no per-task write is needed. Trust states that content is safe to read and grants no tool permission.

CAO decides that a worker's turn has ended by reading its terminal pane, which sometimes captures a still-streaming answer and tears the session down mid-sentence. A Claude worker therefore delivers its report out of band, through a bundled MCP server exposing one tool: the call carries the whole answer atomically, so the result is durable before the pane is read at all. The tool is pre-authorized for that run only, inside the staged snapshot, and its destination is derived from the run directory rather than accepted from the caller — the worker gains a delivery channel, not write access. Providers without the channel keep using the terminal capture, and an answer that arrives truncated is retried on the same worker before the role degrades to another provider.

Codex needs the same kind of preparation for a different reason: it asks about an unseen directory and then writes the answer back into its own configuration, and that write fails whenever any unrelated entry in the file does not pass its stricter write-time validation, leaving the worker parked on a prompt until the step times out. Its trust is not inherited from parent directories, so the exact staging directory is declared before the step and withdrawn after it, by appending and removing one block verbatim rather than through a parser, leaving every other byte of a hand-edited file untouched. Treat third-party provider implementations and their effective tools as part of the deployment's trust boundary.

## Public MCP contracts

`secondbrain` exposes `search_second_brain(query, limit=6)`, `read_second_brain_note(path)`, `search_second_brain_evidence(query, limit=4)` and `related_second_brain_notes(path, limit=12)`.

Canonical folders are Projects, Areas, Knowledge and Research. Only Sources with disposition `knowledge` become evidence; other Sources stay restricted. Searches require a complete access migration. Existing point IDs are classified without new embeddings. Search/index operations share the Qdrant file lock and requests use an internal lock. Search results are capped at 10; note reads at 20,000 characters. Evidence returns short redacted excerpts. Paths, titles, types, scores and truncation metadata are structured JSON. Relative paths remain identifiers that agents can cite.

`ruflo-team` exposes create/list/status/stop team; create/update task; run read-only task; takeover; recall memory; record reviewed outcome. Team creation requires `writer_provider`. Takeover is only for confirmed quota exhaustion. An exhausted owner is excluded from further calls in that team. Verified memory requires completed work and a passing reviewer; same-provider review is stored as degraded.

## Document pipeline

Ingestor watches `00_Drop`, extracts supported documents and archives originals, then queues Sources. Technical files are ignored without deletion. Librarian performs local triage, requests bounded semantic output through CAO, validates it, materializes Markdown and applies a transaction with hashes/backups/rollback. Qdrant refresh and advisory Ruflo memory follow commit.

Placing documents into Drop authorizes the document pipeline to process them. Initial setup starts its user LaunchAgent. Empty Drop performs no AI work. For testing, use the synthetic vault and pause the Librarian when autonomous processing is unwanted.

## Telemetry

Client activity means a session file changed; it proves neither AI success nor quota. Orchestrated calls carry their own timestamps/outcomes. Quota checks describe the last observed call only. Historical records remain unchanged when display reconciliation corrects classification. Dashboard inventory exposes allowlisted metadata instead of MCP command/argument/environment values.
