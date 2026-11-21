# Member portal setup and operation

## Known facts and verification boundary

Use the [BCBS Global Solutions member portal](https://members.bcbsglobalsolutions.com/login/) for GeoBlue claims. Verify member access, the saved reimbursement method and the eClaim wizard against the current account. Store account-specific evidence and identifiers under private `data/`. See [fast-path.md](fast-path.md) for observed controls and how to time the workflow.

The official login guide states that the Member Portal and mobile app share login credentials. The group policy governs eligibility, documents and filing deadlines; the general website is not a substitute for that policy.

Official references:

- https://bcbsglobalsolutions.com/support/account-login-help/
- https://bcbsglobalsolutions.com/support/claims-help/
- https://bcbsglobalsolutions.com/geoblue-rebrand/members/
- https://groupadmin.bcbsglobalsolutions.com/faqs.cfm (family-access information; verify current requirements in the actual account)

## First configuration

Use the available Codex browser/computer tools and their current documentation. There is no insurer API integration in this project. Do not reverse-engineer private endpoints or embed saved cookies, passwords or OTPs in files. Ask the user to sign into the official website and handle authentication challenges. Reuse a supported browser session; a phone App session is not a browser session.

On this Mac, the native HEIC codec needed execution outside the restrictive shell sandbox in testing. If local `sips` conversion is blocked, request only the scoped permission needed to rerun that local preparation command. Do not conclude that the user's file is damaged from a native-codec permission failure; preserve it and report an unresolved preparation error if access remains unavailable.

Before entering or uploading claim data:

1. Read the account's patient list. Check each patient's name, date of birth and member number and which account can access that patient. Do not infer access from family relationship. If a dependent is not accessible, record `needs_authorization` and explain the official next step shown by the portal; other accessible members may proceed.
2. Read the actual policy and benefits documents. Download relevant documents through supported tools, import/prepare them and cite the page containing the filing rule. Record unknown rules as null. Never copy a deadline from another plan or assume a number of days.
3. Inspect the existing saved ACH, wire or check method. Record only its type and a masked label (or delivery label for a check), with portal evidence. Leave full bank details and credentials on the insurer site. If no existing method is configured, have the user configure it on the official site, then verify it.
4. Inspect the claim form without typing new medical data or uploading documents. Record the actual required fields, accepted attachment formats/sizes, patient selector, original-currency fields, reimbursement selector, certification text, submit button and success indicators in a private `data/portal-adapter.json`. Do not store credentials or token-bearing URLs. Store the evidence date and actual origin. Update this private map when the page changes; do not use guessed or stale selectors.
5. Save observed portal/policy evidence with `artifact add --kind portal_verification --file <actual-screenshot-or-download>`. Use the returned artifact id in profile/policy evidence, then `member put` and `policy put`.

If a policy is verified but its filing deadline cannot be found, leave the rule null and show that warning in review. If patient access or reimbursement method is not verified, validation blocks approval.

## Browser preparation and final submission

The default is fast website preparation, then a separate final confirmation. For authorized entry/upload use `draft authorize-entry` / `draft entry-guard` and helper `mode:'prepare'`; do not call `submission begin` during preparation. Stop at Authorization with declaration/signature untouched and show the complete final review. Only after the actual user confirms that prepared application use `draft approve`, `submission begin`, a fresh `submission guard` and a new production helper run.

- Re-read the live page, selected patient and exact origin; reuse verified account defaults. All browser actions use supported browser tools and the native pointer/keyboard fast path. Do not rediscover unrelated account settings or assume old screenshots still match the form.
- The approved `destination_origin` must match the actual origin receiving claim data. If an official link redirects to another origin, verify it and revise the draft's destination, then obtain new approval before transmitting. The current helper accepts HTTPS origins under `bcbsglobalsolutions.com`; an external destination requires deliberate adapter work and user approval.
- Fill only the entry-guard snapshot during preparation and the locked approved snapshot during final execution. Check dates, decimal separators and displayed currency. Preserve original currency. Resolve changed values before the final review.
- Before upload, read the browser tool's file-upload documentation. Use exact archive paths from `entry-guard`, or the final `submission guard` for any newly authorized final-stage upload. If conversion/resizing is required, import that derivative and obtain approval for the actual attachment change before transmitting it.
- Review the website summary and actual declarations with the user before final approval. After that approval, follow current tool rules to certify/sign only as specifically authorized, then verify origin/q reference and the fresh guard before the one final click. Do not accept an unexpected agreement on a generic historical approval.
- After final submit, observe and save the real receipt or matching submitted record. Read screenshot/download documentation before saving a result. Import it with `artifact add --kind submission_receipt`, then `submission finish <attempt_id> --claim-number <observed-number> --artifact <id>`.

The immediate success page can supply only an **eClaim ID**, with an actual Claim Number assigned later. Use `submission finish-eclaim <attempt_id> --eclaim-id <observed-id> --artifact <receipt-id>` for that receipt. This resolves the attempt as submitted while leaving Claim Number null. Follow-up observations can match the eClaim ID and later associate the actual Claim Number; never put the eClaim ID into the Claim Number field.

If the website is unavailable, retains a loader, rejects a document, or exposes unfamiliar required fields, preserve the local draft and describe the blocker. Do not switch to email or claim completion based on a button click alone.

## Interrupted or uncertain submission

Use `submission unknown <attempt_id> --reason <what-was-observed>` immediately after an ambiguous outcome. `recover` marks interrupted open attempts unknown on the next run. Neither command authorizes retrying.

Search the official account's records using the patient, dates, provider and amounts from `submission show <attempt_id>`. An initially absent record may reflect processing delay; absence alone does not prove the application was not received.

- If a matching submission is found: save evidence and call `submission finish` on the existing attempt with the observed number.
- If authoritative website/customer-service evidence establishes that the submission was not received, save that evidence and obtain the user's confirmation of the retry decision. Then use `submission not-submitted <attempt_id> --artifact <id> --user-message <actual-message> --explanation <evidence-description>`. This clears the old approval; review and obtain fresh approval before a new attempt.
- If still ambiguous: keep unknown state. Do not create another initial claim or retry the final click.

## Status and EOBs

For each requested submitted initial claim, match its official number and patient in the live portal. Save a current status screenshot/download; import an observation following the data contract. Save available EOBs with `artifact add --kind eob` and include their ids and actual supplement requirements/denial details in the observation.

Recognized English labels are `Action Required`, `In Process`, `Payment Processing`, `Complete` and `Duplicate`. Unknown labels remain verbatim and do not acquire an invented terminal meaning. A `Complete` claim can still have unpaid or denied amounts; report the EOB's actual facts. Never infer a deposit from these states.
