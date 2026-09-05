# Troubleshooting

- **Command not found:** use a new shell and add `~/.local/bin` to PATH. Check `command -v codex`, `command -v agy`, `command -v cao`.
- **MCP absent in VS Code:** save work, run Developer: Reload Window (`⇧⌘P`), then create a new session. Global configuration is inherited by local projects; project rules may add restrictions.
- **Antigravity skill missing:** check its skill menu/completion and `/help`; the managed registry is `~/.gemini/config/skills.json`. Do not assume an IDE launcher is the agy AI CLI.
- **Antigravity headless command denied:** a non-interactive `--print` session cannot ask you to approve a command. Start `antigravity` interactively in the project and approve the specific requested operation after reading it. Do not enable blanket permission bypass as an installation workaround.
- **Dashboard unavailable:** run `sbe doctor`; inspect local LaunchAgents/service logs privately. `legend.localhost` and `127.0.0.1` use the same port 8765. Port conflicts must be resolved before installation.
- **Librarian not running:** a completed one-shot run with exit code 0 is normal; the queue watcher and timer start new cycles. Check pending Sources and backoff, not just process presence.
- **Search disabled:** run `sbe brain index`. Interrupted access migration fails closed. Never delete the Qdrant lock or modify the database while another process owns it.
- **Quota exhausted:** wait for renewal or use the documented takeover from the other provider. Do not repeatedly probe a provider the team has excluded.
- **Install conflict:** an unmanaged file or user-edited managed file was preserved. Review differences locally. `--adopt-existing` is only for deliberate migration of the previous SecondBrain installation, not a general overwrite option.
- **Uninstall refuses:** a managed file changed after installation. Preserve and compare it with the private installation ledger; uninstall will not discard later edits. Vault/indexes/backups are never purged automatically.

For issues, attach `sbe doctor` and a minimal synthetic reproduction. Do not attach native MCP config/output, vault documents, task histories, or full logs.
