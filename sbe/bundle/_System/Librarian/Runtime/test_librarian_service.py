import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import secondbrain_librarian_service as service
from secondbrain_librarian_service import ai_invalid_entry_waiting, frontmatter, pending_batches, provider_circuit_waiting, run_batch, run_batches


class LibrarianServiceTests(unittest.TestCase):
    def test_frontmatter_reads_json_strings_and_booleans(self):
        values = frontmatter('---\nimport_batch: "Codex"\nneeds_librarian: true\n---\n')
        self.assertEqual(values["import_batch"], "Codex")
        self.assertIs(values["needs_librarian"], True)

    def test_pending_batches_are_oldest_first_and_unique(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            notes = [
                ("a.md", "B", "2026-09-03T02:00:00+02:00", True),
                ("b.md", "A", "2026-09-03T01:00:00+02:00", True),
                ("c.md", "B", "2026-09-03T03:00:00+02:00", True),
                ("d.md", "C", "2026-09-03T00:00:00+02:00", False),
            ]
            for name, batch, stamp, pending in notes:
                (root / name).write_text(
                    f'---\nimport_batch: "{batch}"\ningested_at: "{stamp}"\n'
                    f'needs_librarian: {str(pending).lower()}\nstatus: "processed"\n---\n',
                    encoding="utf-8",
                )
            self.assertEqual(pending_batches(root), ["A", "B"])

    def test_empty_batch_uses_explicit_unbatched_mode(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with patch("secondbrain_librarian_service.subprocess.run", return_value=completed) as run:
            with patch("secondbrain_librarian_service.log"):
                self.assertEqual(run_batch(""), 0)
        self.assertIn("--unbatched", run.call_args.args[0])

    def test_open_provider_circuit_skips_work_until_probe(self):
        base = dt.datetime(2026, 9, 3, 12, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "provider-circuit.json"
            path.write_text(json.dumps({
                "state": "open",
                "consecutive_dual_failures": 10,
                "next_probe_at": (base + dt.timedelta(hours=1)).isoformat(),
            }), encoding="utf-8")
            with patch.object(service, "PROVIDER_CIRCUIT", path):
                waiting, state = provider_circuit_waiting(base)
                self.assertTrue(waiting)
                self.assertEqual(state["consecutive_dual_failures"], 10)
                waiting, _ = provider_circuit_waiting(base + dt.timedelta(hours=1, seconds=1))
                self.assertFalse(waiting)

    def test_temporary_unavailability_stops_later_batches(self):
        with patch("secondbrain_librarian_service.run_batch", side_effect=[75, 0]) as run:
            failed, paused = run_batches(["A", "B"])
        self.assertEqual(failed, ["A"])
        self.assertTrue(paused)
        self.assertEqual(run.call_count, 1)

    def test_ai_invalid_backoff_expires_or_is_invalidated_by_source_change(self):
        base = dt.datetime(2026, 9, 3, 12, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            note = Path(temp) / "S.md"
            note.write_text("before", encoding="utf-8")
            import hashlib
            entry = {
                "source_sha256": hashlib.sha256(b"before").hexdigest(),
                "next_retry_at": (base + dt.timedelta(hours=1)).isoformat(),
            }
            self.assertTrue(ai_invalid_entry_waiting(note, entry, base))
            self.assertFalse(ai_invalid_entry_waiting(note, entry, base + dt.timedelta(hours=2)))
            note.write_text("after", encoding="utf-8")
            self.assertFalse(ai_invalid_entry_waiting(note, entry, base))


if __name__ == "__main__":
    unittest.main()
