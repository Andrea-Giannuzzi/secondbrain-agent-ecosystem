"""Real STDIO servers in a synthetic home; these tests make no AI calls."""
import asyncio
import os
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sbe.installation import BUNDLE


class StdioTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(os.environ.get("SBE_TEST_EMBEDDINGS")=="1", "Opt-in model download: SBE_TEST_EMBEDDINGS=1")
    async def test_real_index_and_simultaneous_searches(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d).resolve();vault=home/"vault"
            (vault/"30_Knowledge").mkdir(parents=True);(vault/"50_Sources").mkdir()
            (vault/"30_Knowledge/Search.md").write_text("# Local semantic search\nLocal semantic search finds related concepts using vector embeddings and a local database. No AI provider call is required.")
            (vault/"50_Sources/Lecture.md").write_text("---\nlibrarian_disposition: knowledge\n---\n# Search lecture\nVector embeddings encode concepts for local semantic search. Contact sample@example.org for this synthetic lecture.")
            (vault/"50_Sources/Admin.md").write_text("---\nlibrarian_disposition: administrative\n---\n# Private administrative note\nThis administrative record about semantic search must never appear in agent evidence.")
            env={**os.environ,"HOME":str(home),"SECOND_BRAIN_VAULT":str(vault),"SECOND_BRAIN_SEMANTIC_STATE":str(home/"semantic"),
                 "PYTHONPATH":str(BUNDLE/"Librarian/Runtime"),"PYTHONDONTWRITEBYTECODE":"1"}
            indexed=await asyncio.to_thread(subprocess.run,[sys.executable,str(BUNDLE/"Semantic/Runtime/brain_index.py")],env=env,capture_output=True,text=True,timeout=300)
            self.assertEqual(indexed.returncode,0,"Synthetic index failed; inspect the isolated model/dependency environment")
            params=StdioServerParameters(command=sys.executable,args=[str(BUNDLE/"Semantic/Runtime/secondbrain_mcp.py")],env=env)
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    canonical,evidence=await asyncio.gather(
                        session.call_tool("search_second_brain",{"query":"local semantic search"}),
                        session.call_tool("search_second_brain_evidence",{"query":"local semantic search"}))
                    self.assertFalse(canonical.is_error);self.assertFalse(evidence.is_error)
                    self.assertEqual(canonical.structured_content["results"][0]["path"],"30_Knowledge/Search.md")
                    self.assertEqual(evidence.structured_content["results"][0]["path"],"50_Sources/Lecture.md")
                    self.assertNotIn("Admin.md",str(evidence))
                    self.assertNotIn("sample@example.org",str(evidence))

    async def test_packaged_servers_and_read_boundaries(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d).resolve(); vault=home/"vault"
            (vault/"30_Knowledge").mkdir(parents=True)
            (vault/"30_Knowledge/A.md").write_text("# A\nLocal search. [[30_Knowledge/B]]\nContact sample@example.org")
            (vault/"30_Knowledge/B.md").write_text("# B\nReview roles.")
            outside=home/"secret.md"; outside.write_text("private")
            (vault/"30_Knowledge/Escape.md").symlink_to(outside)
            env={**os.environ,"HOME":str(home),"SECOND_BRAIN_VAULT":str(vault),
                 "SECOND_BRAIN_SEMANTIC_STATE":str(home/"semantic"),"SECOND_BRAIN_RUFLO_ROOT":str(home/"ruflo"),
                 "PYTHONDONTWRITEBYTECODE":"1","PYTHONPATH":str(BUNDLE/"Librarian/Runtime")}
            params=StdioServerParameters(command=sys.executable,args=[str(BUNDLE/"Semantic/Runtime/secondbrain_mcp.py")],env=env)
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    names={tool.name for tool in (await session.list_tools()).tools}
                    self.assertEqual(names,{"search_second_brain","read_second_brain_note","search_second_brain_evidence","related_second_brain_notes"})
                    note=await session.call_tool("read_second_brain_note",{"path":"30_Knowledge/A.md"})
                    self.assertFalse(note.is_error)
                    self.assertNotIn("sample@example.org",str(note))
                    self.assertEqual(note.structured_content["path"],"30_Knowledge/A.md")
                    related=await session.call_tool("related_second_brain_notes",{"path":"30_Knowledge/A.md"})
                    self.assertIn("30_Knowledge/B.md",str(related.structured_content))
                    for path in ("../secret.md","_System/config.md","50_Sources/private.md","30_Knowledge/Escape.md","30_Knowledge/A.pdf"):
                        result=await session.call_tool("read_second_brain_note",{"path":path})
                        self.assertTrue(result.is_error,path)
                    # No index is an explicit error, never an unfiltered search.
                    result=await session.call_tool("search_second_brain",{"query":"local"})
                    self.assertTrue(result.is_error)
            params=StdioServerParameters(command=sys.executable,args=[str(BUNDLE/"Ruflo/Runtime/ruflo_team_mcp.py")],env=env)
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    tools={tool.name:tool for tool in (await session.list_tools()).tools}
                    self.assertEqual(len(tools),10)
                    self.assertIn("writer_provider",tools["create_ruflo_team"].input_schema["required"])
                    result=await session.call_tool("list_ruflo_teams",{})
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.structured_content["teams"],[])


if __name__=="__main__":unittest.main()
