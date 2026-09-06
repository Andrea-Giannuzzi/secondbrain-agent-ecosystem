import json
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from brain_cluster_materializer import materialize_semantics, validate_semantics
from brain_cluster_executor import managed_links, update_source_status, verify_source_snapshot
import brain_cluster_organizer as organizer
from brain_cluster_organizer import anchor_for, begin_provider_cycle, bind_semantic_envelope, local_triage, parse_semantic_response, prompt_for, provider_circuit_status, provider_result_error, record_provider_cycle_failure, record_provider_success, redact_for_provider, run_with_fallback, sensitive_name, source_has_knowledge_link, split_groups
import ruflo_memory_adapter


def context(count=4):
    return {
        "cluster_id": "batch:cluster",
        "cluster_title": "Cluster",
        "sources": [
            {"id": f"s{i}", "source_note": f"50_Sources/S{i}.md", "title": f"S{i}"}
            for i in range(1, count + 1)
        ],
    }


def bundle(count=4):
    return {
        "version": "semantic-1.0",
        "cluster_id": "batch:cluster",
        "concepts": [
            {"id": "c1", "label": "Vector spaces", "existing_knowledge_id": None, "confidence": 0.91},
            {"id": "c2", "label": "Linear maps", "existing_knowledge_id": None, "confidence": 0.88},
        ],
        "source_concepts": [
            {"source_id": f"s{i}", "concept_ids": ["c1" if i % 2 else "c2"]}
            for i in range(1, count + 1)
        ],
        "relations": [
            {"from_concept": "c1", "to_concept": "c2", "relation": "supports", "confidence": 0.9}
        ],
        "area_proposal": {"label": "Linear Algebra", "existing_area_id": None, "concept_ids": ["c1", "c2"], "confidence": 0.9},
    }


class ClusterPipelineTests(unittest.TestCase):
    def test_python_binds_protocol_envelope(self):
        semantic = bundle()
        semantic["version"] = "wrong"
        semantic["cluster_id"] = "provider-invented-id"
        bound = bind_semantic_envelope(semantic, context())
        self.assertEqual(bound["version"], "semantic-1.0")
        self.assertEqual(bound["cluster_id"], "batch:cluster")
        self.assertEqual(bound["concepts"], semantic["concepts"])

    def test_unlinked_audit_uses_managed_knowledge_links(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp).resolve()
            source = vault / "50_Sources" / "S.md"
            source.parent.mkdir(parents=True)
            record = {"source_note": "50_Sources/S.md"}
            with patch.object(organizer, "VAULT", vault):
                source.write_text("# Source\n", encoding="utf-8")
                self.assertFalse(source_has_knowledge_link(record))
                source.write_text("[[30_Knowledge/Node|Node]]\n", encoding="utf-8")
                self.assertTrue(source_has_knowledge_link(record))

    def circuit_paths(self, root):
        return patch.multiple(
            organizer,
            PROVIDER_CIRCUIT=Path(root) / "provider-circuit.json",
            PROVIDER_CIRCUIT_LOCK=Path(root) / "provider-circuit.lock",
        )

    def test_materializer_is_deterministic_and_structural(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp).resolve()
            patches, files = materialize_semantics(bundle(), context(), {"knowledge": [], "areas": []}, vault)
            self.assertEqual(patches["version"], "cluster-2.0")
            self.assertEqual(patches["area_decision"]["action"], "create_moc")
            self.assertEqual(len(files), 3)
            for path, text in files.items():
                self.assertTrue(path.startswith(("30_Knowledge/", "20_Areas/")))
                self.assertNotIn("## Related", text)
                if path.startswith("20_Areas/"):
                    self.assertNotIn("## Sources", text)
            self.assertEqual(len([x for x in patches["ensure_links"] if x["relation"] == "source_for"]), 4)

    def test_area_threshold_rejects_small_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            patches, files = materialize_semantics(bundle(2), context(2), {"knowledge": [], "areas": []}, Path(temp))
            self.assertEqual(patches["area_decision"]["action"], "none")
            self.assertEqual(len(files), 2)

    def test_exact_source_coverage_is_required(self):
        bad = bundle()
        bad["source_concepts"] = bad["source_concepts"][:-1]
        with self.assertRaises(ValueError):
            validate_semantics(bad, context(), {"knowledge": [], "areas": []})

    def test_empty_semantic_skeleton_is_rejected(self):
        bad = bundle()
        bad["concepts"] = []
        bad["source_concepts"] = [
            {"source_id": f"s{i}", "concept_ids": []} for i in range(1, 5)
        ]
        bad["relations"] = []
        bad["area_proposal"] = None
        with self.assertRaisesRegex(ValueError, "at least one reusable concept"):
            validate_semantics(bad, context(), {"knowledge": [], "areas": []})

    def test_semantic_labels_are_bounded(self):
        bad = bundle()
        bad["concepts"][0]["label"] = "x" * 81
        with self.assertRaisesRegex(ValueError, "exceeds 80 characters"):
            validate_semantics(bad, context(), {"knowledge": [], "areas": []})

    def test_existing_knowledge_is_updated_by_links_only(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            existing = vault / "30_Knowledge" / "Vector spaces.md"
            existing.parent.mkdir(parents=True)
            existing.write_text("# Vector spaces\n\nUser prose.\n", encoding="utf-8")
            b = bundle()
            b["concepts"][0]["existing_knowledge_id"] = "k1"
            canonical = {"knowledge": [{"id": "k1", "path": "30_Knowledge/Vector spaces.md"}], "areas": []}
            patches, files = materialize_semantics(b, context(), canonical, vault)
            self.assertNotIn("30_Knowledge/Vector spaces.md", files)
            self.assertTrue(any(x["to"] == "30_Knowledge/Vector spaces.md" for x in patches["ensure_links"]))

    def test_managed_links_preserve_existing_entries(self):
        old = "# Note\n\n<!-- librarian:relationships:start -->\n## Related\n\n- [[30_Knowledge/Old|Old]] — `supports`\n<!-- librarian:relationships:end -->\n"
        new = managed_links(old, ["- [[30_Knowledge/New|New]] — `uses`"])
        self.assertIn("Old", new)
        self.assertIn("New", new)

    def test_source_status_section_is_visible_and_idempotent_shape(self):
        old = "---\nstatus: \"unprocessed\"\nneeds_librarian: true\n---\n\n# S\n\n## Status\n\nAwaiting Librarian processing.\n\n## Original file\n"
        new = update_source_status(old, "knowledge", "mapped", ["30_Knowledge/Concept.md"])
        self.assertIn("[[30_Knowledge/Concept|Concept]]", new)
        self.assertEqual(new.count("## Status"), 1)

    def test_source_snapshot_rejects_changes_after_review(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp).resolve()
            source = vault / "50_Sources" / "S1.md"
            source.parent.mkdir(parents=True)
            source.write_text("before", encoding="utf-8")
            import hashlib
            manifest = {
                "draft_version": "cluster-2.1",
                "source_notes": ["50_Sources/S1.md"],
                "source_sha256": {
                    "50_Sources/S1.md": hashlib.sha256(b"before").hexdigest()
                },
            }
            verify_source_snapshot(manifest, vault)
            source.write_text("after", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "changed after semantic review"):
                verify_source_snapshot(manifest, vault)

    def test_parser_does_not_scan_echoed_prompt_for_json(self):
        with self.assertRaises(ValueError):
            parse_semantic_response(prompt_for() + '{"version":"semantic-1.0"}')

    def test_parser_accepts_terminal_soft_wrap(self):
        value = {"version": "semantic-1.0", "cluster_id": "x"}
        raw = json.dumps(value, ensure_ascii=False, indent=2)
        self.assertEqual(parse_semantic_response(raw.replace("\n", "\n")), value)

    def test_parser_recovers_cao_no_response_transcript(self):
        value = {"version": "semantic-1.0", "cluster_id": "x"}
        transcript = (
            "[NO RESPONSE - agent completed without producing a text response (120 lines in buffer)]\n"
            "shell prompt and tool output\n\x1b[38;5;189m"
            + json.dumps(value)
            + "\x1b[0m\n> next prompt"
        )
        self.assertEqual(parse_semantic_response(transcript), value)

    def test_parser_recovers_full_semantic_object_after_antigravity_bootstrap(self):
        value = bundle()
        transcript = (
            "Available Skills\nSECURITY CONSTRAINTS\n"
            "You are the semantic librarian. Wait for tasks.\n"
            + json.dumps(value, ensure_ascii=False, indent=2)
        )
        self.assertEqual(parse_semantic_response(transcript), value)

    def test_parser_recovers_final_codex_tui_json_block(self):
        value = {"version": "semantic-1.0", "cluster_id": "x", "label": "Linear maps"}
        wrapped = json.dumps(value).replace("Linear maps", "Linear\nmaps")
        transcript = (
            "• Called Read the three bounded JSON files\n"
            "  └ {\\\"untrusted\\\": \\\"tool output\\\"}\n\n"
            + "─" * 40 + "\n\n• " + wrapped + "\n\n" + "─" * 40
        )
        self.assertEqual(parse_semantic_response(transcript), value)

    def test_cao_prompts_are_single_short_physical_lines(self):
        for retry in (False, True):
            prompt = prompt_for(retry)
            self.assertNotIn("\n", prompt)
            self.assertLessEqual(len(prompt), 140)

    def test_antigravity_success_does_not_call_codex(self):
        response = {"status": "completed", "last_message": "{}"}
        with patch.object(organizer, "begin_provider_cycle", return_value=(True, {})):
            with patch.object(organizer, "record_provider_success") as success:
                with patch.object(organizer, "run_step", return_value=response) as run:
                    result, provider, attempts = run_with_fallback(Path("/tmp"), "prompt")
        self.assertIs(result, response)
        self.assertEqual(provider["name"], "antigravity")
        self.assertEqual(run.call_count, 1)
        success.assert_called_once_with("antigravity")
        self.assertEqual(attempts[0]["status"], "started")

    def test_codex_is_fallback_when_antigravity_cannot_start(self):
        response = {"status": "completed", "last_message": "{}"}
        with patch.object(organizer, "begin_provider_cycle", return_value=(True, {})):
            with patch.object(organizer, "record_provider_success") as success:
                with patch.object(organizer, "run_step", side_effect=[RuntimeError("quota"), response]) as run:
                    _, provider, attempts = run_with_fallback(Path("/tmp"), "prompt")
        self.assertEqual(provider["name"], "codex")
        self.assertEqual(run.call_count, 2)
        self.assertEqual([item["status"] for item in attempts], ["unavailable", "started"])
        success.assert_called_once_with("codex")

    def test_every_provider_failure_is_recorded_once_per_cycle(self):
        state = {"state": "closed", "consecutive_dual_failures": 1}
        with patch.object(organizer, "begin_provider_cycle", return_value=(True, {})):
            with patch.object(organizer, "record_provider_cycle_failure", return_value=state) as record:
                with patch.object(organizer, "run_step", side_effect=RuntimeError("quota")) as run:
                    with self.assertRaises(organizer.ProvidersUnavailable) as raised:
                        run_with_fallback(Path("/tmp"), "prompt")
        self.assertEqual(run.call_count, len(organizer.PROVIDER_CHAIN))
        record.assert_called_once()
        self.assertEqual(len(raised.exception.attempts), len(organizer.PROVIDER_CHAIN))

    def test_codex_trust_is_declared_for_the_step_and_withdrawn_after(self):
        response = {"status": "completed", "last_message": "{}"}
        with tempfile.TemporaryDirectory() as temp, self.circuit_paths(temp):
            config = Path(temp) / "config.toml"
            config.write_text('[mcp_servers.keep]\ncommand = "keep"\n', encoding="utf-8")
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            seen = []

            def observe(ws, prompt, profile, provider):
                # Codex must already be trusted while its own step runs.
                seen.append((provider, str(workspace.resolve()) in config.read_text()))
                return response

            with patch.object(organizer, "CODEX_CONFIG", config):
                with patch.object(organizer, "run_step", side_effect=observe):
                    run_with_fallback(workspace, "prompt")
                    run_with_fallback(workspace, "prompt")
            self.assertIn(("codex", True), seen)
            self.assertTrue(all(not trusted for name, trusted in seen if name != "codex"))
            # Nothing this step added may survive it.
            self.assertEqual(config.read_text(), '[mcp_servers.keep]\ncommand = "keep"\n')

    def test_semantic_head_rotates_between_the_three_providers(self):
        response = {"status": "completed", "last_message": "{}"}
        heads = []
        with tempfile.TemporaryDirectory() as temp, self.circuit_paths(temp):
            for _ in range(4):
                with patch.object(organizer, "run_step", return_value=response):
                    _, provider, _ = run_with_fallback(Path("/tmp"), "prompt")
                heads.append(provider["name"])
        # A fresh cycle moves the head on; a success must not reset the cursor.
        self.assertEqual(heads, ["antigravity", "codex", "claude", "antigravity"])

    def test_review_prefers_a_provider_that_did_not_write_the_semantics(self):
        response = {"status": "completed", "last_message": "{}"}
        with tempfile.TemporaryDirectory() as temp, self.circuit_paths(temp):
            with patch.object(organizer, "run_step", return_value=response) as run:
                _, author, _ = run_with_fallback(Path("/tmp"), "prompt")
                _, reviewer, _ = run_with_fallback(Path("/tmp"), "prompt", reviewing=True, author=author["name"])
        self.assertNotEqual(reviewer["name"], author["name"])
        self.assertEqual(run.call_args.args[2], reviewer["review_profile"])

    def test_review_falls_back_to_the_author_only_as_a_last_resort(self):
        response = {"status": "completed", "last_message": "{}"}
        with tempfile.TemporaryDirectory() as temp, self.circuit_paths(temp):
            with patch.object(organizer, "run_step", side_effect=[RuntimeError("quota"), RuntimeError("quota"), response]):
                _, reviewer, attempts = run_with_fallback(
                    Path("/tmp"), "prompt", reviewing=True, author="antigravity"
                )
        self.assertEqual(reviewer["name"], "antigravity")
        self.assertEqual([item["provider"] for item in attempts][-1], "antigravity")

    def test_rotation_cursor_survives_success_and_manual_reset(self):
        with tempfile.TemporaryDirectory() as temp, self.circuit_paths(temp):
            organizer.begin_provider_cycle()
            organizer.begin_provider_cycle()
            self.assertEqual(organizer.read_provider_circuit()["rotation_head"], "codex")
            organizer.record_provider_success("codex")
            self.assertEqual(organizer.read_provider_circuit()["rotation_head"], "codex")
            organizer.reset_provider_circuit()
            self.assertEqual(organizer.read_provider_circuit()["rotation_head"], "codex")
            # A review cycle must not consume a turn of the rotation.
            organizer.begin_provider_cycle(advance=False)
            self.assertEqual(organizer.read_provider_circuit()["rotation_head"], "codex")

    def test_dual_failure_opens_circuit_on_tenth_cycle(self):
        failure = [
            {"provider": "antigravity", "profile": "a", "status": "unavailable", "error": "quota"},
            {"provider": "codex", "profile": "c", "status": "unavailable", "error": "quota"},
        ]
        base = dt.datetime(2026, 9, 3, 12, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as temp, self.circuit_paths(temp):
            for index in range(9):
                state = record_provider_cycle_failure(failure, base + dt.timedelta(minutes=index))
                self.assertEqual(state["state"], "closed")
            state = record_provider_cycle_failure(failure, base + dt.timedelta(minutes=9))
            self.assertEqual(state["consecutive_dual_failures"], 10)
            self.assertEqual(state["state"], "open")
            self.assertIsNotNone(state["next_probe_at"])

    def test_open_circuit_waits_then_allows_one_probe(self):
        failure = [{"provider": "both", "status": "unavailable", "error": "quota"}]
        base = dt.datetime(2026, 9, 3, 12, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as temp, self.circuit_paths(temp):
            for _ in range(10):
                record_provider_cycle_failure(failure, base)
            allowed, _ = begin_provider_cycle(base + dt.timedelta(minutes=59))
            self.assertFalse(allowed)
            allowed, state = begin_provider_cycle(base + dt.timedelta(hours=1, seconds=1))
            self.assertTrue(allowed)
            self.assertEqual(state["state"], "half_open")
            allowed, _ = begin_provider_cycle(base + dt.timedelta(hours=1, minutes=1))
            self.assertFalse(allowed)

    def test_provider_success_resets_open_circuit(self):
        base = dt.datetime(2026, 9, 3, 12, 0, tzinfo=dt.timezone.utc)
        failure = [{"provider": "both", "status": "unavailable", "error": "quota"}]
        with tempfile.TemporaryDirectory() as temp, self.circuit_paths(temp):
            for _ in range(10):
                record_provider_cycle_failure(failure, base)
            state = record_provider_success("codex", base + dt.timedelta(hours=1))
            self.assertEqual(state["state"], "closed")
            self.assertEqual(state["consecutive_dual_failures"], 0)
            self.assertEqual(state["last_success_provider"], "codex")
            self.assertTrue(provider_circuit_status(base + dt.timedelta(hours=1))["attempt_allowed"])

    def test_open_circuit_makes_no_provider_call(self):
        state = {"state": "open", "next_probe_at": "2099-01-01T00:00:00+00:00"}
        with patch.object(organizer, "begin_provider_cycle", return_value=(False, state)):
            with patch.object(organizer, "run_step") as run:
                with self.assertRaises(organizer.ProviderCircuitOpen):
                    run_with_fallback(Path("/tmp"), "prompt")
        run.assert_not_called()

    def test_invalid_json_still_counts_as_provider_started(self):
        response = {"status": "completed", "last_message": "not json"}
        with patch.object(organizer, "begin_provider_cycle", return_value=(True, {})):
            with patch.object(organizer, "record_provider_success") as success:
                with patch.object(organizer, "record_provider_cycle_failure") as failure:
                    with patch.object(organizer, "run_step", return_value=response):
                        result, _, _ = run_with_fallback(Path("/tmp"), "prompt")
        with self.assertRaises(ValueError):
            parse_semantic_response(result["last_message"])
        success.assert_called_once()
        failure.assert_not_called()

    def test_completed_cao_step_with_http_error_uses_fallback(self):
        hidden_error = {
            "status": "completed",
            "last_message": "■ unexpected status 404 Not Found: http://127.0.0.1:8787/chatgpt/responses",
        }
        success_response = {"status": "completed", "last_message": "{}"}
        with patch.object(organizer, "begin_provider_cycle", return_value=(True, {})):
            with patch.object(organizer, "record_provider_success") as success:
                with patch.object(organizer, "run_step", side_effect=[hidden_error, success_response]):
                    _, provider, attempts = run_with_fallback(Path("/tmp"), "prompt")
        self.assertEqual(provider["name"], "codex")
        self.assertEqual([item["status"] for item in attempts], ["unavailable", "started"])
        success.assert_called_once_with("codex")

    def test_antigravity_individual_quota_message_uses_codex_fallback(self):
        quota_response = {
            "status": "completed",
            "last_message": (
                "Individual quota reached. Please upgrade your subscription "
                "to increase your limits. Resets in 1h30m."
            ),
        }
        success_response = {"status": "completed", "last_message": "{}"}
        with patch.object(organizer, "begin_provider_cycle", return_value=(True, {})):
            with patch.object(organizer, "record_provider_success") as success:
                with patch.object(organizer, "run_step", side_effect=[quota_response, success_response]):
                    _, provider, attempts = run_with_fallback(Path("/tmp"), "prompt")
        self.assertEqual(provider["name"], "codex")
        self.assertEqual([item["status"] for item in attempts], ["unavailable", "started"])
        success.assert_called_once_with("codex")

    def test_semantic_json_with_number_is_not_provider_failure(self):
        message = json.dumps({"version": "semantic-1.0", "label": "HTTP 404"})
        self.assertIsNone(provider_result_error({"status": "completed", "last_message": message}))

    def test_sensitive_personal_names_stay_local(self):
        self.assertTrue(sensitive_name("Documenti utili/Documento Identità.pdf"))
        self.assertTrue(sensitive_name("CV_Mario_Rossi_2024.pdf"))
        self.assertFalse(sensitive_name("Calcolo vettoriale.pdf"))

    def test_provider_context_redacts_personal_identifiers(self):
        raw = "mail mario@example.org IBAN IT60X0542811101000000123456 token=abcdefghijk"
        redacted = redact_for_provider(raw)
        self.assertNotIn("mario@example.org", redacted)
        self.assertNotIn("IT60X0542811101000000123456", redacted)
        self.assertNotIn("abcdefghijk", redacted)

    def test_debug_extraction_stays_local(self):
        record = {
            "title": "estrazione_rossi_debug",
            "metadata": {"original_relative_path": "Codex/Excel/estrazione_rossi_debug.txt"},
            "text": "\n".join(
                f"pagina 1, banda rossa y={i}: Corso | Docente | 12/06/26 | 15/07/26"
                for i in range(10)
            ),
            "words": 120,
            "chars": 900,
        }
        disposition, _ = local_triage(record)
        self.assertEqual(disposition, "technical_reference")

    def test_substantive_debugging_guide_remains_candidate(self):
        record = {
            "title": "Debugging numerical methods",
            "metadata": {"original_relative_path": "Notes/Debugging numerical methods.md"},
            "text": "A rigorous discussion of numerical stability and convergence. " * 30,
            "words": 240,
            "chars": 1800,
        }
        disposition, _ = local_triage(record)
        self.assertEqual(disposition, "candidate")

    def test_single_administrative_word_does_not_hide_substantive_academic_pdf(self):
        record = {
            "title": "Electromagnetism notes",
            "metadata": {"original_relative_path": "University/notes/electromagnetism.pdf"},
            "text": ("Maxwell equations and electromagnetic fields. " * 100) + "payment",
            "words": 601,
            "chars": 4800,
        }
        disposition, _ = local_triage(record)
        self.assertEqual(disposition, "candidate")

    def test_pdf_inside_dump_folder_is_not_automatically_technical(self):
        record = {
            "title": "Laboratory report",
            "metadata": {"original_relative_path": "University/Laboratory dump/report.pdf"},
            "text": "Experimental measurements, uncertainty and analysis. " * 100,
            "words": 500,
            "chars": 5000,
        }
        disposition, _ = local_triage(record)
        self.assertEqual(disposition, "candidate")

    def test_generated_and_repository_control_files_stay_local(self):
        for relative_path in (
            "Codex/MIRA/package-lock.json",
            "Codex/MIRA/src/i18n/locales/it/home.json",
            "Codex/MIRA/.github/ISSUE_TEMPLATE/bug_report.yml",
            "Codex/OOL/output/results.csv",
            "Codex/MIRA/AGENTS.md",
            "Codex/OOL/data/report_testo_estratto.txt",
        ):
            record = {
                "title": Path(relative_path).stem,
                "metadata": {"original_relative_path": relative_path},
                "text": "substantive content " * 100,
                "words": 200,
                "chars": 2000,
            }
            with self.subTest(relative_path=relative_path):
                disposition, _ = local_triage(record)
                self.assertEqual(disposition, "technical_reference")

    def test_repository_container_is_not_a_cluster_anchor(self):
        record = {
            "metadata": {
                "original_relative_path": "Codex/GitHub/pendolo_semplice/README.md"
            }
        }
        self.assertEqual(anchor_for(record, "Codex"), "pendolo_semplice")

    def test_wrapper_readme_joins_the_single_named_child_project(self):
        records = [
            {
                "title": "README",
                "text": "The Strumenti Calcolo project contains symbolic tools.",
                "metadata": {"original_relative_path": "Codex/GitHub/README.md"},
            },
            {
                "title": "README",
                "text": "Symbolic tools.",
                "metadata": {
                    "original_relative_path": "Codex/GitHub/Strumenti Calcolo/README.md"
                },
            },
        ]
        groups = split_groups(records, "Codex")
        self.assertEqual([(name, len(items)) for name, items in groups], [("Strumenti Calcolo", 2)])

    def test_unanchored_sources_do_not_form_a_mixed_cluster(self):
        records = [
            {
                "title": title,
                "text": text,
                "metadata": {"original_relative_path": f"{title}.pdf"},
            }
            for title, text in (("Relativity", "spacetime"), ("Optics", "lenses"))
        ]
        groups = split_groups(records, "")
        self.assertEqual(
            [(name, len(items)) for name, items in groups],
            [("Unsorted · Optics", 1), ("Unsorted · Relativity", 1)],
        )

    def test_ruflo_json_parser_accepts_known_cli_prefix(self):
        raw = '[INFO] Searching: "cluster" (semantic)\n\n{"results": []}\n'
        self.assertEqual(ruflo_memory_adapter._json_from_cli_output(raw), {"results": []})

    def test_ruflo_store_accepts_success_marker_and_disables_daemon(self):
        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": "[INFO] Storing...\n[OK] Data stored successfully\n",
            "stderr": "",
        })()
        with patch.object(ruflo_memory_adapter, "_bin", return_value=Path("/usr/bin/true")):
            with patch.object(ruflo_memory_adapter.subprocess, "run", return_value=completed) as run:
                result = ruflo_memory_adapter._run(
                    ["memory", "store"], success_marker="[OK] Data stored successfully"
                )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(run.call_args.kwargs["env"]["RUFLO_DAEMON_AUTOSTART"], "0")
        self.assertEqual(run.call_args.kwargs["cwd"], str(ruflo_memory_adapter.DEFAULT_ROOT))

    def test_ruflo_semantic_escalation_registers_review_without_ai_execution(self):
        replies = iter([
            {"success": True, "swarmId": "swarm-safe"},
            {"success": True, "agentId": "reviewer-safe"},
            {"success": True, "taskId": "task-safe"},
        ])
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(ruflo_memory_adapter, "LOCK_PATH", Path(temp) / "lock"):
                with patch.object(ruflo_memory_adapter, "_mcp_exec", side_effect=lambda *args: next(replies)) as execute:
                    value = ruflo_memory_adapter.begin_semantic_escalation("batch:cluster", ["invalid"])
        self.assertEqual(value["task_id"], "task-safe")
        self.assertEqual([call.args[0] for call in execute.call_args_list], ["swarm_init", "agent_spawn", "task_create"])

    def test_ruflo_semantic_escalation_closes_without_storing_memory(self):
        escalation = {"swarm_id": "swarm-safe", "task_id": "task-safe"}
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(ruflo_memory_adapter, "LOCK_PATH", Path(temp) / "lock"):
                with patch.object(ruflo_memory_adapter, "_mcp_exec", return_value={"success": True}) as execute:
                    ruflo_memory_adapter.finish_semantic_escalation(escalation, True, "validated")
        self.assertEqual([call.args[0] for call in execute.call_args_list], ["task_complete", "swarm_shutdown"])


if __name__ == "__main__":
    unittest.main()
