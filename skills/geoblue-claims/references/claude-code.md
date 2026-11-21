# Claude Code with Playwright MCP

Open Claude Code from the repository root. The link at `.claude/skills/geoblue-claims` registers `/geoblue-claims`. The project `.mcp.json` starts `geoblue_playwright`, a local Microsoft Playwright MCP server pinned to `@playwright/mcp@0.0.86`.

The browser setup needs Node.js 18 or later, `npx` or `pnpm`, and Google Chrome. Approve the project MCP server in Claude Code and check its configuration with `claude mcp get geoblue_playwright`. The server starts headless and uses the dedicated Chrome profile for this checkout.

## Preparation

Use the `geoblue_playwright` tools directly with the parent skill's intake, entry authorization, and final confirmation. Navigate to the official member homepage, take a snapshot and check for the authenticated **eClaims** control. Dismiss a first-time introduction with **Skip** if it covers the wizard. Follow the [headless workflow](headless.md) and [fast path](fast-path.md) for filling, uploading and the Authorization checkpoint.

If the portal requires sign-in, release the Chrome profile with `browser_close` and use the shared manual sign-in command from the same checkout. After sign-in and closure of that window, navigate with MCP again and verify the authenticated homepage before resuming.

## Test a saved draft

Use only an explicitly authorized, existing draft whose exact eClaim ID is listed under **Unsubmitted eClaims** and whose nickname marks it as a test. Recheck the same ID before each mutation. A test can exercise an existing charge and a new authorized test attachment. Keep existing receipts and do not create duplicate charge rows or upload an existing file twice. Use a clearly named synthetic file without patient data if a dedicated upload probe is needed and authorized. Stop at Authorization with the checkbox unchecked and signature blank. **Never invoke the final Submit action, certify, sign, run `draft approve`, or start a submission attempt in a test.** A TEST draft never becomes a production application.

Time from the first action in the signed-in portal through the Authorization checkpoint. Include tool and model round trips and record login or user wait separately. Save the start/end stages, same-draft evidence, verified attachment name, screenshot path, and submit disposition under private `data/portal-evidence/`. Report whether the run used Claude Code or a direct MCP client.

## Formal submission

Only after the user confirms the exact prepared real claim and its declaration, follow the parent skill's `draft approve`, `submission begin`, and `submission guard` procedure. Check the live origin, draft ID, real claim identity, and final snapshot. Use the actual declaration and authorized signer. Observe the final **Submit eClaim** button and click it once after the fresh guard. Save the real receipt and record the result. If the outcome is uncertain, stop and reconcile through the member portal before any retry.
