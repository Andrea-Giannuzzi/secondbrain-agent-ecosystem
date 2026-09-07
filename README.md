# Second Brain Agent Ecosystem

A local macOS dashboard, document Librarian, semantic search and bounded Claude–Codex–Antigravity teams coordinated through Ruflo and CLI Agent Orchestrator (CAO).

**Release candidate 0.1.2.** See [verification status](docs/verification.md) before relying on a feature. [Guida completa in italiano](docs/it/README.md).

## What runs where

```text
VS Code or terminal: Claude / Codex / Antigravity — coordinator and only project writer
                         │
                    Ruflo team MCP — persistent tasks and reviewed memory
                         │
                        CAO — investigator and reviewer in redacted snapshots

00_Drop → Ingestor → Sources → Librarian → validated transaction → Knowledge
                               │                              │
                          CAO providers                  local Qdrant
                                                              │
                           read-only Second Brain MCP ← agents in any project
```

Four roles describe a workflow; they do not imply four models running simultaneously. The provider of the session coordinates and writes; the other two split the remaining roles. Antigravity is the preferred investigator whenever it does not coordinate, and the reasoning provider that did not write the code reviews it:

| Session provider | Investigator | Reviewer |
| --- | --- | --- |
| Claude | Antigravity | Codex |
| Codex | Antigravity | Claude |
| Antigravity | Codex | Claude |

These are preferences, not constraints: an unavailable provider falls through to the next candidate, and only when no independent provider remains does review fall back to the author, explicitly degraded. The Librarian rotates the head of its own provider chain so the three share the load, and its correction pass starts from a provider that did not produce the semantics. The deterministic Librarian Executor alone writes canonical notes; coding agents only read the vault through MCP.

Ruflo is a third-party coordination/memory dependency, not the canonical knowledge archive. This project is an integration and is not affiliated with OpenAI, Google, AWS or Ruflo.

## Prerequisites

- macOS, Python 3.12+, Node.js 20+, npm and a local vault under your home directory.
- At least one authenticated client: `claude`, `codex` or Antigravity CLI `agy`. At least two are needed for independent cross-provider review.
- `cao` and `cao-server` (tested baseline: cli-agent-orchestrator 2.5.0), `tmux`, Poppler and Tesseract.
- VS Code is optional; the same clients work in Terminal. Remote SSH/container workspaces are outside v0.1 support.

With Homebrew and uv already installed:

```sh
brew install python@3.12 node tmux poppler tesseract
uv tool install 'cli-agent-orchestrator==2.5.0'
```

Install and log in to the clients using their own supported installers. `agy` must be the AI terminal client, not an IDE launcher with the same name. Tested local CLI baselines: Codex 0.153.2, agy 1.1.27 and Claude Code 2.1.263. Setup does not install, sign in to or purchase access to a provider.

## Install

```sh
git clone https://github.com/Andrea-Giannuzzi/secondbrain-agent-ecosystem.git
cd secondbrain-agent-ecosystem
./install.sh
```

Choose a dedicated vault directory, for example `~/Documents/SecondBrain`. Keep the cloned code repository outside the vault. Setup creates a private Python environment, installs dependencies and Ruflo 3.38.21, registers local LaunchAgents, MCPs, skills and CAO profiles. Model files for local embeddings download on the first index/search; searches then use local compute and no AI quota.

Python transitive dependencies are pinned with hashes in `requirements.lock`;
Node dependencies use `package-lock.json` and `npm ci`. Authentication is confirmed
by the operator during setup; `sbe doctor` does not claim to validate provider login
or remaining quota.

Open a new terminal with `~/.local/bin` on PATH, then:

```sh
sbe doctor
sbe brain index
sbe dashboard open
```

The dashboard address is **http://legend.localhost:8765**. `http://127.0.0.1:8765` and `http://localhost:8765` also work. Services bind only to loopback. Quota cards show evidence from the last call, never a guarantee of remaining quota now.

In VS Code, save work, open the command palette (`⇧⌘P`), select **Developer: Reload Window**, then start a new agent session. CLI sessions must also be restarted after configuration changes.

For a demo, copy `examples/vault/30_Knowledge/` into a newly created, empty vault and run `sbe brain index`. The examples are synthetic and not extracted from the author's vault.

## Everyday use

From any local project directory:

```sh
codex
# or
antigravity
# or
claude
```

The installed policies request Second Brain when prior knowledge is relevant and Ruflo for complex work. Loading an MCP or skill does not prove it was invoked. Confirm a tool call or a team/task in the dashboard. For explicit activation, use the skill picker/completion offered by your client:

| Intent | Codex prompt | Antigravity / Claude prompt |
| --- | --- | --- |
| Prior knowledge | `$secondbrain-consult Find relevant notes and cite their paths.` | `/secondbrain-consult Find relevant notes and cite their paths.` |
| Bounded team | `$ruflo-team Investigate, implement and review this task: …` | `/ruflo-team Investigate, implement and review this task: …` |

You can supply an initial prompt from the shell (single quotes preserve the `$`):

```sh
codex -C "$PWD" '$ruflo-team Investigate and review the task described below.'
antigravity --prompt-interactive '/ruflo-team Investigate and review the task described below.'
claude '/ruflo-team Investigate and review the task described below.'
```

Client versions can change skill expansion. If the skill does not appear in completion, use `/help` and request `Use the installed ruflo-team skill and its MCP tools`; inspect `sbe doctor`. Do not assume an invented `/ruflo` command exists. End-to-end expansion must be verified against the installed client version.

## Terminal reference

| Command | Behavior |
| --- | --- |
| `sbe doctor` | Safe names/version/configuration/service checks; no AI calls, no MCP secrets |
| `sbe dashboard open` / `status` | Open UI / read local status |
| `sbe librarian status` | Read document queue/service status |
| `sbe librarian run` | Start a cycle; may consume AI quota and apply validated notes |
| `sbe librarian pause` / `resume` | Pause / resume autonomous AI calls |
| `sbe brain index` | Initialize/update local embeddings and access classification |
| `sbe brain search "query"` | Search canonical notes |
| `sbe brain evidence "query"` | Search short redacted Source evidence |
| `sbe brain read "30_Knowledge/Local Search.md"` | Read a canonical Markdown note |
| `sbe brain related "30_Knowledge/Local Search.md"` | Canonical wikilinks and backlinks |
| `sbe team list` / `sbe team status TEAM_ID` | Inspect durable teams/tasks |
| `sbe uninstall` | Restore unchanged managed files; preserve vault, indexes and backups |

The search tools cannot write, read attachments, enter `_System`, or expose restricted Sources. The full tool contracts are in [architecture](docs/architecture.md).

## Quota and handoff

CAO/Librarian try the eligible alternate providers when a worker call fails, and the Librarian rotates which one leads each cycle. When none can run, bounded circuits pause retries. Network errors are not quota measurements.

A writer session with no quota cannot start another client itself. Open another provider in the same project and say:

> Use ruflo-team. The previous writer returned a confirmed quota exhaustion error. List the active teams, take over the matching team and continue its open tasks. Do not call the exhausted provider again. Mark same-provider review as degraded.

Never request takeover merely because a response is slow or a generic error occurred. No autonomous client-launching supervisor is included.

## Updates, privacy and contributions

The author's local `_System` remains the development source. GitHub is a reviewed distribution generated by an allowlist exporter. No update pulls GitHub code into the author's vault automatically. Users can pull a tagged release and rerun setup; see [migration](docs/migration.md).

Do not publish native `antigravity mcp list` output: some clients show headers/keys of unrelated servers. Use `sbe doctor` for shareable diagnostics. Dashboard status, task names and note paths can still be personal; use synthetic fixtures for screenshots.

- [Architecture and trust boundaries](docs/architecture.md)
- [Privacy](docs/privacy.md) · [Troubleshooting](docs/troubleshooting.md)
- [Migration and release process](docs/migration.md) · [Verification](docs/verification.md)
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Code of conduct](CODE_OF_CONDUCT.md)

Project code is Apache-2.0. Dependencies and provider clients retain their own licenses and terms; see [NOTICE](NOTICE).
