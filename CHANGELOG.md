# Changelog

## 0.1.0 — unreleased

- Initial macOS distribution candidate of local document ingestion, Librarian,
  semantic search, bounded Ruflo/CAO teams and dashboard.
- Positive-allowlist export from private canonical sources.
- Guided bootstrap, safe diagnostics, transactional deployment and conservative
  uninstall, preserving indexes, queues and private audit.
- Technical Drop files are ignored without deletion; relation titles are redacted.
- Canonical and evidence searches recheck current file access; indexing rejects
  external symlinks and extracted-text paths outside the designated directory.
- Python and Node dependencies are locked; command conflicts and interrupted
  uninstall/export preserve previous files. ONNX runtime telemetry is disabled.
- Ruflo initialization and memory operations share one configured database
  directory; existing databases are not reinitialized or moved.
