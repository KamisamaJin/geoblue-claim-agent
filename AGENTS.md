# GeoBlue reimbursement project

For GeoBlue / BCBS Global Solutions reimbursement requests, read and use `skills/geoblue-claims/SKILL.md` (also discoverable through the project skill link when installed).

Run helpers from this project root with `python3 -m geoblue_claims`. Run deterministic tests with `python3 -m unittest discover -s tests -v`. The runtime has no Python package dependencies; PDF processing uses Poppler and HEIC uses macOS sips.

Medical originals, account preferences, patient records, inputs, evidence, timing records, databases and audit logs belong only in ignored `inbox/` or `data/`. Use synthetic fixtures and generic documentation. Before publishing, inspect the staged tree for personal names, account suffixes, claim IDs, local home paths, private files and credentials.

The Python helpers perform local operations only. The skill uses Codex browser tools in Codex and project-scoped Playwright MCP in Claude Code. Obtain authorization before entering or uploading claim data and separate final confirmation before submission. Reconcile an unresolved submission before retrying. Record deposits separately from portal status.
