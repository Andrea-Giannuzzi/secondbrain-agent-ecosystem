import importlib
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sbe.installation import BUNDLE
for directory in ("Librarian/Runtime","Semantic/Runtime","Ingestor/Runtime","Ruflo/Runtime"):
    sys.path.insert(0,str(BUNDLE/directory))
import secondbrain_ingestor_recursive as ingest
import secondbrain_store as store
import brain_index as index
import ruflo_team_bridge as bridge
import ruflo_memory_adapter as memory
from secondbrain_access import classify_file


class SafetyTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("SBE_TEST_RUFLO_BIN"), "Opt-in native Ruflo installation required")
    def test_native_memory_uses_one_database_across_adapters(self):
        binary=Path(os.environ["SBE_TEST_RUFLO_BIN"]).resolve()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();db=root/"data/memory.db"
            with patch.multiple(bridge,RUFLO_BIN=binary,RUFLO_DB=db,RUNTIME_ROOT=root):
                value=bridge._ruflo_exec("memory_store",{"key":"synthetic-check","namespace":"secondbrain-team","value":"Synthetic reviewed result","upsert":True,"provenance_type":"tool_result"})
                self.assertTrue(value["success"])
                self.assertTrue(db.is_file())
                with patch.object(memory,"_bin",return_value=binary),patch.object(memory,"_db",return_value=db),patch.object(memory,"DEFAULT_ROOT",root):
                    retrieved=memory._run(["mcp","exec","-t","memory_retrieve","-p",'{"key":"synthetic-check","namespace":"secondbrain-team"}'])
                self.assertTrue(retrieved["found"])
                self.assertIn("Synthetic reviewed result",str(retrieved))
            self.assertFalse((root/".swarm/memory.db").exists())

    def test_ignored_input_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            drop=Path(d);ignored=drop/"node_modules";ignored.mkdir();f=ignored/"important.txt";f.write_text("owned by user")
            with patch.object(ingest,"DROP",drop):
                ingest.prune_technical()
                self.assertTrue(ingest.ignored(f))
                self.assertTrue(f.exists())

    def test_relation_inventory_does_not_follow_directory_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            vault=Path(d)/"vault";(vault/"30_Knowledge").mkdir(parents=True)
            outside=Path(d)/"outside";outside.mkdir();(outside/"secret.md").write_text("# private")
            (vault/"30_Knowledge/foreign").symlink_to(outside,target_is_directory=True)
            with patch.object(store,"VAULT",vault):
                paths,_=store._canonical_inventory()
                self.assertFalse(paths)

    def test_title_redaction(self):
        self.assertNotIn("example.org",store._title(Path("note.md"),"# Contact person@example.org"))

    def test_index_rejects_external_notes_and_extracted_paths(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d).resolve(); vault=base/"vault"
            (vault/"30_Knowledge").mkdir(parents=True); (vault/"50_Sources").mkdir()
            outside=base/"outside.md"; outside.write_text("private material outside the vault")
            (vault/"30_Knowledge/Escape.md").symlink_to(outside)
            (vault/"50_Sources/Evidence.md").write_text('---\nlibrarian_disposition: knowledge\nextracted_text: ../outside.md\n---\n# Evidence\n')
            with patch.object(index,"VAULT",vault):
                docs=index.scan_documents()
            self.assertNotIn("30_Knowledge/Escape.md",docs)
            self.assertEqual(len(docs["50_Sources/Evidence.md"]["parts"]),1)
            self.assertEqual(classify_file(vault,"30_Knowledge/Escape.md"),"restricted")
            with patch.object(store,"VAULT",vault):
                self.assertEqual(store._canonical_inventory(),({},{}))

    def test_current_source_disposition_overrides_old_index(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            vault=Path(d).resolve(); (vault/"50_Sources").mkdir()
            note=vault/"50_Sources/Changed.md"
            note.write_text('---\nlibrarian_disposition: administrative\n---\n# Changed\n')
            point=SimpleNamespace(score=1,payload={"source_path":"50_Sources/Changed.md","access_class":"evidence","text":"old sensitive text"})
            @contextmanager
            def locked():
                yield SimpleNamespace(query_points=lambda **kw: SimpleNamespace(points=[point]))
            with patch.object(store,"VAULT",vault),patch.object(store,"_locked_client",locked):
                self.assertEqual(store.search_second_brain_evidence("Changed")["results"],[])
