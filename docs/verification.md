# Verification status — 0.1.0 candidate

This file records evidence, not a promise that every client version works.

| Check | Status |
| --- | --- |
| Legacy runtime suites | 112 Python and 2 JavaScript tests passed on installed Python 3.13 / Node 22 |
| Runtime/package tests | 126 Python tests passed on CPython 3.12.14, including opt-in real embeddings, simultaneous MCP queries and native Ruflo memory |
| Exporter | 5 tests passed: ownership, repeat export, secrets, symlinks, rollback after write failure |
| Fresh Python 3.12 package | Installed in an isolated environment; locked Python dependencies verified and npm clean install passed (611 packages) |
| Installation transactions | Install/update twice, injected install/uninstall failures, uninstall twice, conflicts and state preservation passed in synthetic homes; LaunchAgents were not registered in these tests |
| MCP initialization and safe calls | Both real STDIO servers initialized; four Second Brain and ten Ruflo tools verified; note/read/relations, real search/evidence, restricted paths and concurrent requests passed |
| Installed runtime wrappers | Organizer and Executor launched with the declared Python environment in a synthetic home |
| Native Ruflo memory | Fresh synthetic database initialized on first store; team adapter writes and Librarian adapter retrieves the same record. Both database path variables are aligned; independent Antigravity review PASS on this correction; no private database reinitialization |
| Existing local services | Dashboard and CAO responded; both providers expose separate activity, orchestrated-call and quota records; both global MCP names configured |
| Visual desktop/mobile and native client skill expansion | Pending; browser access was blocked by a saved user preference |
| Codex author / Antigravity reviewer | Independent CAO initial and final hardening reviews returned PASS; no same-provider fallback |
| Antigravity author / Codex reviewer | Blocked: headless Antigravity auto-denied a command permission requiring interactive consent. No bypass enabled |
| Claude as a third provider | Automated suites only: role matrix, reassignment, degraded review, Librarian rotation and cross-provider correction, and the full installer flow including rollback of the Claude client registration. No end-to-end AI run has been executed |
| Public clean clone installation | Pending publication |
| Private installation memory | The earlier installed bridge failed to record an outcome. The corrected adapter has passed synthetic native checks; private persistence needs verification after runtime deployment |

Release is blocked until required checks pass or limitations are explicitly accepted. No screenshot of the private dashboard is distributed as a public fixture.

The checks above ran on Apple Silicon macOS. They do not prove native VS Code UI
behavior or a fresh user's authenticated provider setup. Full system installation
requires writes to Application Support and LaunchAgents outside the agent's
current writable sandbox; it has not been applied to the maintainer's installation.

One isolated indexing process crashed in ONNX Runtime's native telemetry worker
(`SIGSEGV`, Microsoft Applications Events stack). The supported telemetry-disable
API is now called before Qdrant use. The targeted retry and subsequent full suite
passed; this is mitigation evidence, not proof that an intermittent native crash
can never recur. Native cache warnings still appear in the restricted sandbox.
