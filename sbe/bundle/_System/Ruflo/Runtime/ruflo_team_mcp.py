#!/usr/bin/env python3
"""MCP surface for the restricted Second Brain Ruflo team bridge."""
from mcp.server.mcpserver import MCPServer

from ruflo_team_bridge import (
    create_ruflo_task,
    create_ruflo_team,
    get_ruflo_team_status,
    list_ruflo_teams,
    recall_ruflo_team_memory,
    record_ruflo_verified_outcome,
    run_ruflo_readonly_task,
    stop_ruflo_team,
    take_over_ruflo_team,
    update_ruflo_task,
)


INSTRUCTIONS = """Use this server only for complex work that benefits from separated investigation, execution, and review. Codex and Antigravity may fill every role. The calling VS Code provider is both coordinator and sole writer and must identify itself as writer_provider when creating the team. Either provider may investigate. Review prefers a provider different from the author and falls back to the same provider only when necessary; this is recorded as degraded. Only confirmed quota exhaustion permits a handoff: the alternate provider should list active teams and call take_over_ruflo_team before continuing the recorded open tasks. After handoff the exhausted provider is excluded from every later call in that team and must not be probed again. CAO workers receive redacted snapshots and cannot write to the project. Use one active team per objective, keep general autopilot off, and label degraded review honestly in memory."""

server = MCPServer("ruflo-team", instructions=INSTRUCTIONS)
server.tool()(create_ruflo_team)
server.tool()(create_ruflo_task)
server.tool()(run_ruflo_readonly_task)
server.tool()(update_ruflo_task)
server.tool()(get_ruflo_team_status)
server.tool()(list_ruflo_teams)
server.tool()(take_over_ruflo_team)
server.tool()(recall_ruflo_team_memory)
server.tool()(record_ruflo_verified_outcome)
server.tool()(stop_ruflo_team)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
