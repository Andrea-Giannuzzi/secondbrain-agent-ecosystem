#!/usr/bin/env python3
"""Shared outbound redaction for Librarian providers and read-only agent access."""
from __future__ import annotations

import re


REDACTIONS = (
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), "[EMAIL REDACTED]"),
    (re.compile(r"\bIT\d{2}[A-Z]\d{10}[A-Z0-9]{12}\b", re.I), "[IBAN REDACTED]"),
    (re.compile(r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b", re.I), "[FISCAL CODE REDACTED]"),
    (
        re.compile(
            r"-----BEGIN [^-]*(?:PRIVATE KEY|CERTIFICATE)-----.*?-----END [^-]+-----",
            re.I | re.S,
        ),
        "[CREDENTIAL REDACTED]",
    ),
    (
        re.compile(
            r"(?i)\b(?:api[_ -]?key|access[_ -]?token|client[_ -]?secret|password|token)"
            r"\s*[:=]\s*[^\s,;]{8,}"
        ),
        "[SECRET REDACTED]",
    ),
)


def redact_sensitive(value: object) -> str:
    """Return text safe for an external model or MCP client."""
    text = str(value or "")
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text

