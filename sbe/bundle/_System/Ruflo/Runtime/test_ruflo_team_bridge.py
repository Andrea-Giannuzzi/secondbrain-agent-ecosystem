import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import ruflo_team_bridge as bridge
import worker_report_mcp as delivery


class RufloTeamBridgeTests(unittest.TestCase):
    def test_cli_parser_returns_root_result_instead_of_nested_config(self):
        raw = 'prefix {"echo":true}\nResult:\n{"success":true,"swarmId":"safe","config":{"nested":true}}\n'
        value = bridge._decode_last_object(raw)
        self.assertEqual(value["swarmId"], "safe")

    def runtime(self, root: Path):
        state_dir = root / "state"
        return patch.multiple(
            bridge,
            RUNTIME_ROOT=root,
            STATE_DIR=state_dir,
            STATE_PATH=state_dir / "teams.json",
            LOCK_PATH=state_dir / "teams.lock",
            RUNS_DIR=root / "runs",
            RUFLO_DB=root / "data/memory.db",
        )

    def seed_team(self, root: Path, project: Path, *, task_role="reviewer", writer_provider="codex"):
        state_dir = root / "state"
        state_dir.mkdir(parents=True)
        state = bridge._default_state()
        state["teams"]["team-safe"] = {
            "team_id": "team-safe",
            "ruflo_swarm_id": "swarm-safe",
            "objective": "Inspect project",
            "project_path": str(project),
            "status": "active",
            "created_at": bridge._iso(),
            "updated_at": bridge._iso(),
            "agents": {role: f"agent-{role}" for role in bridge.ROLES},
            "coordinator_provider": writer_provider,
            "writer_provider": writer_provider,
            "reviewer_provider": bridge.REVIEWER_PREFERENCE[writer_provider][0],
            "handoffs": [],
            "unavailable_providers": {},
            "tasks": {
                "task-safe": {
                    "task_id": "task-safe", "role": task_role,
                    "description": "Inspect", "priority": "normal",
                    "status": "pending", "progress": 0,
                    "created_at": bridge._iso(), "updated_at": bridge._iso(),
                    "provider_attempts": [],
                }
            },
            "circuit": {
                "state": "closed", "consecutive_dual_failures": 0,
                "threshold": 10, "retry_at": None,
            },
        }
        if task_role in {"coordinator", "executor"}:
            state["teams"]["team-safe"]["tasks"]["task-safe"]["provider"] = writer_provider
        elif task_role == "reviewer":
            state["teams"]["team-safe"]["tasks"]["task-safe"]["provider"] = state["teams"]["team-safe"]["reviewer_provider"]
        bridge._atomic_json(state_dir / "teams.json", state)

    def test_create_team_registers_exact_four_roles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            calls = []

            def fake(tool, params, timeout=45):
                calls.append((tool, params))
                if tool == "swarm_init":
                    return {"success": True, "swarmId": "swarm-safe"}
                if tool == "agent_spawn":
                    return {"success": True, "agentId": params["agentId"]}
                raise AssertionError(tool)

            with self.runtime(root), patch.object(bridge, "_ruflo_exec", side_effect=fake):
                result = bridge.create_ruflo_team("Complex task", str(project), "codex")
            self.assertEqual(set(result["roles"]), bridge.ROLES)
            self.assertEqual(result["coordinator_provider"], "codex")
            self.assertEqual(result["writer_provider"], "codex")
            self.assertEqual(result["reviewer_provider"], "claude")
            self.assertTrue(result["provider_separation"])
            self.assertEqual([name for name, _ in calls].count("agent_spawn"), 4)
            self.assertFalse(calls[0][1]["config"]["autopilot"])

    def test_only_review_is_assigned_a_provider(self):
        # The session provider coordinates and writes; the reasoning provider that
        # did not write the code reviews it. Nothing else is delegated: investigation
        # through CAO reads a redacted snapshot without tools, history or context.
        self.assertEqual(bridge._writer_and_reviewer("claude"), ("claude", "codex"))
        self.assertEqual(bridge._writer_and_reviewer("codex"), ("codex", "claude"))
        self.assertEqual(bridge._writer_and_reviewer("antigravity"), ("antigravity", "claude"))
        self.assertEqual(bridge.READONLY_ROLES, {"reviewer"})
        self.assertFalse(hasattr(bridge, "_investigator_candidates"))
        for writer in bridge.PROVIDERS:
            reviewer = bridge._reviewer_candidates(writer)
            # The author never leads and always closes the list: it is the last
            # resort, and reaching it is what marks the review degraded.
            self.assertNotEqual(reviewer[0], writer)
            self.assertEqual(reviewer[-1], writer)
            self.assertEqual(sorted(reviewer), sorted(bridge.PROVIDERS))

    def test_investigation_is_refused_and_says_where_it_belongs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            with self.runtime(root):
                self.seed_team(root, project, task_role="investigator")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post"
                ) as cao:
                    with self.assertRaisesRegex(bridge.BridgeError, "Investigation belongs to the coordinator"):
                        bridge.run_ruflo_readonly_task("team-safe", "task-safe", "Inspect", [])
                    cao.assert_not_called()

    def test_the_coordinator_closes_its_own_investigation(self):
        """No longer a CAO role, so it completes like coordinator and executor work."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            with self.runtime(root):
                self.seed_team(root, project, task_role="investigator")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}):
                    result = bridge.update_ruflo_task("team-safe", "task-safe", "completed")
            self.assertEqual(result["status"], "completed")

    def test_coordinator_and_writer_must_match_session_provider(self):
        team = {
            "coordinator_provider": "antigravity",
            "writer_provider": "codex",
            "reviewer_provider": "antigravity",
        }
        with self.assertRaisesRegex(bridge.BridgeError, "Coordinator and writer"):
            bridge._team_provider_separation(team)

    def test_context_snapshot_redacts_and_rejects_escape_or_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "safe.py").write_text("token=abcdefghijk and user@example.com", encoding="utf-8")
            (project / "50_Sources").mkdir()
            (project / "50_Sources/private.md").write_text("private", encoding="utf-8")
            with self.runtime(root):
                workspace = bridge._stage_context("team-safe", "task-safe", project, ["safe.py"])
                text = (workspace / "safe.py").read_text()
                self.assertIn("[SECRET REDACTED]", text)
                self.assertIn("[EMAIL REDACTED]", text)
                with self.assertRaises(bridge.BridgeError):
                    bridge._stage_context("team-safe", "task-safe", project, ["../outside.py"])
                with self.assertRaises(bridge.BridgeError):
                    bridge._stage_context("team-safe", "task-safe", project, ["50_Sources/private.md"])

    def test_executor_cannot_run_through_cao(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            with self.runtime(root):
                self.seed_team(root, project, task_role="executor")
                with patch.object(bridge, "_cao_post") as cao:
                    with self.assertRaisesRegex(bridge.BridgeError, "Only a reviewer task runs through CAO"):
                        bridge.run_ruflo_readonly_task(
                            "team-safe", "task-safe", "Change code", ["code.py"]
                        )
                cao.assert_not_called()

    def test_successful_readonly_run_falls_back_and_closes_circuit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("print('ok')", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project)
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", side_effect=[bridge.BridgeError("quota"), {"status": "completed", "last_message": "code.py:1 is fine\nVERDICT: PASS"}]
                ):
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Inspect", ["code.py"]
                    )
                self.assertEqual(result["provider"], "antigravity")
                self.assertEqual(result["status"], "completed")
                state = json.loads((root / "state/teams.json").read_text())
                task = state["teams"]["team-safe"]["tasks"]["task-safe"]
                self.assertEqual(state["teams"]["team-safe"]["circuit"]["state"], "closed")
                self.assertIsNone(task["current_provider"])
                self.assertEqual([item["status"] for item in task["provider_attempts"]], ["unavailable", "completed"])
                self.assertTrue(all(item.get("started_at") for item in task["provider_attempts"]))
                self.assertTrue(all(item.get("ended_at") for item in task["provider_attempts"]))
                self.assertEqual(task["provider_attempts"][1]["profile"], "ruflo_antigravity_readonly_worker")

    def test_reviewer_falls_back_to_writer_and_marks_degraded_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("print('ok')", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="reviewer", writer_provider="codex")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", side_effect=[
                        bridge.BridgeError("quota"),
                        bridge.BridgeError("quota"),
                        {"status": "completed", "last_message": "code.py:1 reviewed in degraded mode\nVERDICT: PASS"},
                    ]
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Review", ["code.py"]
                    )
                self.assertEqual(cao.call_count, 3)
                self.assertEqual([call.args[0]["provider"] for call in cao.call_args_list], ["claude_code", "antigravity_cli", "codex"])
                self.assertEqual(result["provider"], "codex")
                state = json.loads((root / "state/teams.json").read_text())
                team = state["teams"]["team-safe"]
                task = team["tasks"]["task-safe"]
                self.assertFalse(task["review_independent"])
                self.assertEqual(task["review_mode"], "same_provider_fallback")
                self.assertTrue(team["degraded_failover"])

    def test_echoed_prompt_without_verdict_falls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("pass", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project)
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", side_effect=[
                        {"status": "completed", "last_message": "Return a concise report."},
                        {"status": "completed", "last_message": "No blocker.\nVERDICT: PASS"},
                    ],
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Inspect", ["code.py"]
                    )
                self.assertEqual(cao.call_count, 2)
                self.assertEqual(result["provider"], "antigravity")
                self.assertEqual(result["worker_verdict"], "PASS")
                self.assertIn("missing VERDICT", result["provider_attempts"][0]["error"])

    def test_valid_report_may_discuss_quota_errors(self):
        result = {
            "status": "completed",
            "last_message": "The 429 quota path is covered.\nVERDICT: PASS",
        }
        self.assertIsNone(bridge._provider_error(result))
        self.assertEqual(bridge._worker_verdict(result), "PASS")

    def test_tenth_failure_cycle_pauses_without_a_further_call(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("pass", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project)
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", side_effect=bridge.BridgeError("quota")
                ) as cao:
                    for _ in range(10):
                        with self.assertRaises(bridge.BridgeError):
                            bridge.run_ruflo_readonly_task("team-safe", "task-safe", "Inspect", ["code.py"])
                    self.assertEqual(cao.call_count, 30)
                    with self.assertRaisesRegex(bridge.BridgeError, "PAUSED_QUOTA"):
                        bridge.run_ruflo_readonly_task("team-safe", "task-safe", "Inspect", ["code.py"])
                    self.assertEqual(cao.call_count, 30)

    def test_verified_memory_requires_completed_reviewer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("pass", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="executor")
                state = json.loads((root / "state/teams.json").read_text())
                state["teams"]["team-safe"]["tasks"]["task-safe"]["status"] = "completed"
                state["teams"]["team-safe"]["tasks"]["review-safe"] = {
                    "task_id": "review-safe", "role": "reviewer", "description": "Review",
                    "status": "pending", "progress": 0, "provider": "antigravity", "provider_attempts": [],
                }
                bridge._atomic_json(root / "state/teams.json", state)
                with patch.object(bridge, "_ruflo_exec") as ruflo:
                    with self.assertRaisesRegex(bridge.BridgeError, "completed reviewer"):
                        bridge.record_ruflo_verified_outcome(
                            "team-safe", "task-safe", "review-safe", "Done", ["code.py"]
                        )
                ruflo.assert_not_called()

    def test_memory_records_same_provider_review_as_degraded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("pass", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="executor", writer_provider="codex")
                state = json.loads((root / "state/teams.json").read_text())
                state["teams"]["team-safe"]["tasks"]["task-safe"]["status"] = "completed"
                state["teams"]["team-safe"]["tasks"]["review-safe"] = {
                    "task_id": "review-safe", "role": "reviewer", "description": "Review",
                    "status": "completed", "progress": 100, "provider": "codex",
                    "worker_verdict": "PASS",
                    "provider_attempts": [{"provider": "codex", "status": "completed"}],
                }
                bridge._atomic_json(root / "state/teams.json", state)
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}) as ruflo:
                    result = bridge.record_ruflo_verified_outcome(
                        "team-safe", "task-safe", "review-safe", "Done", ["code.py"]
                    )
                self.assertTrue(result["stored"])
                self.assertFalse(result["review_independent"])
                self.assertEqual(result["verification_mode"], "same_provider_fallback")
                request = ruflo.call_args.args[1]
                self.assertTrue(request["key"].startswith("reviewed-degraded:"))
                self.assertIn("degraded-provider-failover", request["tags"])

    def test_verified_memory_accepts_completed_independent_reviewer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("pass", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="executor", writer_provider="codex")
                state = json.loads((root / "state/teams.json").read_text())
                state["teams"]["team-safe"]["tasks"]["task-safe"]["status"] = "completed"
                state["teams"]["team-safe"]["tasks"]["review-safe"] = {
                    "task_id": "review-safe", "role": "reviewer", "description": "Review",
                    "status": "completed", "progress": 100, "provider": "antigravity",
                    "worker_verdict": "PASS",
                    "provider_attempts": [{"provider": "antigravity", "status": "completed"}],
                }
                bridge._atomic_json(root / "state/teams.json", state)
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}) as ruflo:
                    result = bridge.record_ruflo_verified_outcome(
                        "team-safe", "task-safe", "review-safe", "Done", ["code.py"]
                    )
                self.assertTrue(result["stored"])
                payload = ruflo.call_args.args[1]["value"]
                self.assertEqual(payload["writer_provider"], "codex")
                self.assertEqual(payload["reviewer_provider"], "antigravity")

    def test_verified_memory_rejects_blocked_reviewer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("pass", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="executor", writer_provider="codex")
                state = json.loads((root / "state/teams.json").read_text())
                state["teams"]["team-safe"]["tasks"]["task-safe"]["status"] = "completed"
                state["teams"]["team-safe"]["tasks"]["review-safe"] = {
                    "task_id": "review-safe", "role": "reviewer", "description": "Review",
                    "status": "completed", "progress": 100, "provider": "antigravity",
                    "worker_verdict": "BLOCKED",
                    "provider_attempts": [{"provider": "antigravity", "status": "completed"}],
                }
                bridge._atomic_json(root / "state/teams.json", state)
                with patch.object(bridge, "_ruflo_exec") as ruflo:
                    with self.assertRaisesRegex(bridge.BridgeError, "VERDICT: PASS"):
                        bridge.record_ruflo_verified_outcome(
                            "team-safe", "task-safe", "review-safe", "Done", ["code.py"]
                        )
                ruflo.assert_not_called()

    def test_list_and_takeover_transfer_open_team_to_other_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            with self.runtime(root):
                self.seed_team(root, project, task_role="executor", writer_provider="codex")
                listing = bridge.list_ruflo_teams()
                self.assertEqual(listing["teams"][0]["writer_provider"], "codex")
                self.assertEqual(listing["teams"][0]["open_tasks"], 1)

                result = bridge.take_over_ruflo_team("team-safe", "antigravity", "Codex quota exhausted")
                self.assertEqual(result["coordinator_provider"], "antigravity")
                self.assertEqual(result["writer_provider"], "antigravity")
                self.assertEqual(result["reviewer_provider"], "claude")
                self.assertEqual(result["handoff"]["from_provider"], "codex")
                self.assertIn("codex", result["unavailable_providers"])
                state = json.loads((root / "state/teams.json").read_text())
                team = state["teams"]["team-safe"]
                self.assertEqual(team["tasks"]["task-safe"]["provider"], "antigravity")
                self.assertIn("codex", team["unavailable_providers"])
                self.assertTrue(team["degraded_failover"])

                repeated = bridge.take_over_ruflo_team("team-safe", "antigravity")
                self.assertTrue(repeated["already_owner"])

    def test_takeover_rejects_non_quota_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            with self.runtime(root):
                self.seed_team(root, project)
                with self.assertRaisesRegex(bridge.BridgeError, "quota exhaustion"):
                    bridge.take_over_ruflo_team("team-safe", "antigravity", "Codex temporarily unavailable")

    def test_stopped_team_cannot_be_taken_over(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            with self.runtime(root):
                self.seed_team(root, project)
                state = json.loads((root / "state/teams.json").read_text())
                state["teams"]["team-safe"]["status"] = "stopped"
                bridge._atomic_json(root / "state/teams.json", state)
                with self.assertRaisesRegex(bridge.BridgeError, "active team"):
                    bridge.take_over_ruflo_team("team-safe", "antigravity")

    def test_review_after_handoff_is_compared_with_actual_code_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("pass", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="executor", writer_provider="codex")
                state = json.loads((root / "state/teams.json").read_text())
                state["teams"]["team-safe"]["tasks"]["task-safe"]["status"] = "completed"
                bridge._atomic_json(root / "state/teams.json", state)
                bridge.take_over_ruflo_team("team-safe", "antigravity", "Codex quota exhausted")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True, "taskId": "review-safe"}):
                    reviewer = bridge.create_ruflo_task("team-safe", "reviewer", "Review prior work")
                self.assertEqual(reviewer["reviewed_writer_provider"], "codex")
                self.assertEqual(reviewer["provider"], "claude")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", return_value={"status": "completed", "last_message": "Reviewed\nVERDICT: PASS"}
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "review-safe", "Review", ["code.py"]
                    )
                self.assertTrue(result["review_independent"])
                self.assertEqual(result["review_mode"], "independent")
                self.assertEqual(cao.call_count, 1)
                self.assertEqual(cao.call_args.args[0]["provider"], "claude_code")

    def test_handoff_never_calls_exhausted_provider_for_new_work_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("pass", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="executor", writer_provider="codex")
                bridge.take_over_ruflo_team("team-safe", "antigravity", "Codex quota exhausted")
                state = json.loads((root / "state/teams.json").read_text())
                state["teams"]["team-safe"]["tasks"]["task-safe"]["status"] = "completed"
                bridge._atomic_json(root / "state/teams.json", state)
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True, "taskId": "review-safe"}):
                    reviewer = bridge.create_ruflo_task("team-safe", "reviewer", "Review new work")
                # Codex is excluded, but Claude still gives an independent review.
                self.assertEqual(reviewer["reviewed_writer_provider"], "antigravity")
                self.assertEqual(reviewer["preferred_provider"], "claude")
                self.assertEqual(reviewer["provider"], "claude")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", return_value={"status": "completed", "last_message": "Reviewed\nVERDICT: PASS"}
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "review-safe", "Review", ["code.py"]
                    )
                self.assertEqual(cao.call_count, 1)
                self.assertEqual(cao.call_args.args[0]["provider"], "claude_code")
                self.assertTrue(result["review_independent"])
                self.assertEqual(result["review_mode"], "independent")

    def test_a_claude_session_reviews_with_codex_and_investigates_by_itself(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("pass", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="executor", writer_provider="claude")
                state = json.loads((root / "state/teams.json").read_text())
                state["teams"]["team-safe"]["tasks"]["task-safe"]["status"] = "completed"
                bridge._atomic_json(root / "state/teams.json", state)
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True, "taskId": "review-safe"}):
                    reviewer = bridge.create_ruflo_task("team-safe", "reviewer", "Review the change")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True, "taskId": "inspect-safe"}):
                    investigator = bridge.create_ruflo_task("team-safe", "investigator", "Inspect the change")
                self.assertEqual(reviewer["provider"], "codex")
                # Investigation is the coordinator's own work: no provider is chosen
                # for it, and none is spent on it.
                self.assertNotIn("provider", investigator)
                self.assertNotIn("eligible_providers", investigator)
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", return_value={"status": "completed", "last_message": "Reviewed\nVERDICT: PASS"}
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "review-safe", "Review", ["code.py"]
                    )
                self.assertEqual(cao.call_args.args[0]["provider"], "codex")
                self.assertEqual(cao.call_count, 1)
                self.assertTrue(result["review_independent"])

    def test_review_degrades_only_when_every_independent_provider_is_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("pass", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="executor", writer_provider="antigravity")
                bridge.take_over_ruflo_team("team-safe", "codex", "Antigravity quota exhausted")
                bridge.take_over_ruflo_team("team-safe", "claude", "Codex quota exhausted")
                state = json.loads((root / "state/teams.json").read_text())
                state["teams"]["team-safe"]["tasks"]["task-safe"]["status"] = "completed"
                state["teams"]["team-safe"]["tasks"]["task-safe"]["provider"] = "claude"
                bridge._atomic_json(root / "state/teams.json", state)
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True, "taskId": "review-safe"}):
                    reviewer = bridge.create_ruflo_task("team-safe", "reviewer", "Review new work")
                self.assertEqual(reviewer["provider"], "claude")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", return_value={"status": "completed", "last_message": "Reviewed\nVERDICT: PASS"}
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "review-safe", "Review", ["code.py"]
                    )
                self.assertEqual(cao.call_count, 1)
                self.assertEqual(cao.call_args.args[0]["provider"], "claude_code")
                self.assertFalse(result["review_independent"])
                self.assertEqual(result["review_mode"], "same_provider_fallback")

    def test_handoff_excludes_the_exhausted_provider_from_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("pass", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="reviewer", writer_provider="codex")
                bridge.take_over_ruflo_team("team-safe", "antigravity", "Codex quota exhausted")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", return_value={"status": "completed", "last_message": "Reviewed\nVERDICT: PASS"}
                ) as cao:
                    # The exhausted provider is skipped, never probed again.
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Review", ["code.py"]
                    )
                self.assertEqual(result["provider"], "claude")
                self.assertEqual(cao.call_count, 1)
                self.assertEqual(cao.call_args.args[0]["provider"], "claude_code")

    def report_path(self, root: Path) -> Path:
        return root / "runs" / "team-safe" / "task-safe" / bridge.REPORT_NAME

    def test_report_delivered_after_the_pane_looked_finished_is_still_the_answer(self):
        """The failure this guards: CAO ends a step at the first pane read that
        looks complete, so a worker that announces itself before working is cut
        off with only its preamble captured. The worker keeps running and
        delivers out of band, and that delivery is the answer."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("print('ok')", encoding="utf-8")
            report = self.report_path(root)

            def cao(body, timeout=660):
                # The pane holds a preamble and a spinner, never a verdict.
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(
                    json.dumps({"verdict": "BLOCKED", "report": "code.py:1 leaks a handle"}),
                    encoding="utf-8",
                )
                return {
                    "terminal_id": "term-1",
                    "status": "completed",
                    "last_message": "\u2022 Reading code.py\n\u2022 Working (5s \u2022 esc to interrupt)",
                }

            with self.runtime(root):
                self.seed_team(root, project, task_role="reviewer", writer_provider="claude")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", side_effect=cao
                ), patch.object(bridge, "_release_terminal") as release:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Review", ["code.py"]
                    )
            self.assertEqual(result["provider"], "codex")
            self.assertEqual(result["worker_verdict"], "BLOCKED")
            self.assertEqual(result["result"], "code.py:1 leaks a handle")
            self.assertEqual(result["provider_attempts"][0]["delivery"], "report_channel")
            self.assertFalse(result["fallback_used"])
            release.assert_called_once_with("term-1")

    def test_step_never_tears_down_its_own_terminal(self):
        """Teardown belongs to the bridge: CAO's would fire while the worker is
        still writing its report."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("print('ok')", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="reviewer", writer_provider="claude")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", return_value={"status": "completed", "last_message": "ok\nVERDICT: PASS"}
                ) as cao, patch.object(bridge, "_release_terminal"):
                    bridge.run_ruflo_readonly_task("team-safe", "task-safe", "Review", ["code.py"])
            self.assertFalse(cao.call_args.args[0]["teardown"])

    def test_terminal_kept_alive_by_the_bridge_is_released_when_the_worker_fails(self):
        """A step that fails still names its terminal; nothing else will reap it."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("print('ok')", encoding="utf-8")
            failure = bridge.BridgeError("CAO HTTP 504: ran long")
            failure.terminal_id = "term-stuck"
            with self.runtime(root):
                self.seed_team(root, project, task_role="reviewer", writer_provider="claude")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", side_effect=[failure, {"status": "completed", "last_message": "ok\nVERDICT: PASS"}]
                ), patch.object(bridge, "_release_terminal") as release:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Review", ["code.py"]
                    )
            self.assertEqual(result["provider"], "antigravity")
            release.assert_called_once_with("term-stuck")

    def test_failed_step_body_yields_the_terminal_it_left_running(self):
        detail = json.dumps({"detail": {"message": "ran long", "kind": "timeout", "terminal_id": "term-9"}})
        self.assertEqual(bridge._terminal_id_from_detail(detail), "term-9")
        self.assertIsNone(bridge._terminal_id_from_detail("not json"))
        self.assertIsNone(bridge._terminal_id_from_detail(json.dumps({"detail": "plain"})))

    def test_codex_worker_is_not_told_it_holds_tools_it_does_not_have(self):
        """CAO translates the tool vocabulary only for providers it maps; codex
        is not one, so its list reaches the worker verbatim. Naming fs_read and
        fs_list there made codex answer that it could not read the snapshot."""
        codex_tools = bridge.PROVIDERS["codex"]["tools"]
        # Named twice wrong already: CAO's `fs_read`/`fs_list`, then `shell`,
        # which is not a codex tool either. `exec_command` is how codex reads.
        self.assertNotIn("fs_read", codex_tools)
        self.assertNotIn("fs_list", codex_tools)
        self.assertNotIn("shell", codex_tools)
        self.assertIn("exec_command", codex_tools)
        for name, provider in bridge.PROVIDERS.items():
            self.assertTrue(provider["tools"], name)


    def test_status_describes_tasks_instead_of_resending_their_reports(self):
        """A status check is a lookup, not a re-read: the coordinator already saw
        the report when the task completed, and can get it back from the task's
        own cache without a provider. Returning tasks whole re-sent 45k
        characters of finished reports on every check."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            with self.runtime(root):
                self.seed_team(root, project, task_role="reviewer", writer_provider="claude")
                state = json.loads((root / "state/teams.json").read_text())
                task = state["teams"]["team-safe"]["tasks"]["task-safe"]
                task.update({
                    "status": "completed", "provider": "codex", "result": "R" * 9000,
                    "worker_verdict": "BLOCKED", "review_mode": "independent",
                    "review_independent": True, "truncated": False,
                    "provider_attempts": [
                        {"provider": "antigravity", "status": "unavailable", "error": "quota" * 200},
                        {"provider": "codex", "status": "completed", "delivery": "report_channel"},
                    ],
                })
                bridge._atomic_json(root / "state/teams.json", state)
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}):
                    status = bridge.get_ruflo_team_status("team-safe")
                    cached = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Review", []
                    )
            digest = status["tasks"][0]
            self.assertNotIn("result", digest)
            self.assertNotIn("provider_attempts", digest)
            self.assertLess(len(json.dumps(status["tasks"])), 1000)
            # Everything needed to judge the task survives.
            self.assertEqual(digest["result_chars"], 9000)
            self.assertEqual(digest["worker_verdict"], "BLOCKED")
            self.assertEqual(digest["review_mode"], "independent")
            self.assertEqual(digest["delivery"], "report_channel")
            self.assertEqual(digest["providers_tried"], ["antigravity:unavailable", "codex:completed"])
            # And the report itself is one cached call away, with no provider run.
            self.assertTrue(cached["cached"])
            self.assertEqual(cached["result"], "R" * 9000)

    def test_status_keeps_the_error_a_blocked_task_cannot_be_judged_without(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            with self.runtime(root):
                self.seed_team(root, project)
                state = json.loads((root / "state/teams.json").read_text())
                state["teams"]["team-safe"]["tasks"]["task-safe"].update({
                    "status": "blocked",
                    "provider_attempts": [{"provider": "codex", "status": "unavailable", "error": "CAO HTTP 504"}],
                })
                bridge._atomic_json(root / "state/teams.json", state)
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}):
                    status = bridge.get_ruflo_team_status("team-safe")
            self.assertEqual(status["tasks"][0]["last_error"], "CAO HTTP 504")


    # --- the review arrives as findings the coordinator can act on one by one ---

    def test_a_finished_review_is_not_lost_to_the_word_chosen_for_its_verdict(self):
        """Observed: two well-formed findings were rejected because the worker
        said FAIL instead of BLOCKED, and it did not try again."""
        for word in ("FAIL", "failed", "Rejected", "blocked"):
            self.assertEqual(delivery.VERDICT_SYNONYMS[word.upper()], "BLOCKED")
        for word in ("PASS", "ok", "approved"):
            self.assertEqual(delivery.VERDICT_SYNONYMS[word.upper()], "PASS")

    def test_a_finding_must_say_how_the_defect_is_reached(self):
        complete = {
            "file": "a.ts", "line": 12, "claim": "loses an edit",
            "reachability": "closing a tab during the 120ms window",
            "severity": "high",
        }
        cleaned = delivery._clean_findings([complete], "BLOCKED")
        self.assertEqual(cleaned[0]["id"], "f1")
        self.assertEqual(cleaned[0]["line"], "12")
        for missing in ("reachability", "severity", "claim", "file"):
            with self.assertRaises(ValueError) as caught:
                delivery._clean_findings([{k: v for k, v in complete.items() if k != missing}], "BLOCKED")
            self.assertIn(missing, str(caught.exception))
        with self.assertRaises(ValueError):
            delivery._clean_findings([dict(complete, severity="catastrophic")], "BLOCKED")

    def test_a_review_is_never_discarded_for_having_nothing_to_list(self):
        """A reviewer that could not inspect the snapshot has exactly that to
        report. Rejecting the call lost the whole answer, the same way the strict
        verdict check once did; the emptiness reaches the coordinator instead."""
        self.assertEqual(delivery._clean_findings([], "BLOCKED"), [])
        self.assertEqual(delivery._clean_findings([], "PASS"), [])

    def test_findings_reach_the_coordinator_and_are_counted_in_the_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("print('ok')", encoding="utf-8")
            report = self.report_path(root)
            findings = [
                {"id": "f1", "file": "code.py", "line": "1", "claim": "leaks", "reachability": "normal use", "severity": "high"},
                {"id": "f2", "file": "code.py", "line": "9", "claim": "races", "reachability": "two tabs", "severity": "low"},
            ]

            def cao(body, timeout=660):
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(json.dumps(
                    {"verdict": "BLOCKED", "report": "checked code.py", "findings": findings}
                ), encoding="utf-8")
                return {"terminal_id": "term-1", "status": "completed", "last_message": "\u2022 Reading"}

            with self.runtime(root):
                self.seed_team(root, project, task_role="reviewer", writer_provider="claude")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", side_effect=cao
                ), patch.object(bridge, "_release_terminal"):
                    result = bridge.run_ruflo_readonly_task("team-safe", "task-safe", "Review", ["code.py"])
                    with patch.object(bridge, "_ruflo_exec", return_value={"success": True}):
                        status = bridge.get_ruflo_team_status("team-safe")
            self.assertEqual([item["id"] for item in result["findings"]], ["f1", "f2"])
            self.assertEqual(result["findings"][0]["reachability"], "normal use")
            # The status counts them; it does not reproduce them.
            digest = status["tasks"][0]
            self.assertEqual(digest["findings"], 2)
            self.assertEqual(digest["findings_by_severity"], {"high": 1, "low": 1})
            self.assertNotIn("claim", json.dumps(digest))

    def test_a_delivery_that_lands_before_the_step_ends_is_not_waited_out(self):
        """Measured: a worker delivered at 38s, CAO never recognised its pane as
        finished, and the answer sat unread until the step timed out at 608s."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / bridge.REPORT_NAME
            report.write_text(json.dumps({"verdict": "PASS", "report": "ok", "findings": []}), encoding="utf-8")
            started = time.monotonic()
            with patch.object(bridge, "REPORT_POLL_SECONDS", 0.0):
                # running() stays True: the step is still going, and it does not matter.
                delivered, stalled = bridge._await_worker_report(
                    report, root, lambda: True, started + bridge.WORKER_BUDGET_SECONDS
                )
            self.assertEqual(delivered["verdict"], "PASS")
            self.assertFalse(stalled)
            self.assertLess(time.monotonic() - started, 5)

    def test_a_finished_step_ends_the_wait_at_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(
                bridge._await_worker_report(root / bridge.REPORT_NAME, root, lambda: False, 0.0),
                (None, False),
            )


    # --- silence is not thinking, and a blocked review is still knowledge -----

    def test_a_worker_that_goes_silent_is_given_up_on_before_the_budget(self):
        """The pane is refused as a completion signal, not as a heartbeat: a TUI
        repaints its spinner while it works, so an unchanging pane means nothing
        is happening. Observed: codex accepted a prompt near its weekly limit and
        produced not one token for the whole ten-minute budget."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / bridge.REPORT_NAME
            with patch.object(bridge, "STALL_SECONDS", 0.0), patch.object(
                bridge, "REPORT_POLL_SECONDS", 0.0
            ), patch.object(bridge, "_terminal_for_workspace", return_value="t1"), patch.object(
                bridge, "_terminal_finished", return_value=False
            ), patch.object(bridge, "_terminal_output", return_value="frozen pane"):
                delivered, stalled = bridge._await_worker_report(report, root, lambda: True, time.monotonic() + 60)
            self.assertIsNone(delivered)
            self.assertTrue(stalled)

    def test_a_pane_that_keeps_moving_is_never_called_stalled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / bridge.REPORT_NAME
            frames = iter(["working 1s", "working 2s", "working 3s"])
            with patch.object(bridge, "STALL_SECONDS", 0.0), patch.object(
                bridge, "REPORT_POLL_SECONDS", 0.0
            ), patch.object(bridge, "_terminal_for_workspace", return_value="t1"), patch.object(
                bridge, "_terminal_finished", return_value=False
            ), patch.object(bridge, "_terminal_output", side_effect=lambda _: next(frames, None)):
                delivered, stalled = bridge._await_worker_report(report, root, lambda: True, time.monotonic() + 0.05)
            self.assertIsNone(delivered)
            self.assertFalse(stalled)

    def test_an_unreadable_pane_is_not_mistaken_for_a_silent_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / bridge.REPORT_NAME
            with patch.object(bridge, "STALL_SECONDS", 0.0), patch.object(
                bridge, "REPORT_POLL_SECONDS", 0.0
            ), patch.object(bridge, "_terminal_for_workspace", return_value="t1"), patch.object(
                bridge, "_terminal_finished", return_value=False
            ), patch.object(bridge, "_terminal_output", return_value=None):
                delivered, stalled = bridge._await_worker_report(report, root, lambda: True, time.monotonic() + 0.05)
            self.assertFalse(stalled)

    def seed_completed_review(self, root: Path, project: Path, verdict="BLOCKED", provider="codex"):
        self.seed_team(root, project, task_role="reviewer", writer_provider="claude")
        state = json.loads((root / "state/teams.json").read_text())
        state["teams"]["team-safe"]["tasks"]["task-safe"].update({
            "status": "completed", "provider": provider, "worker_verdict": verdict,
            "reviewed_writer_provider": "claude", "result": "checked code.py",
            "findings": [{"id": "f1", "file": "code.py", "line": "3", "severity": "high",
                          "claim": "loses an edit", "reachability": "closing a tab mid-window"}],
            "provider_attempts": [{"provider": provider, "status": "completed"}],
        })
        bridge._atomic_json(root / "state/teams.json", state)

    def test_a_blocked_review_is_recorded_without_claiming_anything_is_verified(self):
        """Recording only verified outcomes kept the successes and threw away the
        reviews that found something, which is where the knowledge is."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            stored = {}

            def execute(tool, params, timeout=45):
                if tool == "memory_store":
                    stored.update(params)
                return {"success": True}

            with self.runtime(root):
                self.seed_completed_review(root, project)
                with patch.object(bridge, "_ruflo_exec", side_effect=execute):
                    result = bridge.record_ruflo_review_outcome("team-safe", "task-safe")
            self.assertEqual(result["verdict"], "BLOCKED")
            self.assertEqual(result["findings"], 1)
            self.assertTrue(result["review_independent"])
            # Keyed and tagged apart, so a search can never read it as a pass.
            self.assertTrue(stored["key"].startswith("review:"))
            self.assertIn("verdict-blocked", stored["tags"])
            self.assertNotIn("verified", stored["tags"])
            self.assertEqual(stored["value"]["record_kind"], "review_outcome")
            self.assertEqual(stored["value"]["findings"][0]["reachability"], "closing a tab mid-window")

    def test_verified_still_needs_more_than_a_review_record(self):
        """The weaker record must not become a back door into the stronger one."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            with self.runtime(root):
                self.seed_completed_review(root, project, verdict="BLOCKED")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}):
                    with self.assertRaises(bridge.BridgeError):
                        bridge.record_ruflo_verified_outcome(
                            "team-safe", "task-safe", "task-safe", "s", ["code.py"]
                        )

    def test_a_same_provider_review_is_recorded_as_degraded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            stored = {}

            def execute(tool, params, timeout=45):
                if tool == "memory_store":
                    stored.update(params)
                return {"success": True}

            with self.runtime(root):
                self.seed_completed_review(root, project, verdict="PASS", provider="claude")
                with patch.object(bridge, "_ruflo_exec", side_effect=execute):
                    result = bridge.record_ruflo_review_outcome("team-safe", "task-safe")
            self.assertFalse(result["review_independent"])
            self.assertIn("same-provider-fallback", stored["tags"])
            self.assertEqual(stored["value"]["verification_mode"], "same_provider_fallback")


    def test_the_tool_list_matches_the_profile_that_ships_with_it(self):
        """CAO takes whichever list the caller sends, so the two must agree: the
        profile said `exec_command` while this table still said `shell`, and the
        worker believed the table and reported it could not read anything."""
        profile = Path(__file__).resolve().parents[1] / "CAOProfiles" / "ruflo_codex_readonly_worker.md"
        if not profile.is_file():
            self.skipTest("profile not present beside the runtime")
        head = profile.read_text(encoding="utf-8").split("---\n\n")[0]
        declared = re.findall(r'^\s*-\s*"([^"]+)"', head.split("allowedTools:")[1].split("mcpServers:")[0], re.M)
        self.assertEqual(declared, bridge.PROVIDERS["codex"]["tools"])


if __name__ == "__main__":
    unittest.main()
