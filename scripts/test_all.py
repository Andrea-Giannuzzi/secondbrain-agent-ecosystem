#!/usr/bin/env python3
"""Run the same source tests shipped with the public package."""
import sys
import unittest
from pathlib import Path
from sbe.installation import BUNDLE

directories=[BUNDLE/"Librarian/Runtime",BUNDLE/"Semantic/Runtime",BUNDLE/"Ruflo/Runtime",BUNDLE/"AgentAccess",Path(__file__).resolve().parents[1]/"tests"]
for p in directories: sys.path.insert(0,str(p))
suite=unittest.TestSuite()
for path in directories:
    suite.addTests(unittest.TestLoader().discover(str(path),pattern="test_*.py"))
raise SystemExit(0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1)
