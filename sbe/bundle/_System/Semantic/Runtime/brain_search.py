#!/usr/bin/env python3
"""Human-facing CLI for the same fail-closed canonical search used by MCP."""
from __future__ import annotations

import json
import sys

from secondbrain_store import search_second_brain


def main() -> int:
    if len(sys.argv) < 2:
        print('Usage: brain-search "your semantic query" [limit]')
        return 2
    try:
        result = search_second_brain(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 8)
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
