import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from recover_scanned_sources import sample_pages, update_frontmatter


class OCRRecoveryTests(unittest.TestCase):
    def test_short_pdf_uses_every_page_and_long_pdf_is_bounded(self):
        self.assertEqual(sample_pages(3), [1, 2, 3])
        pages = sample_pages(526)
        self.assertLessEqual(len(pages), 16)
        self.assertEqual(pages[:3], [1, 2, 3])
        self.assertEqual(pages[-1], 526)

    def test_extraction_metadata_preserves_existing_frontmatter(self):
        original = '---\nstatus: "organized"\nupdated: "2026-09-02"\n---\n\n# Source\n'
        updated = update_frontmatter(original, {"extraction_method": "tesseract-sampled"})
        self.assertIn('status: "organized"', updated)
        self.assertIn('updated: "2026-09-02"', updated)
        self.assertIn('extraction_method: "tesseract-sampled"', updated)

    def test_recovered_source_can_remove_old_disposition_and_return_to_queue(self):
        original = (
            '---\nstatus: "organized"\nneeds_librarian: false\n'
            'librarian_disposition: "low_information"\n---\n\n# Source\n'
        )
        updated = update_frontmatter(original, {
            "status": "unprocessed",
            "needs_librarian": True,
            "librarian_disposition": None,
        })
        self.assertIn('status: "unprocessed"', updated)
        self.assertIn('needs_librarian: true', updated)
        self.assertNotIn("librarian_disposition:", updated)


if __name__ == "__main__":
    unittest.main()
