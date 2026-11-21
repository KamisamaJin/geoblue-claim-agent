# GeoBlue Claim Agent

English | [简体中文](README.zh-CN.md)

An agent skill and local command-line tool for GeoBlue / BCBS Global Solutions family claims. It organizes multilingual bills, records the source for each claim field, checks totals and duplicates, and prepares drafts in the member portal. It can also check claim status and prepare supporting documents.

## Getting started

Clone this repository and run the following from the project root with Python 3.9 or later:

```sh
python3 -m geoblue_claims doctor
python3 -m geoblue_claims init
python3 -m geoblue_claims install-skill
```

Open the project in Codex, place PDF, JPG, PNG, or HEIC files in `inbox/`, and ask:

> Use $geoblue-claims to process new bills from the inbox. Prepare each portal draft and stop for my confirmation before final submission.

On first use, sign in to the member portal and verify your policy, patient access, and saved reimbursement method. The browser and member portal handle authentication. [Account setup guide](skills/geoblue-claims/references/browser-and-setup.md)

You can also ask Codex to show drafts awaiting confirmation, prepare additional documents for a claim, check claim status, or record a deposit you received.

The skill asks for authorization to enter and upload each claim's information, then prepares the portal draft. At Authorization, it shows the patient, charges, attachments, and reimbursement method for your review. The skill waits for your final confirmation before certifying, signing, and submitting. Changes to the claim or attachments require fresh confirmation.

## Claude Code

Open this repository in Claude Code and approve the project `geoblue_playwright` MCP server. It needs Node.js 18 or later, `npx` or `pnpm`, and Google Chrome. Run `claude mcp get geoblue_playwright` to check the server connection, then invoke `/geoblue-claims`. Sign in to the member portal in the Chrome window Playwright opens. The same entry and final confirmation steps apply. [Claude Code setup and workflow](skills/geoblue-claims/references/claude-code.md)

## Requirements

The Python helpers use the standard library. The agent reads documents and uses the current host's browser tools for portal actions. `install-skill` links the bundled skill into `.agents/skills` for Codex. If Codex does not recognize `$geoblue-claims`, reopen the project or ask it to read the [skill file](skills/geoblue-claims/SKILL.md).

PDF preparation uses Poppler's `pdfinfo`, `pdftotext`, and `pdftoppm`. On macOS, install Poppler with `brew install poppler`. HEIC conversion uses the macOS `sips` utility and may require codec access outside a restricted sandbox. Files are limited to 100 MiB, and PDFs to 100 pages. The tool keeps the originals and reports files that need to be split.

## Local data and workflow

| Path | Contents |
| --- | --- |
| `inbox/` | Incoming source files. Scanning copies the files and leaves them in place. |
| `data/documents/` | Content-addressed originals, extracted PDF text, and page previews. |
| `data/inputs/` | Local JSON inputs for members, policies, drafts, and status updates. |
| `data/portal-preferences.json` | Optional form preferences. Masked reimbursement references come from verified member profiles. |
| `data/artifacts/` | Portal receipts, status screenshots, and explanations of benefits. |
| `data/claims/` and `data/claims.sqlite3` | Approved claim snapshots and the SQLite ledger. |

Git ignores `inbox/` and `data/`. File permissions restrict local access, and the local store is unencrypted. Store backups of `data/` in a private location.

`intake` scans the inbox, prepares new materials, and lists pending work. The CLI works with local files and records. CLI confirmation arguments record the user's authorization from the conversation.

In Codex the browser helper uses `prepare` for real drafts and `test` for rehearsals. Both stop before certification, signature, and final submission. Claude Code follows the same stages using Playwright MCP. After final confirmation of a real draft, the skill checks the submission guard before submitting. [Fast workflow](skills/geoblue-claims/references/fast-path.md)

If the outcome of a submission is uncertain, check the portal for a matching claim before considering a retry. A portal status of `Complete` records processing. Confirm a deposit separately when you receive it.

## Tests

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_fast_flow.cjs
```

The tests use synthetic data and cover document preparation, claim validation, duplicate detection, attachment integrity, approval changes, submission recovery, and the browser helper's submission guard.

The fast workflow targets three minutes of active processing for straightforward claims. Actual time depends on document volume, portal availability, and uploads. Record the stages covered by each measurement. [Timing guide](skills/geoblue-claims/references/fast-path.md#timing-a-run)

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
