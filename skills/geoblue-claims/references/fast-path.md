# Fast path, timing and test boundary

Use after account setup on the authenticated member website. Account access, policy, patient mapping and the saved payout method come from private records. Revisit them when missing, changed, contradicted by the page or required to reconcile an unresolved attempt. Keep existing contact details and select the saved masked payout method from the patient's verified private profile. Ask for missing facts in one batch.

Use this preparation path with [headless Playwright MCP](headless.md) for new real bills. After per-claim entry/upload authorization, finish the website draft, show the final review and stop for the user's actual final confirmation. Do not start a submission attempt, certify or sign during preparation. New actual bills use their own evidence and matching real ledger draft; TEST drafts stay excluded from real filing.

## Three-minute plan

| Stage | Active-time budget | Action |
| --- | ---: | --- |
| Intake and interpretation | 40 seconds | One `intake`; read only new/unassigned material, retain page evidence, perform local arithmetic and duplicate checks. Reuse existing extracted fields only for identical hashes. |
| Website preparation | 110 seconds | Direct eClaim entry; batch known actions by wizard stage; name attachments before uploading; use saved provider/location and payout defaults. |
| Review and formal result | 30 seconds | One compact final review; production only: fresh approval/guard, one final click, actual receipt archive. Human response time is separate. |

This is a target for complete, straightforward applications, not a guarantee for arbitrary document counts or website delays. Include model/tool round trips in the stopwatch. At 180 seconds save the current draft/stage and report the missing fact or failed control. Do not silently continue an unbounded retry loop. A time-limited partial draft is not a completed reimbursement.

## Observed route and reliable batching

1. Reuse the authenticated tab and its in-page navigation. A new tab, full reload or expired session can land on Login. To return Home, use the portal's Home control (or its hamburger menu); do not use `goto()` as a shortcut through an active wizard. From the ready homepage choose **File a new eClaim**, then the paperless **Get started**. Do not detour through benefits/enrollment/documents on each bill.
2. Preliminary questions: select PRIMARY MEMBER, wait for its **Selected** label; select BANK WIRE TRANSFER OR ACH PAYMENT and wait for its Selected label. Use the actual accident answer. Scroll Next into view. The next page may first show **NOTICE** with **Close**; do not wait for patient fields behind that notice.
3. Basic information: keep contact defaults, choose the exact patient, preserve a normal nickname (tests use TEST-DO-NOT-SUBMIT). Other Insurance's heading is **Other Insurance**, although the step indicator says Other Insurance Form. Wait for the next actual field rather than a guessed heading.
4. Charge: category before saved facility. Saved provider labels can be uppercase; match case-insensitively. Establish/select the saved clinic before country/currency. If the current visual page already shows the confirmed saved city/country/currency, preserve those fields. Do not rely on empty semantic input values to conclude a visible default is missing.
5. Dropdowns use role **searchbox**, not textbox named Search. Wait for the actual popup, focus its searchbox and use native `typeText` for filtering. Country **South Korea**; currency **Korean Republic Won (S.Korea)**, searched with Korea, are the observed Korean labels. These are exact observations, not defaults for other countries/currencies. After a choice, wait for the popup to close before the next field.
6. Date picker: use its button and the calendar's **mm/dd/yyyy** searchbox, then **OK**. Keep an already matching date. Flutter's semantic input can disagree with the rendered page. Click the currently rendered control with native pointer input and enter the value with the keyboard. Confirm the rendered result once per section.
7. Scroll the actual section into view before actions. Flutter may report off-screen controls as visible, and semantic click locations can drift after reflow. When a semantic action fails, take one fresh screenshot and target the visible control. Do not keep repeating the same selector or blindly reuse old indices/coordinates. Treat a macro failure as a handoff to this visual fallback.
8. Save one approved charge and verify its displayed provider, date, total and currency once. Respect each claim's explicitly approved grouping. Do not create an extra pharmacy charge when those costs are already included.
9. **Add Supporting Documents** is generic text, despite native AX sometimes calling it a link. Use `getByText`. File chooser takes one file per upload and returns to the saved-charge page. Wait for each friendly filename; do not upload an existing filename again on resume. Exact bytes and paths come from the local guard.
10. Reimbursement: choose the existing masked saved account; keep its values. Scroll Next into view. Both real preparation and tests stop at Authorization with blank signature and unchecked certification. Show a final review and screenshot; only the user's subsequent confirmation permits a separate production submission. Keep the one-click/unknown-outcome rules.

The page can render a form skeleton before displaying a blocking **Loading** overlay. Field existence alone is insufficient readiness. Wait for that overlay to disappear and re-read the UI before pointer actions. Never focus an underlying control through a loading overlay.

## Optional Codex CUA helper

Read current CUA/browser/upload documentation first. Load the local module with dynamic `import()` in `cua_repl`; string `eval` is unavailable. Do not run it as a terminal browser driver. Example (use the actual absolute project path):

```js
var mod = await import('/absolute/project/skills/geoblue-claims/scripts/fast-flow.mjs');
var run = mod.createGeoBlueFastRun(tab, {mode:'prepare'});
// Use the entry-guard snapshot/paths and the private observed UI mapping.
await run.prepareStart(payload); // New real draft only; see saved-draft entry below.
await run.prepareCharge(payload);
// Save observed charge/attachment evidence before leaving Charges.
await run.prepareFinish(payload); // Stops at Authorization; no declaration/signature/Submit.
nodeRepl.write(run.report());
// Show the final review and wait for the actual user's final confirmation.
```

Payload requires `accident:false`, `other_insurance:false`, `category:'hospital_facility'`, exact `patient`, `facility`, `facility_search`, `city`, `country_search`, `country_option`, `currency_search`, `currency_option`, `condition`, `service`, decimal-string `total`, `service_date_us`, `account_last4` and `attachments:[{path,filename,sha256}]`. For real drafts, optional `nickname` and `charge_nickname` replace generated defaults only when supplied. Other answers/multiple charges/unseen provider forms use the actual form and native helpers instead of forcing this shortcut.

Set `saved_location_visually_verified` / `saved_currency_visually_verified` only after the current page or trustworthy saved-facility evidence establishes those exact values for this payload; these flags preserve the observed defaults rather than entering replacements. They do not supply missing evidence. Do not cache false claims of verification.

| Mode | Use | Final action |
| --- | --- | --- |
| `prepare` | Normal real application names and authorized entry/upload | Stops before certification, signature and Submit |
| `test` (factory default) | Explicitly requested rehearsals with TEST names | Never certify, sign or Submit |
| `production` | New run only after the prepared application's final review is confirmed | Matching fresh local guard, same q reference, one final click |

Modes are immutable. Create a new production run after final confirmation; do not switch a preparation/test run or treat its entry authorization as final approval. The submit gate and deadline are tested against a local simulated page. Do not probe the final-submit method on the real portal during testing. Local CLI guards and actual conversation consent remain mandatory. Retain private evidence under `data/portal-evidence/`, never patient data in fixtures.

CUA dynamic imports are cached within a session. If this module was already imported before a skill update, load a trusted temporary copy named with the current source fingerprint rather than silently using the old module. String `eval` is unavailable, and version query suffixes were interpreted as literal file paths by the observed loader. An ordinary fresh session can import the source path directly.

For repeated live diagnostics, reuse an existing named TEST draft. The helper accepts `resume_eclaim_id` only at a Preliminary Questions URL carrying that same q reference and verifies that the reference survives into Basic Information. If the portal drops or changes it, stop; do not silently create another draft. A form's Back button may use browser history, so a directly opened form can return to an unrelated prior wizard page. Inspect the actual URL after Back. Existing attachments are skipped by filename on an unchanged, confirmed snapshot; never use filename alone to authorize changed bytes.

### Verified saved-draft entry

From the authenticated homepage, use **eClaims** (not File a new eClaim). Wait for **Loading unsubmitted eClaim list** to disappear. In **Unsubmitted eClaims**, find the exact patient group and filter it by the complete `eClaim ID: <existing-id>` text, then use that group's **details** button. The observed entry shows NOTICE; close it and wait for **Patient dropdown**. The resulting URL is Basic Information (`.../claimant?q=<existing-id>`), not Preliminary Questions. Verify the same q reference before editing.

Preserve the existing nickname and contact fields; select the confirmed patient, proceed through Other Insurance, then fill the charge. Normal runs choose only their matching real ledger draft; explicit rehearsals choose a named TEST draft. If a charge is already present, edit that existing row instead of adding another one. Load prior successful upload records (`report.uploads`) as `uploadedAttachments` only after confirming the approved fingerprints are unchanged; pass each attachment's SHA-256 to `upload`/`prepareFinish`. Matching saved upload evidence is required to skip a filename.

The native helper's `prepareCharge` and `prepareFinish` batches were used after this observed saved-draft entry. `prepareStart` is the new-application path; do not call it merely to resume a saved draft. An untouched test Authorization page reports Submission disabled in the button label, even though the generic semantic `isEnabled()` API returned true. Use the actual label and blank certification/signature evidence; do not infer readiness from that Boolean alone.

## Timing a run

Start the clock at intake for new bills or at the first browser action for a draft replay. Include model and tool round trips. Record the end stage, any user wait time, and whether the documents were already interpreted. Save the timings with the draft evidence under `data/portal-evidence/`. Report intake, live portal preparation and final submission only for the stages actually measured. Tests stop before certification, signature and Submit.
