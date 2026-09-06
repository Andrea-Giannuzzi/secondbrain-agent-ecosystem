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

    def test_role_matrix_assigns_reviewer_and_investigator_per_session_provider(self):
        # The session provider coordinates and writes; the other two split the rest.
        self.assertEqual(bridge._writer_and_reviewer("claude"), ("claude", "codex"))
        self.assertEqual(bridge._writer_and_reviewer("codex"), ("codex", "claude"))
        self.assertEqual(bridge._writer_and_reviewer("antigravity"), ("antigravity", "claude"))
        self.assertEqual(bridge._investigator_candidates("claude")[0], "antigravity")
        self.assertEqual(bridge._investigator_candidates("codex")[0], "antigravity")
        self.assertEqual(bridge._investigator_candidates("antigravity")[0], "codex")
        for coordinator in bridge.PROVIDERS:
            reviewer = bridge._reviewer_candidates(coordinator)
            investigator = bridge._investigator_candidates(coordinator)
            self.assertNotEqual(reviewer[0], coordinator)
            self.assertNotEqual(investigator[0], coordinator)
            self.assertNotEqual(reviewer[0], investigator[0])
            # Every provider stays reachable, the writer last and only as fallback.
            self.assertEqual(reviewer[-1], coordinator)
            self.assertEqual(sorted(set(reviewer)), sorted(bridge.PROVIDERS))
            self.assertEqual(sorted(set(investigator)), sorted(bridge.PROVIDERS))
        with self.assertRaisesRegex(bridge.BridgeError, "writer_provider"):
            bridge._writer_and_reviewer("other")

    def test_investigator_preference_may_override_the_matrix_head(self):
        self.assertEqual(
            bridge._investigator_candidates("claude", "codex"), ["codex", "antigravity", "claude"]
        )
        with self.assertRaisesRegex(bridge.BridgeError, "provider_preference"):
            bridge._investigator_candidates("claude", "other")

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
                self.assertEqual(result["provider"], "claude")
                self.assertEqual(result["status"], "completed")
                state = json.loads((root / "state/teams.json").read_text())
                task = state["teams"]["team-safe"]["tasks"]["task-safe"]
                self.assertEqual(state["teams"]["team-safe"]["circuit"]["state"], "closed")
                self.assertIsNone(task["current_provider"])
                self.assertEqual([item["status"] for item in task["provider_attempts"]], ["unavailable", "completed"])
                self.assertTrue(all(item.get("started_at") for item in task["provider_attempts"]))
                self.assertTrue(all(item.get("ended_at") for item in task["provider_attempts"]))
                self.assertEqual(task["provider_attempts"][1]["profile"], "ruflo_claude_readonly_worker")

    def test_delivered_report_is_preferred_over_the_terminal_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("print('ok')", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="reviewer", writer_provider="antigravity")
                runs = root / "runs/team-safe/task-safe"

                def deliver(body, timeout=660):
                    # The worker answers through the tool while the pane is still
                    # rendering, so the capture below stays truncated on purpose.
                    bridge._atomic_json(runs / "worker-report.json", {
                        "version": "worker-report-1.0", "verdict": "BLOCKED",
                        "report": "code.py:1 divides by zero on empty input", "truncated": False,
                    })
                    return {"status": "completed", "last_message": "Reviewed code.py. Findings"}

                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", side_effect=deliver
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Review", ["code.py"]
                    )
                self.assertEqual(cao.call_count, 1)
                self.assertEqual(result["provider"], "claude")
                self.assertEqual(result["worker_verdict"], "BLOCKED")
                self.assertIn("divides by zero", result["result"])
                state = json.loads((root / "state/teams.json").read_text())
                attempt = state["teams"]["team-safe"]["tasks"]["task-safe"]["provider_attempts"][0]
                self.assertEqual(attempt["delivery"], "report_channel")
                # The tool is allowed for this run only, inside the staged snapshot.
                allowed = json.loads((runs / "workspace/.claude/settings.local.json").read_text())
                self.assertEqual(allowed["permissions"]["allow"], [bridge.REPORT_TOOL])

    def test_a_stale_report_is_cleared_before_the_worker_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("print('ok')", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="reviewer", writer_provider="antigravity")
                runs = root / "runs/team-safe/task-safe"
                runs.mkdir(parents=True)
                bridge._atomic_json(runs / "worker-report.json", {
                    "version": "worker-report-1.0", "verdict": "PASS",
                    "report": "stale answer from an earlier run", "truncated": False,
                })
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", return_value={"status": "completed", "last_message": "Reviewed.VERDICT:PASS"}
                ):
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Review", ["code.py"]
                    )
                # The stale file was removed, so the answer came from the capture.
                self.assertNotIn("stale answer", result["result"])
                state = json.loads((root / "state/teams.json").read_text())
                attempt = state["teams"]["team-safe"]["tasks"]["task-safe"]["provider_attempts"][0]
                self.assertEqual(attempt["delivery"], "terminal_capture")

    def test_incomplete_capture_retries_the_same_worker_before_degrading(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("print('ok')", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="reviewer", writer_provider="antigravity")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", side_effect=[
                        # CAO tore the pane down mid-answer: no end marker.
                        {"status": "completed", "last_message": "Reviewed code.py. Findings"},
                        {"status": "completed", "last_message": "code.py:1 is fine\nVERDICT: PASS"},
                    ]
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Review", ["code.py"]
                    )
                # The designated reviewer keeps the role instead of degrading.
                self.assertEqual(cao.call_count, 2)
                self.assertEqual([call.args[0]["provider"] for call in cao.call_args_list],
                                 ["claude_code", "claude_code"])
                self.assertEqual(result["provider"], "claude")
                self.assertTrue(result["review_independent"])
                state = json.loads((root / "state/teams.json").read_text())
                attempts = state["teams"]["team-safe"]["tasks"]["task-safe"]["provider_attempts"]
                self.assertEqual([item["provider"] for item in attempts], ["claude"])
                self.assertEqual(attempts[0]["capture_retries"], 1)

    def test_quota_failure_is_never_retried_on_the_same_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "code.py").write_text("print('ok')", encoding="utf-8")
            with self.runtime(root):
                self.seed_team(root, project, task_role="reviewer", writer_provider="antigravity")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", side_effect=[
                        bridge.BridgeError("quota"),
                        {"status": "completed", "last_message": "code.py:1 is fine\nVERDICT: PASS"},
                    ]
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Review", ["code.py"]
                    )
                self.assertEqual(cao.call_count, 2)
                self.assertEqual([call.args[0]["provider"] for call in cao.call_args_list],
                                 ["claude_code", "codex"])
                self.assertEqual(result["provider"], "codex")

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
                        "team-safe", "task-safe", "Review", ["code.py"], "codex"
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
                        # An echo is retried once on the same worker, then the
                        # role moves down the chain; it is never accepted.
                        {"status": "completed", "last_message": "Return a concise report."},
                        {"status": "completed", "last_message": "Return a concise report."},
                        {"status": "completed", "last_message": "No blocker.\nVERDICT: PASS"},
                    ],
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Inspect", ["code.py"], "antigravity"
                    )
                self.assertEqual(cao.call_count, 3)
                self.assertEqual([call.args[0]["provider"] for call in cao.call_args_list],
                                 ["antigravity_cli", "antigravity_cli", "claude_code"])
                self.assertEqual(result["provider"], "claude")
                self.assertEqual(result["worker_verdict"], "PASS")
                self.assertIn("missing VERDICT", result["provider_attempts"][0]["error"])

    def test_verdict_survives_terminal_capture_but_a_bare_prompt_echo_does_not(self):
        # CAO captures the pane, which collapses the final line into the sentence
        # before it; a complete answer must still be accepted.
        collapsed = {"status": "completed", "last_message": "code.py:9 is a narrow edge case.VERDICT:PASS"}
        self.assertEqual(bridge._worker_verdict(collapsed), "PASS")
        self.assertIsNone(bridge._provider_error(collapsed))
        # Teardown appends its own epilogue after the answer, so the marker is not
        # the end of the capture either.
        epilogue = {"status": "completed", "last_message":
                    "code.py:9 is a narrow edge case.VERDICT:PASS\nResume this session with: claude --resume 810d8dd9"}
        self.assertEqual(bridge._worker_verdict(epilogue), "PASS")
        # The echoed instruction names both verdicts as one pair, so a capture
        # holding only the prompt is still refused, wrapped or collapsed.
        for echoed in (
            "Task: Review code.py. End with exactly one line: VERDICT: PASS or VERDICT: BLOCKED.",
            "Endwithexactlyoneline:VERDICT:PASSorVERDICT:BLOCKED.",
            "End with exactly one line: VERDICT: PASS or VERDICT: BLOCKED. Resume this session with: claude --resume x",
        ):
            with self.assertRaisesRegex(bridge.BridgeError, "missing VERDICT"):
                bridge._worker_verdict({"status": "completed", "last_message": echoed})
        # A real answer that follows the echoed instruction is still read.
        answered = {"status": "completed", "last_message":
                    "End with exactly one line: VERDICT: PASS or VERDICT: BLOCKED. No blocker found.VERDICT: BLOCKED"}
        self.assertEqual(bridge._worker_verdict(answered), "BLOCKED")
        truncated = {"status": "completed", "last_message": "1. average_rate raises on an empty list"}
        with self.assertRaisesRegex(bridge.BridgeError, "missing VERDICT"):
            bridge._worker_verdict(truncated)

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

    def test_claude_session_reviews_with_codex_and_investigates_with_antigravity(self):
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
                self.assertEqual(investigator["eligible_providers"][0], "antigravity")
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", return_value={"status": "completed", "last_message": "Reviewed\nVERDICT: PASS"}
                ) as cao:
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "review-safe", "Review", ["code.py"]
                    )
                self.assertEqual(cao.call_args.args[0]["provider"], "codex")
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
                    ["claude", "antigravity"],
                )
                with patch.object(bridge, "_ruflo_exec", return_value={"success": True}), patch.object(
                    bridge, "_cao_post", return_value={"status": "completed", "last_message": "Inspected\nVERDICT: PASS"}
                ) as cao:
                    # An explicit preference for the exhausted provider is skipped, not probed.
                    result = bridge.run_ruflo_readonly_task(
                        "team-safe", "task-safe", "Inspect", ["code.py"], "codex"
                    )
                self.assertEqual(result["provider"], "claude")
                self.assertEqual(cao.call_count, 1)
                self.assertEqual(cao.call_args.args[0]["provider"], "claude_code")


if __name__ == "__main__":
    unittest.main()
