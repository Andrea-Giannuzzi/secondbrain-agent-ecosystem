import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ruflo_team_bridge as bridge


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

    def seed_team(self, root: Path, project: Path, *, task_role="investigator", writer_provider="codex"):
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
            "reviewer_provider": "antigravity" if writer_provider == "codex" else "codex",
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
            self.assertEqual(result["reviewer_provider"], "antigravity")
            self.assertTrue(result["provider_separation"])
            self.assertEqual([name for name, _ in calls].count("agent_spawn"), 4)
            self.assertFalse(calls[0][1]["config"]["autopilot"])

    def test_writer_provider_always_selects_opposite_reviewer(self):
        self.assertEqual(bridge._writer_and_reviewer("codex"), ("codex", "antigravity"))
        self.assertEqual(bridge._writer_and_reviewer("antigravity"), ("antigravity", "codex"))
        with self.assertRaisesRegex(bridge.BridgeError, "writer_provider"):
            bridge._writer_and_reviewer("other")

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
                    with self.assertRaisesRegex(bridge.BridgeError, "VS Code agent owns writes"):
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
                        "team-safe", "task-safe", "Inspect", ["code.py"], "antigravity"
                    )
                self.assertEqual(result["provider"], "codex")
                self.assertEqual(result["status"], "completed")
                state = json.loads((root / "state/teams.json").read_text())
                task = state["teams"]["team-safe"]["tasks"]["task-safe"]
                self.assertEqual(state["teams"]["team-safe"]["circuit"]["state"], "closed")
                self.assertIsNone(task["current_provider"])
                self.assertEqual([item["status"] for item in task["provider_attempts"]], ["unavailable", "completed"])
                self.assertTrue(all(item.get("started_at") for item in task["provider_attempts"]))
                self.assertTrue(all(item.get("ended_at") for item in task["provider_attempts"]))
                self.assertEqual(task["provider_attempts"][1]["profile"], "ruflo_codex_readonly_worker")

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
                        {"status": "completed", "last_message": "code.py:1 reviewed in degraded mode\nVERDICT: PASS"},
                    ]
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Review", ["code.py"], "codex"
                    )
                self.assertEqual(cao.call_count, 2)
                self.assertEqual([call.args[0]["provider"] for call in cao.call_args_list], ["antigravity_cli", "codex"])
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
                        "team-safe", "task-safe", "Inspect", ["code.py"], "antigravity"
                    )
                self.assertEqual(cao.call_count, 2)
                self.assertEqual(result["provider"], "codex")
                self.assertEqual(result["worker_verdict"], "PASS")
                self.assertIn("missing VERDICT", result["provider_attempts"][0]["error"])

    def test_valid_report_may_discuss_quota_errors(self):
        result = {
            "status": "completed",
            "last_message": "The 429 quota path is covered.\nVERDICT: PASS",
        }
        self.assertIsNone(bridge._provider_error(result))
        self.assertEqual(bridge._worker_verdict(result), "PASS")

    def test_tenth_dual_failure_pauses_without_an_eleventh_call(self):
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
                    self.assertEqual(cao.call_count, 20)
                    with self.assertRaisesRegex(bridge.BridgeError, "PAUSED_QUOTA"):
                        bridge.run_ruflo_readonly_task("team-safe", "task-safe", "Inspect", ["code.py"])
                    self.assertEqual(cao.call_count, 20)

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
                self.assertEqual(result["reviewer_provider"], "codex")
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
                self.assertEqual(reviewer["provider"], "antigravity")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", return_value={"status": "completed", "last_message": "Reviewed\nVERDICT: PASS"}
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "review-safe", "Review", ["code.py"]
                    )
                self.assertTrue(result["review_independent"])
                self.assertEqual(result["review_mode"], "independent")
                self.assertEqual(cao.call_count, 1)
                self.assertEqual(cao.call_args.args[0]["provider"], "antigravity_cli")

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
                self.assertEqual(reviewer["reviewed_writer_provider"], "antigravity")
                self.assertEqual(reviewer["preferred_provider"], "codex")
                self.assertEqual(reviewer["provider"], "antigravity")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", return_value={"status": "completed", "last_message": "Reviewed\nVERDICT: PASS"}
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "review-safe", "Review", ["code.py"]
                    )
                self.assertEqual(cao.call_count, 1)
                self.assertEqual(cao.call_args.args[0]["provider"], "antigravity_cli")
                self.assertFalse(result["review_independent"])
                self.assertEqual(result["review_mode"], "same_provider_fallback")

    def test_handoff_excludes_exhausted_provider_from_investigation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("pass", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="investigator", writer_provider="codex")
                bridge.take_over_ruflo_team("team-safe", "antigravity", "Codex quota exhausted")
                state = json.loads((root / "state/teams.json").read_text())
                self.assertEqual(
                    state["teams"]["team-safe"]["tasks"]["task-safe"]["eligible_providers"],
                    ["antigravity"],
                )
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", return_value={"status": "completed", "last_message": "Inspected\nVERDICT: PASS"}
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Inspect", ["code.py"], "codex"
                    )
                self.assertEqual(result["provider"], "antigravity")
                self.assertEqual(cao.call_count, 1)
                self.assertEqual(cao.call_args.args[0]["provider"], "antigravity_cli")


if __name__ == "__main__":
    unittest.main()
