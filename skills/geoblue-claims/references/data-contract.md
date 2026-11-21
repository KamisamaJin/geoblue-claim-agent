# Private JSON contracts and commands

All examples are synthetic. Write real inputs only under the project's `data/inputs/`. Every command prints JSON; errors go to stderr with a nonzero exit code. Inspect arrays of per-document errors even when other files succeeded. Help is available with `python3 -m geoblue_claims --help` and nested `--help`.

## Source references

References are nonempty lists. A document reference requires an imported, prepared document id and an existing 1-based page. Draft clinical references must refer to attached documents. Profile/policy sources can refer to other imported policy documents.

```json
[
  {"source":"document","document_id":"<sha256 from scan>","page":1,"quote":"exact original-language text"},
  {"source":"artifact","artifact_id":"<id from artifact add>","note":"what the actual saved portal evidence establishes"},
  {"source":"user","note":"actual information supplied or corrected by the user in this chat"}
]
```

Use only relevant entries, not all three. A model assumption is never a user source. Evidence ids point to private immutable copies; do not invent ids or write a local summary and label it as portal evidence.

## Members and policies

`member put --file <private-json>` stores this structure. `member list` reads profiles. Names and member numbers must match the actual account. One profile represents one patient. Do not store passwords, cookies, OTPs, raw bank account/routing numbers or API keys.

```json
{
  "id":"self", "full_name":"Demo Member", "date_of_birth":"1990-01-02",
  "member_number":"SYNTHETIC-01", "relationship":"self", "policy_id":"group",
  "account_label":"primary-account", "access_status":"verified",
  "evidence":[{"source":"artifact","artifact_id":"<actual evidence id>","note":"Observed patient and member details"}],
  "reimbursement":{
    "method":"ach", "label":"USD ACH ****1234", "saved_on_portal":true,
    "evidence":[{"source":"artifact","artifact_id":"<actual evidence id>","note":"Observed saved masked method"}]
  }
}
```

`access_status`: `unverified`, `needs_authorization`, or `verified`. Reimbursement method: `ach`, `wire` or `check`. `saved_on_portal` must be true and evidence must exist before approval. Updating a profile invalidates related unsubmitted approvals.

Optional preferences live in the ignored `data/portal-preferences.json`. Set `{"treat_clinic_bills_as_facility": true}` if the user wants clinic invoices to default to Hospital / Facility. Keep masked payout references in the member profile and derive the browser's `account_last4` from the profile's verified label when using the wire fast path.

`policy put --file <private-json>` accepts `id`, `name`, `verified` and `evidence` plus `filing_deadline_rule` (a faithful plan-specific rule, or null). If a rule is known, include nonempty `deadline_evidence` page references. `policy list` reads policies. Set `verified` true only after inspecting the actual member's group policy.

## Initial drafts

`draft put --file <private-json>` creates an application, or returns its existing id for identical input. Add `--claim <claim_id>` to update an unsubmitted one. It automatically supplies `kind: initial`, an empty `uncertainties` list, and `destination_origin: https://members.bcbsglobalsolutions.com` if absent.

```json
{
  "kind":"initial", "member_id":"self", "service_date":"2026-11-01", "country":"KR",
  "provider":{"name":"SYNTHETIC CLINIC","contact":"Synthetic provider contact"},
  "reason_for_treatment":"Reason documented by the provider or user",
  "currency":"KRW", "total":"10000",
  "charges":[
    {"id":"consultation","description":"진료 / consultation","amount":"8000","invoice_number":"DEMO-01","evidence":["<source reference object>"]},
    {"id":"medicine","description":"약 / medicine","amount":"2000","invoice_number":"DEMO-01","evidence":["<source reference object>"]}
  ],
  "attachments":["<document id>"], "payment_proof":["<source reference object>"],
  "uncertainties":[],
  "evidence":{
    "member_id":["<source reference object>"], "service_date":["<source reference object>"],
    "country":["<source reference object>"], "provider.name":["<source reference object>"],
    "provider.contact":["<source reference object>"], "reason_for_treatment":["<source reference object>"],
    "currency":["<source reference object>"], "total":["<source reference object>"]
  }
}
```

Replace every illustrative reference string with a real reference **object**. An unresolved draft may contain blanks, null values and `uncertainties`; `draft review` identifies what must be resolved before approval. It never fills values.

- Amounts are exact decimal **strings**, without symbols or separators. Arithmetic uses Decimal and performs no conversion or rounding. Discounts can be negative itemized entries; the total must be positive and equal the sum. Each charge has a unique local id. Different currencies require separate drafts.
- Dates are `YYYY-MM-DD`; country/currency use two/three-letter uppercase codes read from evidence. Check the actual currency against its source; format validation alone does not establish a valid or correct currency.
- `payment_proof` links a receipt or user-provided proof. An invoice alone must not be described as proof of payment unless it actually indicates payment.
- Optional `visit_reference` distinguishes genuinely separate visits. Optional `filing_deadline` requires `evidence.filing_deadline` and a verified plan rule; the tool does not calculate eligibility or exceptions.
- `draft distinguish <id> --other <candidate-id> --user-message <actual-explanation>` acknowledges a possible duplicate. Revisions invalidate this acknowledgment when the other draft changes. `draft cancel <id> --reason ...` cancels only a local unsubmitted draft.

## Supplements

Create a new draft with `kind: supplement`, `parent_claim_id` pointing to the submitted initial claim, the same `member_id`, `attachments`, a `note` describing requested documents and `evidence.note` references to the actual request. The request may be a saved portal artifact, while attachments contain the new supporting documents. Include `uncertainties` if needed. Do not change original clinical values or charge totals to send a supplement.

Review/approval/attempt commands are the same. A supplement's finish command must use its parent's existing official number. Its local completion does not assume that the parent portal state changed.

## Review and attempt records

`draft review <id>` returns validation errors/warnings, an exact snapshot and `review_token`.

The default preparation flow uses `draft authorize-entry <id> --token <current-review-token> --user-message <actual-entry-authorization>`, then `draft entry-guard <id>` before entering/uploading with helper `mode:'prepare'`. The entry guard may leave only saved reimbursement-method checks pending for later wizard steps. It returns `may_submit: false`, does not set `approved_hash`, and never permits `submission begin`. Snapshot changes invalidate it. Reuse valid case-specific entry authorization without asking again.

After preparing the website draft and choosing its saved payout method, present the complete final review and wait for the actual user's final confirmation. Only that confirmation permits the commands below and a new `mode:'production'` helper run. Do not run them while awaiting final review, or treat entry authorization as final approval:

```text
draft approve <id> --token <current-review-token> --user-message <actual-user-message>
submission begin <id>
submission guard <attempt-id>
```

The submission guard returns the locked snapshot and archived attachment paths. It is required for any remaining final-stage entry and final Submit; preparation uses the separate entry guard. Neither sends data itself. A changed document, artifact, patient, policy, destination or payment reference blocks submission and requires a new final review/confirmation. Replacing a still-present inbox source forces re-review. Moving/removing an incoming file does not delete its archive.

`artifact add --file <actual-evidence-path> --kind <kind>` stores evidence and returns an id. Kinds: `portal_verification`, `submission_receipt`, `status_evidence`, `eob`, `resolution_evidence`.

`submission finish <attempt-id> --claim-number <official-number> --artifact <receipt-id>` records success only after observing it. `submission unknown`, `recover`, and `submission not-submitted` implement the reconciliation flow in the browser reference. `submission show <attempt-id>` returns the authoritative locked payload. Submitted drafts are immutable; later profiles/files do not rewrite that payload.

If the actual receipt provides only an eClaim ID, use `submission finish-eclaim <attempt-id> --eclaim-id <observed-id> --artifact <receipt-id>`. It requires an intact `submission_receipt` artifact and resolves the attempt without inventing a Claim Number. `status` exposes both identifiers separately. A follow-up observation may use matching `eclaim_id` before a Claim Number exists; include both identifiers when an actual Claim Number is later assigned on that same eClaim.

## Portal observations and actual receipt

Save live evidence, then `portal-update <claim-id> --file <private-json>`:

```json
{
  "portal_claim_number":"<exact recorded number>",
  "raw_status":"In Process", "observed_at":"2030-01-15T12:00:00+09:00",
  "artifact_id":"<actual status evidence id>",
  "amounts":[{"label":"Exact portal amount label","amount":"7.35","currency":"USD"}],
  "supplement_requirements":[], "eob_artifact_ids":[], "denial_details":null
}
```

Use actual labels and values; omit unobserved optional amounts. Older observations do not overwrite newer ones. Preserve unknown raw statuses for review. Amounts never replace the original currency/charges.

`received <claim-id> --user-message <actual-user-confirmation> [--amount <actual-decimal> --currency <actual-currency>]` records the user's report of a deposit separately from portal processing. Amount/currency are both supplied or both omitted. `history <entity-id>` reads the private audit history.
