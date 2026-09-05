import json
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import secondbrain_store as store


READY_MANIFEST = {
    "version": 2,
    "files": {},
    "access_migration": {"schema_version": 1, "complete": True},
}


class FakeClient:
    def __init__(self, points=None, delay=0):
        self.points = points or []
        self.delay = delay
        self.filters = []
        self.active = 0
        self.maximum_active = 0
        self.guard = threading.Lock()

    def collection_exists(self, name):
        return True

    def query_points(self, **kwargs):
        self.filters.append(kwargs["query_filter"])
        with self.guard:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        time.sleep(self.delay)
        with self.guard:
            self.active -= 1
        return SimpleNamespace(points=self.points)

    def close(self):
        pass


class SecondBrainStoreTests(unittest.TestCase):
    def test_search_filters_and_redacts_evidence(self):
        point = SimpleNamespace(score=0.9, payload={
            "source_path": "50_Sources/Lecture.md",
            "title": "Lecture",
            "access_class": "evidence",
            "text": "Contact user@example.com token=abcdefghijklmnop",
        })
        fake = FakeClient([point])

        @contextmanager
        def locked():
            yield fake

        with patch.object(store, "_locked_client", locked), patch.object(store, "classify_file", return_value="evidence"):
            value = store.search_second_brain_evidence("topic", 4)
        self.assertEqual(value["results"][0]["path"], "50_Sources/Lecture.md")
        self.assertIn("[EMAIL REDACTED]", value["results"][0]["snippet"])
        self.assertIn("[SECRET REDACTED]", value["results"][0]["snippet"])
        self.assertNotIn("user@example.com", json.dumps(value))
        condition = fake.filters[0].must[0]
        self.assertEqual(condition.match.value, "evidence")

    def test_search_limit_is_capped_at_ten(self):
        points = [SimpleNamespace(score=1.0 - index / 100, payload={
            "source_path": f"30_Knowledge/{index}.md", "title": str(index),
            "access_class": "canonical", "text": "content",
        }) for index in range(12)]
        fake = FakeClient(points)

        @contextmanager
        def locked():
            yield fake

        with patch.object(store, "_locked_client", locked), patch.object(store, "classify_file", return_value="canonical"):
            value = store.search_second_brain("topic", 100)
        self.assertEqual(len(value["results"]), 10)

    def test_search_discards_wrong_folder_even_if_payload_is_mislabeled(self):
        point = SimpleNamespace(score=1.0, payload={
            "source_path": "50_Sources/Private.md", "title": "Private",
            "access_class": "canonical", "text": "secret",
        })
        fake = FakeClient([point])

        @contextmanager
        def locked():
            yield fake

        with patch.object(store, "_locked_client", locked):
            value = store.search_second_brain("secret")
        self.assertEqual(value["results"], [])

    def test_read_rejects_traversal_source_system_and_external_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / "vault"
            (vault / "30_Knowledge").mkdir(parents=True)
            (vault / "50_Sources").mkdir()
            (vault / "_System").mkdir()
            (vault / "30_Knowledge/Good.md").write_text("# Good\nemail a@b.com\n")
            (vault / "50_Sources/Source.md").write_text("source")
            outside = Path(temp) / "outside.md"
            outside.write_text("outside")
            (vault / "30_Knowledge/Escape.md").symlink_to(outside)
            with patch.object(store, "VAULT", vault.resolve()):
                good = store.read_second_brain_note("30_Knowledge/Good.md")
                self.assertIn("[EMAIL REDACTED]", good["content"])
                for path in ("../outside.md", "50_Sources/Source.md", "_System/x.md", "30_Knowledge/Escape.md"):
                    with self.subTest(path=path), self.assertRaises((ValueError, PermissionError)):
                        store.read_second_brain_note(path)

    def test_read_truncates_canonical_note_at_twenty_thousand_characters(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "30_Knowledge").mkdir()
            (vault / "30_Knowledge/Long.md").write_text("# Long\n" + "x" * 21_000)
            with patch.object(store, "VAULT", vault.resolve()):
                value = store.read_second_brain_note("30_Knowledge/Long.md")
            self.assertEqual(len(value["content"]), 20_000)
            self.assertTrue(value["truncated"])

    def test_related_notes_returns_canonical_wikilinks_and_backlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            for folder in store.CANONICAL_FOLDERS:
                (vault / folder).mkdir()
            (vault / "30_Knowledge/A.md").write_text("# A\n[[30_Knowledge/B|B]]")
            (vault / "30_Knowledge/B.md").write_text("# B")
            (vault / "20_Areas/C.md").write_text("# C\n[[30_Knowledge/A]]")
            with patch.object(store, "VAULT", vault.resolve()):
                value = store.related_second_brain_notes("30_Knowledge/A.md")
            relations = {item["path"]: item["snippet"] for item in value["relations"]}
            self.assertEqual(relations["30_Knowledge/B.md"], "wikilink")
            self.assertEqual(relations["20_Areas/C.md"], "backlink")

    def test_file_and_process_lock_serializes_simultaneous_queries(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            (state / "qdrant").mkdir()
            (state / "manifest.json").write_text(json.dumps(READY_MANIFEST))
            (state / "qdrant.lock").touch()
            fake = FakeClient(delay=0.05)
            with patch.multiple(
                store,
                STATE=state,
                DB_PATH=state / "qdrant",
                MANIFEST_PATH=state / "manifest.json",
                LOCK_PATH=state / "qdrant.lock",
                QdrantClient=lambda path: fake,
            ):
                threads = [threading.Thread(target=store.search_second_brain, args=("query",)) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
            self.assertEqual(fake.maximum_active, 1)

    def test_search_fails_closed_before_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            (state / "qdrant").mkdir()
            (state / "manifest.json").write_text(json.dumps({"version": 1, "files": {}}))
            (state / "qdrant.lock").touch()
            with patch.multiple(
                store,
                STATE=state,
                DB_PATH=state / "qdrant",
                MANIFEST_PATH=state / "manifest.json",
                LOCK_PATH=state / "qdrant.lock",
            ):
                with self.assertRaisesRegex(RuntimeError, "migration is incomplete"):
                    store.search_second_brain("query")


if __name__ == "__main__":
    unittest.main()
