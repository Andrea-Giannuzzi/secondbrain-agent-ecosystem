import tempfile
import unittest
from pathlib import Path

from secondbrain_access import (
    access_class_for,
    migrate_access_payloads,
    migration_complete,
)


class FakeClient:
    def __init__(self):
        self.calls = []

    def set_payload(self, **kwargs):
        self.calls.append(kwargs)


class SecondBrainAccessTests(unittest.TestCase):
    def test_access_classes(self):
        self.assertEqual(access_class_for("30_Knowledge", {}), "canonical")
        self.assertEqual(access_class_for("50_Sources", {"librarian_disposition": "knowledge"}), "evidence")
        self.assertEqual(access_class_for("50_Sources", {"librarian_disposition": "administrative"}), "restricted")

    def test_migration_reuses_manifest_ids_and_is_resumable(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "30_Knowledge").mkdir()
            (vault / "50_Sources").mkdir()
            (vault / "30_Knowledge/Node.md").write_text("# Node\n")
            (vault / "50_Sources/Evidence.md").write_text(
                '---\nlibrarian_disposition: "knowledge"\n---\n# Evidence\n'
            )
            (vault / "50_Sources/Private.md").write_text(
                '---\nlibrarian_disposition: "sensitive_reference"\n---\n# Private\n'
            )
            manifest = {"version": 1, "files": {
                "30_Knowledge/Node.md": {"folder": "30_Knowledge", "point_ids": ["a"]},
                "50_Sources/Evidence.md": {"folder": "50_Sources", "point_ids": ["b", "c"]},
                "50_Sources/Private.md": {"folder": "50_Sources", "point_ids": ["d"]},
            }}
            checkpoints = []
            client = FakeClient()
            updated = migrate_access_payloads(client, "collection", manifest, vault, lambda value: checkpoints.append(value.copy()))
            self.assertEqual(updated, 3)
            self.assertEqual([call["points"] for call in client.calls], [["a"], ["b", "c"], ["d"]])
            self.assertEqual([call["payload"]["access_class"] for call in client.calls], ["canonical", "evidence", "restricted"])
            self.assertTrue(migration_complete(manifest))
            self.assertEqual(migrate_access_payloads(client, "collection", manifest, vault, lambda value: None), 0)
            self.assertEqual(len(client.calls), 3)

    def test_incomplete_manifest_fails_readiness(self):
        self.assertFalse(migration_complete({"version": 2, "files": {}}))


if __name__ == "__main__":
    unittest.main()

