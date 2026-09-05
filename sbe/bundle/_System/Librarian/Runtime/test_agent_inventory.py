import json
import tempfile
import unittest
from pathlib import Path
from agent_inventory import agent_inventory

class InventoryTests(unittest.TestCase):
    def test_allowlist_never_emits_config_values_or_skill_body(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            cfg = home / '.gemini/config'
            cfg.mkdir(parents=True)
            (cfg / 'mcp_config.json').write_text(json.dumps({'mcpServers': {'secondbrain': {'command': 'SECRET', 'args': ['SECRET'], 'env': {'TOKEN': 'SECRET'}}, 'off': {'url': 'SECRET', 'enabled': False}}}))
            skill = cfg / 'skills/example/SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.write_text('---\nname: example\n---\nSECRET body')
            (cfg / 'hooks.json').write_text(json.dumps({'audit': {'Stop': [{'command': 'SECRET'}]}}))
            result = agent_inventory(home)
            self.assertNotIn('SECRET', json.dumps(result))
            self.assertEqual(next(x for x in result['items'] if x['name'] == 'off')['status'], 'disabled')
            self.assertEqual(next(x for x in result['items'] if x['kind'] == 'Hook')['events'], ['Stop'])

    def test_symlinks_and_malformed_configs_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / 'home'; home.mkdir()
            cfg = home / '.gemini/config'; cfg.mkdir(parents=True)
            target = Path(temp) / 'secret'; target.write_text('{"mcpServers":{"SECRET":{"command":"x"}}}')
            (cfg / 'mcp_config.json').symlink_to(target)
            (cfg / 'skills.json').write_text('[]')
            result = agent_inventory(home)
            self.assertNotIn('SECRET', json.dumps(result))
            self.assertEqual(len(result['issues']), 2)

    def test_inherited_skill_paths_are_explicitly_unverified(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); cfg=home/'.gemini/config'; cfg.mkdir(parents=True)
            (cfg/'skills.json').write_text('{"inherits":[{"path":"/outside"}]}')
            self.assertEqual(agent_inventory(home)['issues'][0]['status'], 'inherited_not_scanned')
