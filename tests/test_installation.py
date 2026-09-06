import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from sbe.installation import BUNDLE, Transaction, check_started_services, deploy, image, root, uninstall

# Emulates `claude mcp add/remove -s user`, which owns ~/.claude.json.
CLAUDE_STUB = """#!/usr/bin/env python3
import json, sys
from pathlib import Path
config = Path({config!r})
data = json.loads(config.read_text()) if config.exists() else {{}}
servers = data.setdefault("mcpServers", {{}})
argv = sys.argv[1:]
if argv[:4] == ["mcp", "add", "-s", "user"]:
    servers[argv[4]] = {{"type": "stdio", "command": argv[-1]}}
elif argv[:4] == ["mcp", "remove", "-s", "user"]:
    servers.pop(argv[4], None)
else:
    raise SystemExit(1)
config.write_text(json.dumps(data, indent=2))
"""


class InstallationTests(unittest.TestCase):
    def fixture(self, directory):
        home=Path(directory).resolve()/"home"; home.mkdir()
        vault=home/"A vault with spaces"; vault.mkdir()
        bins=home/"bin"; bins.mkdir()
        exe={}
        for name in ("codex","agy","cao","cao-server","tmux","node"):
            p=bins/name; p.write_text("#!/bin/sh\nexit 0\n"); p.chmod(0o700); exe[name]=str(p)
        claude=bins/"claude"; claude.write_text(CLAUDE_STUB.format(config=str(home/".claude.json")))
        claude.chmod(0o700); exe["claude"]=str(claude)
        python=home/"release/venv/bin/python"; python.parent.mkdir(parents=True); python.write_text("fixture")
        cfg=home/".codex/config.toml"; cfg.parent.mkdir()
        cfg.write_text('# Keep this comment\n[mcp_servers.existing]\ncommand="keep"\n')
        g=home/".gemini/config/mcp_config.json"; g.parent.mkdir(parents=True)
        g.write_text('{"mcpServers":{"existing":{"command":"keep","env":{"TOKEN":"private-fixture"}}}}')
        return home,vault,python,exe

    def snapshot(self,home):
        return {str(p.relative_to(home)):image(p) for p in home.rglob("*") if (p.is_file() or p.is_symlink()) and "Backups" not in p.parts and p.name!="install.lock"}

    def test_repeat_install_rollback_uninstall_preserves_state(self):
        with tempfile.TemporaryDirectory() as d:
            home,vault,python,exe=self.fixture(d)
            state=home/"Library/Application Support/SecondBrainSemantic/qdrant/data"; state.parent.mkdir(parents=True); state.write_bytes(b"private index")
            initial=self.snapshot(home)
            for _ in range(2):
                deploy(home,vault,python,["codex","antigravity"],exe,services=False)
                self.assertEqual(state.read_bytes(),b"private index")
            first=self.snapshot(home)
            with self.assertRaises(RuntimeError):
                deploy(home,vault,python,["codex","antigravity"],exe,services=False,fail_after=True)
            self.assertEqual(first,self.snapshot(home))
            with self.assertRaises(RuntimeError):
                uninstall(home,services=False,fail_after=3)
            self.assertEqual(first,self.snapshot(home))
            self.assertIn("Keep this comment",(home/".codex/config.toml").read_text())
            reg=json.loads((home/".gemini/config/skills.json").read_text())
            self.assertEqual(len(reg["entries"]),1)
            self.assertEqual(len(list((home/".aws/cli-agent-orchestrator/agent-store").glob("*.md"))),7)
            for _ in range(2):
                uninstall(home,services=False)
            self.assertEqual(initial,self.snapshot(home))

    def test_claude_client_is_configured_idempotently_and_reversibly(self):
        with tempfile.TemporaryDirectory() as d:
            home,vault,python,exe=self.fixture(d)
            # Same formatting the stub writes back, so the snapshot compares content only.
            (home/".claude.json").write_text(json.dumps({"mcpServers":{"existing":{"type":"stdio","command":"keep"}}},indent=2))
            initial=self.snapshot(home)
            for _ in range(2):
                deploy(home,vault,python,["claude"],exe,services=False)
            installed=self.snapshot(home)
            servers=json.loads((home/".claude.json").read_text())["mcpServers"]
            self.assertEqual(servers["existing"],{"type":"stdio","command":"keep"})
            self.assertEqual(servers["ruflo-team"]["command"],
                             str(home/"Library/Application Support/SecondBrainRuflo/team-runtime/ruflo-team-mcp"))
            self.assertIn("secondbrain",servers)
            self.assertIn("BEGIN SECONDBRAIN CONSULT POLICY",(home/".claude/CLAUDE.md").read_text())
            # CAO cannot answer Claude's workspace trust dialog, so the snapshot
            # roots are trusted once and their subdirectories inherit it.
            projects=json.loads((home/".claude.json").read_text())["projects"]
            support=home/"Library/Application Support"
            for root in (support/"SecondBrainRuflo/TeamRuns", support/"SecondBrainLibrarian/ClusterRuns"):
                self.assertTrue(projects[str(root)]["hasTrustDialogAccepted"])
            self.assertTrue((home/".claude/skills/ruflo-team/SKILL.md").is_file())
            # Only the Claude profiles are installed for a Claude-only deployment.
            profiles={p.name for p in (home/".aws/cli-agent-orchestrator/agent-store").glob("*.md")}
            self.assertEqual(profiles,{"ruflo_claude_readonly_worker.md","librarian_claude_cluster_semantic.md",
                                       "librarian_claude_cluster_semantic_reviewer.md"})
            with self.assertRaises(RuntimeError):
                deploy(home,vault,python,["claude"],exe,services=False,fail_after=True)
            self.assertEqual(installed,self.snapshot(home))
            uninstall(home,services=False)
            self.assertEqual(initial,self.snapshot(home))

    def test_failed_first_install_restores_initial_config(self):
        with tempfile.TemporaryDirectory() as d:
            home,vault,python,exe=self.fixture(d); before=self.snapshot(home)
            with self.assertRaises(RuntimeError):
                deploy(home,vault,python,["codex"],exe,services=False,fail_after=True)
            self.assertEqual(before,self.snapshot(home))

    def test_unmanaged_alias_and_malformed_config_fail_without_loss(self):
        with tempfile.TemporaryDirectory() as d:
            home,vault,python,exe=self.fixture(d)
            alias=home/".local/bin/antigravity"; alias.parent.mkdir(parents=True); alias.write_text("user command")
            before=self.snapshot(home)
            for adopt in (False, True):
                with self.assertRaises(ValueError):
                    deploy(home,vault,python,["antigravity"],exe,services=False,adopt=adopt)
                self.assertEqual(before,self.snapshot(home))

    def test_uninstall_refuses_modified_managed_config(self):
        with tempfile.TemporaryDirectory() as d:
            home,vault,python,exe=self.fixture(d)
            deploy(home,vault,python,["codex"],exe,services=False)
            p=home/".codex/config.toml"; p.write_text(p.read_text()+"\n# My new setting\n")
            before=self.snapshot(home)
            with self.assertRaises(ValueError): uninstall(home,services=False)
            self.assertEqual(before,self.snapshot(home))

    def test_reject_symlink_parent(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d).resolve()/"home";home.mkdir();outside=Path(d).resolve()/"outside";outside.mkdir()
            (home/"config").symlink_to(outside,target_is_directory=True)
            with Transaction(home) as tx:
                with self.assertRaises(ValueError):tx.write(home/"config/file",b"data")
            self.assertFalse((outside/"file").exists())

    def test_loaded_but_stopped_service_is_not_healthy(self):
        with patch("sbe.installation.safe_run",return_value=SimpleNamespace(stdout="state = waiting\nlast exit code = 1")):
            with self.assertRaises(ValueError):
                check_started_services("gui/fixture",["com.local.secondbrain.dashboard"],timeout=0)

    def test_ledger_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            home,vault,python,exe=self.fixture(d)
            base=root(home);base.mkdir(parents=True)
            (base/"installed.json").write_text(json.dumps({"files":{str(home/"../outside"): {"before":None,"after":None}}}))
            with self.assertRaises(ValueError):
                uninstall(home,services=False)


if __name__=="__main__":unittest.main()
