# Privacy

Everything runs locally except provider reasoning and dependency/model downloads. Local embedding search does not consume AI quota. A retrieved note or redacted snapshot may be sent to the selected provider for reasoning; local storage does not make that subsequent call offline.

Embedding entry points disable ONNX Runtime telemetry through its supported API.
Third-party clients retain their own telemetry and privacy settings.

Never put the live vault inside the public code checkout. Original documents, extracted text, queues, transactions, team snapshots, indexes, session logs and backup configurations must stay private. Redaction is best effort and not a guarantee of anonymization. Do not submit documents that should not reach a provider.

The exporter uses a positive file allowlist and refuses symlinks, known private paths and credential patterns. An unknown file in the public working copy blocks export. Inspect the staged Git diff before every upload. Secret scanning supplements human review; it cannot recognize every possible sensitive value.

`sbe doctor` reports only known component names, versions and states. Other status commands intentionally include local task/note metadata and should not be shared unreviewed. Never share raw client MCP lists or private backup files.
