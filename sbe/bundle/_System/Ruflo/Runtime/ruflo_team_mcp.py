#!/usr/bin/env python3
"""MCP surface for the restricted Second Brain Ruflo team bridge."""
from mcp.server.mcpserver import MCPServer

from ruflo_team_bridge import (
    create_ruflo_task,
    create_ruflo_team,
    get_ruflo_team_status,
    list_ruflo_teams,
    recall_ruflo_team_memory,
    record_ruflo_review_outcome,
    record_ruflo_verified_outcome,
    run_ruflo_readonly_task,
    stop_ruflo_team,
    take_over_ruflo_team,
    update_ruflo_task,
)


INSTRUCTIONS = """Use this server only for complex work that benefits from separated investigation, execution, and review. The calling session provider is both coordinator and sole writer and must identify itself as writer_provider when creating the team. ONLY REVIEW runs through CAO: the reasoning provider that did not write the code reviews it, so a Claude session gets a Codex reviewer, a Codex session gets a Claude reviewer, and an Antigravity session gets a Claude reviewer. That is a preference, not a constraint: an unavailable provider falls through to the next candidate. Investigation stays with the coordinator, which has the repository, its tools and the conversation; a CAO worker would read a redacted snapshot without any of them, and its answer arrives only after the work has waited for it. Create an investigator task to track that work if you want it recorded, do the work yourself, and close it with update_ruflo_task. Review falls back to the author only when no independent provider remains, and that is recorded as degraded. Only confirmed quota exhaustion permits a handoff: another provider should list active teams and call take_over_ruflo_team before continuing the recorded open tasks. After handoff the exhausted provider is excluded from every later call in that team and must not be probed again. CAO workers receive redacted snapshots and cannot write to the project. Use one active team per objective, keep general autopilot off, and label degraded review honestly in memory."""

server = MCPServer("ruflo-team", instructions=INSTRUCTIONS)
server.tool()(create_ruflo_team)
server.tool()(create_ruflo_task)
server.tool()(run_ruflo_readonly_task)
server.tool()(update_ruflo_task)
server.tool()(get_ruflo_team_status)
server.tool()(list_ruflo_teams)
server.tool()(take_over_ruflo_team)
server.tool()(recall_ruflo_team_memory)
server.tool()(record_ruflo_review_outcome)
server.tool()(record_ruflo_verified_outcome)
server.tool()(stop_ruflo_team)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
