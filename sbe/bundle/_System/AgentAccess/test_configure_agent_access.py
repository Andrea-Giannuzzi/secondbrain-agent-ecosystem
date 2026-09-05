import json
import os
import tempfile
import unittest
from pathlib import Path

from configure_agent_access import install_antigravity_access, configure_cli_alias, configure_antigravity, configure_codex_mcp_approval, install_policy


class ConfigureAgentAccessTests(unittest.TestCase):
    def test_antigravity_merge_preserves_servers_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "mcp_config.json"
            config.write_text(json.dumps({"mcpServers": {"existing": {"command": "keep"}}}))
            self.assertTrue(configure_antigravity(config, "/runtime/secondbrain-mcp"))
            first = config.read_bytes()
            self.assertFalse(configure_antigravity(config, "/runtime/secondbrain-mcp"))
            self.assertEqual(first, config.read_bytes())
            value = json.loads(config.read_text())
            self.assertEqual(value["mcpServers"]["existing"], {"command": "keep"})
            self.assertEqual(value["mcpServers"]["secondbrain"]["args"], [])
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_antigravity_supports_a_second_named_server(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "mcp_config.json"
            configure_antigravity(config, "/runtime/secondbrain-mcp")
            configure_antigravity(config, "/runtime/ruflo-team-mcp", "ruflo-team")
            value = json.loads(config.read_text())
            self.assertEqual(set(value["mcpServers"]), {"secondbrain", "ruflo-team"})
            self.assertEqual(value["mcpServers"]["ruflo-team"]["command"], "/runtime/ruflo-team-mcp")

    def test_policy_replaces_its_block_without_duplication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            instructions = root / "GEMINI.md"
            policy = root / "policy.md"
            instructions.write_text("Keep me.\n")
            policy.write_text("<!-- BEGIN SECONDBRAIN CONSULT POLICY -->\nPolicy one.\n<!-- END SECONDBRAIN CONSULT POLICY -->\n")
            self.assertTrue(install_policy(instructions, policy))
            self.assertFalse(install_policy(instructions, policy))
            self.assertEqual(instructions.read_text().count("BEGIN SECONDBRAIN"), 1)
            self.assertIn("Keep me.", instructions.read_text())

    def test_policy_only_file_is_idempotent_without_leading_blank_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            instructions = root / "GEMINI.md"
            policy = root / "policy.md"
            policy.write_text("<!-- BEGIN SECONDBRAIN CONSULT POLICY -->\nPolicy.\n<!-- END SECONDBRAIN CONSULT POLICY -->\n")
            self.assertTrue(install_policy(instructions, policy))
            first = instructions.read_bytes()
            self.assertFalse(install_policy(instructions, policy))
            self.assertEqual(instructions.read_bytes(), first)
            self.assertFalse(instructions.read_text().startswith("\n"))

    def test_codex_approval_is_scoped_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            config.write_text(
                'approval_policy = "never"\n\n'
                '[mcp_servers.secondbrain]\ncommand = "/semantic"\n\n'
                '[mcp_servers.ruflo-team]\ncommand = "/ruflo"\n'
                'default_tools_approval_mode = "prompt"\n\n'
                '[mcp_servers.other]\ncommand = "/keep"\n'
            )
            self.assertTrue(configure_codex_mcp_approval(config))
            first = config.read_bytes()
            self.assertFalse(configure_codex_mcp_approval(config))
            self.assertEqual(first, config.read_bytes())
            text = config.read_text()
            self.assertEqual(text.count('default_tools_approval_mode = "approve"'), 2)
            self.assertNotIn('default_tools_approval_mode = "prompt"', text)
            self.assertIn('[mcp_servers.other]\ncommand = "/keep"', text)
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()

class GlobalAccessTests(unittest.TestCase):
    def test_install_twice_keeps_existing_settings_and_skills(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); source=home/'source'
            for name in ('secondbrain-consult', 'ruflo-team'):
                path=source/name/'SKILL.md'; path.parent.mkdir(parents=True); path.write_text('---\nname: '+name+'\n---\nInstructions')
            agy=home/'bin/agy'; agy.parent.mkdir(); agy.write_text('#!/bin/sh\nexit 0\n'); agy.chmod(0o700)
            config=home/'.gemini/config/skills.json'; config.parent.mkdir(parents=True); config.write_text('{"entries":[{"path":"~/existing"}],"inherits":[{"path":"~/shared"}]}')
            install_antigravity_access(home,source,agy); first=config.read_bytes()
            install_antigravity_access(home,source,agy)
            self.assertEqual(config.read_bytes(),first)
            self.assertEqual(len(json.loads(first)['entries']),2)
            self.assertEqual((home/'.local/bin/antigravity').resolve(),agy.resolve())
            self.assertEqual((home/'.gemini/config/skills/secondbrain-ecosystem/ruflo-team/SKILL.md').stat().st_mode & 0o777,0o600)

    def test_alias_collision_never_overwrites_user_file(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); agy=home/'agy'; agy.write_text('x'); agy.chmod(0o700)
            alias=home/'antigravity'; alias.write_text('keep')
            with self.assertRaises(ValueError): configure_cli_alias(alias,agy)
            self.assertEqual(alias.read_text(),'keep')
