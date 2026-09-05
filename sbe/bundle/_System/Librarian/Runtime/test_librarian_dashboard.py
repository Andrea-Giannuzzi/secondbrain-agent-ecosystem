import datetime as dt
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
import re
from unittest.mock import patch

import librarian_dashboard as dashboard


class LibrarianDashboardTests(unittest.TestCase):
    def test_dashboard_identifies_ecosystem_and_has_unique_navigation_targets(self):
        assets = Path(__file__).parent.parent / "Dashboard"
        html = (assets / "index.html").read_text(encoding="utf-8")
        javascript = (assets / "dashboard.js").read_text(encoding="utf-8")
        css = (assets / "dashboard.css").read_text(encoding="utf-8")
        self.assertIn("<title>Dashboard — Ecosystem Control Room</title>", html)
        self.assertIn("<h1>Dashboard</h1>", html)
        ids = re.findall(r'id="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))
        for target in ("ecosystem", "ruflo-teams-section", "provider-activity", "agent-profiles", "librarian"):
            self.assertIn(f'href="#{target}"', html)
            self.assertIn(target, ids)
        self.assertIn("handoff richiesto", javascript)
        self.assertIn("fallback degradato", javascript)
        self.assertIn("Stesso provider · fallback degradato", html)
        self.assertIn(".service-line b { line-height: 1.35; text-align: left; }", css)
        self.assertIn("last_quota_check", javascript)
        self.assertIn("client_activity", javascript)

    def test_dashboard_origins_include_memorable_alias_and_legacy_hosts(self):
        origins = dashboard.allowed_dashboard_origins("127.0.0.1", 8765)
        self.assertIn("http://legend.localhost:8765", origins)
        self.assertIn("http://127.0.0.1:8765", origins)
        self.assertIn("http://localhost:8765", origins)

    def test_source_snapshot_separates_ready_and_backoff(self):
        base = dt.datetime.now(dt.timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            sources = vault / "50_Sources"
            system = vault / "_System/Librarian"
            sources.mkdir(parents=True)
            system.mkdir(parents=True)
            ready = sources / "Ready.md"
            waiting = sources / "Waiting.md"
            organized = sources / "Done.md"
            ready.write_text('---\nstatus: "unprocessed"\nneeds_librarian: true\n---\n', encoding="utf-8")
            waiting.write_text('---\nstatus: "unprocessed"\nneeds_librarian: true\n---\n', encoding="utf-8")
            organized.write_text('---\nstatus: "organized"\nneeds_librarian: false\n---\n[[30_Knowledge/Node|Node]]\n', encoding="utf-8")
            invalid = system / "ai-invalid-state.json"
            invalid.write_text(json.dumps({"sources": {
                "50_Sources/Waiting.md": {
                    "source_sha256": hashlib.sha256(waiting.read_bytes()).hexdigest(),
                    "next_retry_at": (base + dt.timedelta(hours=1)).isoformat(),
                    "consecutive_failures": 2,
                    "errors": ["invalid"],
                }
            }}), encoding="utf-8")
            with patch.multiple(
                dashboard,
                VAULT=vault,
                SOURCES=sources,
                AI_INVALID_STATE=invalid,
            ):
                value = dashboard.source_snapshot()
            self.assertEqual(value["total"], 3)
            self.assertEqual(value["organized"], 1)
            self.assertEqual(value["linked"], 1)
            self.assertEqual(value["unlinked"], 2)
            self.assertEqual(value["unlinked_breakdown"]["pending"], 2)
            self.assertEqual([item["title"] for item in value["pending_ready"]], ["Ready"])
            self.assertEqual(value["backoff"][0]["failures"], 2)

    def test_pause_circuit_is_persistent_and_manual(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            circuit = root / "provider-circuit.json"
            lock = root / "provider-circuit.lock"
            circuit.write_text(json.dumps({"state": "closed", "consecutive_dual_failures": 0}), encoding="utf-8")
            with patch.multiple(dashboard, PROVIDER_CIRCUIT=circuit, PROVIDER_CIRCUIT_LOCK=lock):
                dashboard.pause_circuit()
            value = json.loads(circuit.read_text())
            self.assertEqual(value["state"], "open")
            self.assertTrue(value["manual_pause"])
            self.assertEqual(value["next_probe_at"], "2099-01-01T00:00:00+01:00")

    def test_retry_one_source_keeps_other_backoff_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_path = root / "ai-invalid-state.json"
            lock = root / "ai-invalid-state.lock"
            state_path.write_text(json.dumps({"sources": {"a": {}, "b": {}}}), encoding="utf-8")
            with patch.multiple(dashboard, AI_INVALID_STATE=state_path, AI_INVALID_LOCK=lock):
                with patch.object(dashboard, "kickstart_service") as kickstart:
                    dashboard.retry_source("a")
            value = json.loads(state_path.read_text())
            self.assertEqual(set(value["sources"]), {"b"})
            kickstart.assert_called_once_with()

    def test_provider_observations_use_latest_real_attempt_per_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            runs = Path(temp)
            older = runs / "older" / "manifest.json"
            newer = runs / "newer" / "manifest.json"
            older.parent.mkdir()
            newer.parent.mkdir()
            older.write_text(json.dumps({
                "updated_at": "2026-09-04T08:00:00+02:00",
                "provider_attempts": [
                    {"provider": "antigravity", "status": "unavailable", "error": "quota reached"},
                    {"provider": "codex", "status": "started"},
                ],
            }), encoding="utf-8")
            newer.write_text(json.dumps({
                "updated_at": "2026-09-04T09:00:00+02:00",
                "provider_attempts": [
                    {"provider": "antigravity", "status": "started"},
                ],
            }), encoding="utf-8")
            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))
            with patch.object(dashboard, "RUNS", runs):
                value = dashboard.provider_observations({"antigravity": True, "codex": True, "other": False})
            self.assertEqual(value["antigravity"]["status"], "started")
            self.assertEqual(value["antigravity"]["observed_at"], "2026-09-04T09:00:00+02:00")
            self.assertEqual(value["codex"]["status"], "started")
            self.assertEqual(value["other"]["status"], "not_installed")

    def test_only_quota_failures_request_provider_handoff(self):
        calls = [
            {"provider": "codex", "status": "unavailable", "error": "quota reached", "observed_at": "2026-09-04T10:00:00+02:00"},
            {"provider": "antigravity", "status": "unavailable", "error": "network disconnected", "observed_at": "2026-09-04T10:00:00+02:00"},
        ]
        with patch.object(dashboard, "RUNS", Path("/missing")):
            value = dashboard.provider_observations({"codex": True, "antigravity": True}, calls)
        self.assertTrue(value["codex"]["quota_exhausted"])
        self.assertFalse(value["antigravity"]["quota_exhausted"])

    def test_provider_observations_include_newer_ruflo_activity(self):
        with tempfile.TemporaryDirectory() as temp:
            runs = Path(temp)
            manifest = runs / "older" / "manifest.json"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps({
                "updated_at": "2026-09-04T09:00:00+02:00",
                "cluster_title": "Cluster",
                "provider_attempts": [{"provider": "antigravity", "status": "started"}],
            }), encoding="utf-8")
            ruflo_call = {
                "provider": "antigravity", "status": "completed",
                "observed_at": "2026-09-04T10:00:00+02:00",
                "source": "ruflo", "subject": "Review MIRA",
            }
            with patch.object(dashboard, "RUNS", runs):
                value = dashboard.provider_observations({"antigravity": True}, [ruflo_call])
            self.assertEqual(value["antigravity"]["status"], "completed")
            self.assertEqual(value["antigravity"]["source"], "ruflo")
            self.assertEqual(value["antigravity"]["subject"], "Review MIRA")

    def test_general_provider_activity_uses_only_file_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            codex = root / "codex"
            antigravity = root / "antigravity"
            (codex / "2026/09/04").mkdir(parents=True)
            (antigravity / "log").mkdir(parents=True)
            codex_session = codex / "2026/09/04/session.jsonl"
            antigravity_log = antigravity / "log/cli.log"
            codex_session.write_text("SECRET PROMPT", encoding="utf-8")
            antigravity_log.write_text("SECRET OUTPUT", encoding="utf-8")
            os.utime(codex_session, (100, 100))
            os.utime(antigravity_log, (200, 200))
            with patch.multiple(dashboard, CODEX_SESSIONS=codex, ANTIGRAVITY_CLI=antigravity, ANTIGRAVITY_APP=root / "missing", ANTIGRAVITY_IDE=root / "missing2"):
                activity = dashboard.general_provider_activity()
            self.assertEqual([item["provider"] for item in activity], ["antigravity", "codex"])
            self.assertTrue(all(item["privacy"] == "metadata_only" for item in activity))
            self.assertNotIn("SECRET", json.dumps(activity))

    def test_provider_observation_keeps_orchestrated_status_and_adds_general_activity(self):
        general = [{
            "provider": "codex", "status": "observed", "observed_at": "2026-09-04T15:00:00+02:00",
            "source": "general", "subject": "Sessione Codex aggiornata", "privacy": "metadata_only",
        }]
        with patch.object(dashboard, "RUNS", Path("/missing")):
            value = dashboard.provider_observations({"codex": True}, [], general)
        self.assertEqual(value["codex"]["status"], "unknown")
        self.assertEqual(value["codex"]["general_activity"]["observed_at"], "2026-09-04T15:00:00+02:00")

    def test_ruflo_team_snapshot_counts_only_active_teams(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "teams.json"
            state.write_text(json.dumps({"teams": {
                "active": {
                    "team_id": "active", "objective": "Inspect", "status": "active",
                    "updated_at": "2026-09-04T10:00:00+02:00",
                    "circuit": {"state": "PAUSED_QUOTA"},
                    "coordinator_provider": "codex",
                    "writer_provider": "codex", "reviewer_provider": "antigravity",
                    "degraded_failover": True,
                    "unavailable_providers": {"antigravity": {"reason": "quota"}},
                    "last_review_mode": "same_provider_fallback",
                    "last_handoff": {
                        "from_provider": "antigravity", "to_provider": "codex",
                        "reason": "quota", "at": "2026-09-04T09:55:00+02:00",
                    },
                    "agents": {"investigator": "agent-investigator"},
                    "tasks": {
                        "one": {
                            "task_id": "one", "role": "investigator", "status": "in_progress",
                            "description": "Inspect", "progress": 20, "updated_at": "2026-09-04T10:00:00+02:00",
                            "provider_attempts": [{"provider": "antigravity", "status": "started"}],
                        },
                        "two": {
                            "task_id": "two", "role": "reviewer", "status": "completed",
                            "provider": "codex", "review_independent": False,
                            "review_mode": "same_provider_fallback",
                            "worker_verdict": "PASS",
                            "provider_attempts": [
                                {"provider": "antigravity", "status": "unavailable"},
                                {"provider": "codex", "status": "completed"},
                            ],
                        },
                    },
                },
                "old": {
                    "team_id": "old", "objective": "Old", "status": "stopped",
                    "updated_at": "2026-09-04T09:00:00+02:00", "agents": {},
                    "tasks": {"three": {"task_id": "three", "role": "executor", "status": "pending"}},
                },
            }}), encoding="utf-8")
            with patch.object(dashboard, "RUFLO_TEAM_STATE", state):
                value = dashboard.ruflo_team_snapshot()
            self.assertEqual(value["active_teams"], 1)
            self.assertEqual(value["active_tasks"], 1)
            self.assertEqual(value["running_tasks"], 1)
            self.assertEqual(value["paused_quota"], 1)
            self.assertEqual(len(value["teams"]), 2)
            self.assertEqual(len(value["tasks"]), 3)
            self.assertEqual(value["agents"][0]["status"], "working")
            self.assertEqual(value["provider_calls"][0]["source"], "ruflo")
            self.assertTrue(value["teams"][0]["provider_separation"])
            self.assertTrue(value["teams"][0]["degraded_failover"])
            self.assertEqual(value["teams"][0]["unavailable_providers"], ["antigravity"])
            self.assertEqual(value["teams"][0]["last_handoff"]["to_provider"], "codex")
            investigator_task = next(item for item in value["tasks"] if item["task_id"] == "one")
            self.assertEqual(investigator_task["provider"], "codex")
            self.assertEqual(value["agents"][0]["provider"], "codex")
            self.assertEqual(value["agents"][0]["eligible_providers"], ["codex"])
            reviewer = next(item for item in value["tasks"] if item["task_id"] == "two")
            self.assertEqual(reviewer["review_mode"], "same_provider_fallback")
            self.assertEqual(reviewer["worker_verdict"], "PASS")
            self.assertTrue(reviewer["fallback_used"])

    def test_cao_fleet_discovers_new_ecosystem_profiles(self):
        profiles = [
            {"name": "ruflo_new_specialist", "description": "New", "role": "reviewer", "loadable": True, "source": "local"},
            {"name": "unrelated", "description": "Other", "role": "developer", "loadable": True, "source": "built-in"},
        ]

        def fake_get(path, default):
            if path == "/sessions":
                return [{"name": "cao-test", "status": "detached", "agent_profile": "ruflo_new_specialist"}]
            if path == "/sessions/cao-test/terminals":
                return [{
                    "id": "abc", "agent_profile": "ruflo_new_specialist", "provider": "codex",
                    "last_active": "2026-09-04T10:00:00+02:00", "working_directory": "/tmp/run/workspace",
                }]
            return default

        with patch.object(dashboard, "cao_get", side_effect=fake_get):
            value = dashboard.cao_fleet_snapshot(profiles)
        self.assertEqual([item["name"] for item in value["profiles"]], ["ruflo_new_specialist"])
        self.assertEqual(value["profiles"][0]["last_active"], "2026-09-04T10:00:00+02:00")
        self.assertEqual(value["profiles"][0]["system"], "Ruflo Team")
        self.assertEqual(value["profiles"][0]["lifecycle"], "legacy")
        self.assertEqual(value["profiles"][0]["status"], "historical")
        self.assertEqual(value["terminals"][0]["workspace"], "run")

    def test_current_profile_can_be_ready_while_historical_profile_cannot(self):
        profiles = [
            {"name": "ruflo_codex_readonly_worker", "loadable": True, "role": "reviewer"},
            {"name": "librarian_codex", "loadable": True, "role": "reviewer"},
        ]
        with patch.object(dashboard, "cao_get", return_value=[]):
            value = dashboard.cao_fleet_snapshot(profiles)
        by_name = {item["name"]: item for item in value["profiles"]}
        self.assertEqual(by_name["ruflo_codex_readonly_worker"]["status"], "configured")
        self.assertEqual(by_name["ruflo_codex_readonly_worker"]["function"], "Investigatore o revisore")
        self.assertEqual(by_name["librarian_codex"]["status"], "historical")


if __name__ == "__main__":
    unittest.main()

class TelemetryMigrationTests(unittest.TestCase):
    def test_three_signals_are_independent(self):
        calls = [{"provider":"antigravity", "status":"unavailable", "error":"HTTP 429", "observed_at":"2026-09-04T22:27:00+02:00", "source":"ruflo"}]
        activity = [{"provider":"antigravity", "client":"CLI", "observed_at":"2026-09-04T23:57:00+02:00"}]
        with patch.object(dashboard, "librarian_provider_calls", return_value=[]):
            row = dashboard.provider_observations({"antigravity":True}, calls, activity)['antigravity']
        self.assertEqual(row['client_activity'][0]['observed_at'], activity[0]['observed_at'])
        self.assertEqual(row['last_orchestrated_call']['status'], 'unavailable')
        self.assertEqual(row['last_quota_check']['status'], 'exhausted')

    def test_reconcile_only_attempt_with_own_verdict(self):
        task={'status':'completed', 'provider':'codex', 'worker_verdict':'PASS'}
        failed={'provider':'antigravity', 'status':'unavailable', 'error':'HTTP 429'}
        self.assertEqual(dashboard.reconciled_attempt_status(task, failed), 'unavailable')
        valid=dict(failed, error='Provider technical failure: Discuss quota and HTTP 429\nVERDICT: PASS')
        self.assertEqual(dashboard.reconciled_attempt_status(task, valid), 'completed')
        self.assertEqual(valid['status'], 'unavailable')

    def test_builtins_are_separate_and_history_never_ready(self):
        with patch.object(dashboard, 'cao_get', return_value=[]):
            row=dashboard.cao_fleet_snapshot([{'name':'generic', 'source':'built-in', 'loadable':True}, {'name':'librarian_old', 'loadable':True}])
        self.assertEqual(len(row['builtin_profiles']),1)
        self.assertEqual(row['profiles'][0]['status'],'historical')

    def test_network_error_preserves_last_conclusive_quota_check(self):
        calls=[{'provider':'codex','status':'unavailable','error':'network disconnected','observed_at':'2026-09-05T02:00:00+02:00'},
               {'provider':'codex','status':'completed','observed_at':'2026-09-05T01:00:00+02:00'}]
        with patch.object(dashboard,'librarian_provider_calls',return_value=[]):
            row=dashboard.provider_observations({'codex':True},calls)['codex']
        self.assertEqual(row['last_orchestrated_call']['status'],'unavailable')
        self.assertEqual(row['last_quota_check']['status'],'usable')
        self.assertEqual(row['last_quota_check']['observed_at'],calls[1]['observed_at'])

    def test_mentions_of_quota_are_not_quota_measurements(self):
        self.assertFalse(dashboard.quota_error('Could not parse the quota dashboard document'))
        self.assertTrue(dashboard.quota_error('HTTP 429'))
