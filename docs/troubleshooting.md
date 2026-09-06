# Troubleshooting

- **Command not found:** use a new shell and add `~/.local/bin` to PATH. Check `command -v codex`, `command -v agy`, `command -v cao`.
- **MCP absent in VS Code:** save work, run Developer: Reload Window (`⇧⌘P`), then create a new session. Global configuration is inherited by local projects; project rules may add restrictions.
- **Antigravity skill missing:** check its skill menu/completion and `/help`; the managed registry is `~/.gemini/config/skills.json`. Do not assume an IDE launcher is the agy AI CLI.
- **Antigravity headless command denied:** a non-interactive `--print` session cannot ask you to approve a command. Start `antigravity` interactively in the project and approve the specific requested operation after reading it. Do not enable blanket permission bypass as an installation workaround.
- **Dashboard unavailable:** run `sbe doctor`; inspect local LaunchAgents/service logs privately. `legend.localhost` and `127.0.0.1` use the same port 8765. Port conflicts must be resolved before installation.
- **Librarian not running:** a completed one-shot run with exit code 0 is normal; the queue watcher and timer start new cycles. Check pending Sources and backoff, not just process presence.
- **Search disabled:** run `sbe brain index`. Interrupted access migration fails closed. Never delete the Qdrant lock or modify the database while another process owns it.
- **Quota exhausted:** wait for renewal or use the documented takeover from another provider. Do not repeatedly probe a provider the team has excluded.
- **A Codex worker hangs until the step times out:** it is parked on the workspace trust prompt because it could not persist the answer. Setup normally avoids this by declaring the staging directory trusted for the duration of the step; if you see `config/batchWrite failed` in the terminal log, an entry in `~/.codex/config.toml` is failing Codex's write-time validation and should be repaired by whichever tool owns it.
- **A Claude worker fails with an initialization timeout:** its snapshot root lost the pre-accepted workspace trust, so the worker exits at the trust dialog. Rerun setup, which grants it again for `SecondBrainRuflo/TeamRuns` and `SecondBrainLibrarian/ClusterRuns`.
- **A worker reports a missing `VERDICT` line:** the answer was cut short, or only the echoed prompt was captured. Both are refused on purpose: a partial answer is never recorded as an independent review. The same worker is retried once, then the role moves to the next eligible provider. A Claude worker that keeps failing this way is not delivering through its report channel: check that its profile still names the bundled `worker-report-mcp` command and that the path resolved at install time.
- **Install conflict:** an unmanaged file or user-edited managed file was preserved. Review differences locally. `--adopt-existing` is only for deliberate migration of the previous SecondBrain installation, not a general overwrite option.
- **Uninstall refuses:** a managed file changed after installation. Preserve and compare it with the private installation ledger; uninstall will not discard later edits. Vault/indexes/backups are never purged automatically.

For issues, attach `sbe doctor` and a minimal synthetic reproduction. Do not attach native MCP config/output, vault documents, task histories, or full logs.
