# Security

Use GitHub's private vulnerability reporting for security issues when enabled.
Do not disclose credentials, real vault content, full MCP configuration or
unredacted provider logs in public issues. If private reporting is unavailable,
open a minimal issue requesting a private contact without exploit details.

Supported release line: 0.1.x after release. This is a local single-user system;
do not expose its dashboard or CAO API to the internet. Client permissions,
snapshot boundaries, path validation and rollback must not be weakened to make
a test pass. See docs/privacy.md for limits of redaction.
