import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from sbe.installation import BUNDLE, Transaction, check_started_services, deploy, image, root, uninstall


class InstallationTests(unittest.TestCase):
    def fixture(self, directory):
        home=Path(directory).resolve()/"home"; home.mkdir()
        vault=home/"A vault with spaces"; vault.mkdir()
        bins=home/"bin"; bins.mkdir()
        exe={}
        for name in ("codex","agy","cao","cao-server","tmux","node"):
            p=bins/name; p.write_text("#!/bin/sh\nexit 0\n"); p.chmod(0o700); exe[name]=str(p)
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
