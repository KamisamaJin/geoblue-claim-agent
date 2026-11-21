import base64
import copy
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from geoblue_claims.cli import main
from geoblue_claims.common import ClaimError, now
from geoblue_claims.store import Store


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2l8AAAAASUVORK5CYII=")


def make_pdf(scanned=False):
    """One synthetic page, entirely stdlib; no personal data or PDF dependency."""
    if scanned:
        stream = b"q 300 0 0 200 40 550 cm /Im0 Do Q"
        image = zlib.compress(bytes([235, 240, 250]) * 4)
        resources = b"/XObject << /Im0 6 0 R >>"
    else:
        stream = b"BT /F1 16 Tf 40 750 Td (SYNTHETIC BILL - USD 12.30) Tj ET"
        resources = b"/Font << /F1 5 0 R >>"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << " + resources + b" >> /Contents 4 0 R >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    if scanned:
        objects.append(b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length "
                       + str(len(image)).encode() + b" >>\nstream\n" + image + b"\nendstream")
    output, offsets = bytearray(b"%PDF-1.4\n"), [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    start = len(output)
    output.extend(("xref\n0 %d\n0000000000 65535 f \n" % len(offsets)).encode())
    for offset in offsets[1:]:
        output.extend(("%010d 00000 n \n" % offset).encode())
    output.extend(("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(offsets), start)).encode())
    return bytes(output)


class ClaimsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="geoblue-test-")
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root)
        (self.root / "inbox/bill.png").write_bytes(PNG)
        self.doc = self.store.scan()["imported"][0]["document_id"]
        self.store.prepare(self.doc)
        artifact_file = self.root / "onboarding.txt"
        artifact_file.write_text("SYNTHETIC portal evidence: Demo Member and saved ACH ****1234", encoding="utf-8")
        self.artifact = self.store.add_artifact(artifact_file, "portal_verification")["id"]
        self.art_ref = [{"source": "artifact", "artifact_id": self.artifact, "note": "Synthetic observed portal details"}]
        self.store.put_record("policies", {"id": "group", "name": "Synthetic group policy", "verified": True,
                                         "filing_deadline_rule": None, "evidence": self.art_ref})
        self.member = {"id": "self", "full_name": "Demo Member", "date_of_birth": "1990-01-02", "member_number": "SYNTHETIC-01",
                       "relationship": "self", "policy_id": "group", "account_label": "demo-account",
                       "access_status": "verified", "evidence": self.art_ref,
                       "reimbursement": {"method": "ach", "label": "USD ACH ****1234", "saved_on_portal": True, "evidence": self.art_ref}}
        self.store.put_record("members", self.member)
        self.ref = [{"source": "document", "document_id": self.doc, "page": 1, "quote": "SYNTHETIC / 진료비 / 医疗费用"}]
        self.draft = {"kind": "initial", "member_id": "self", "service_date": "2026-11-01", "country": "KR",
                      "provider": {"name": "가상 병원 / 示例医院", "contact": "Synthetic contact"},
                      "reason_for_treatment": "진료 / 门诊检查", "currency": "KRW", "total": "10000",
                      "charges": [{"id": "consultation", "description": "진료 / 诊疗", "amount": "8000", "invoice_number": "DEMO-01", "evidence": self.ref},
                                  {"id": "medicine", "description": "약 / 药品", "amount": "2000", "invoice_number": "DEMO-01", "evidence": self.ref}],
                      "attachments": [self.doc], "payment_proof": self.ref, "uncertainties": [],
                      "evidence": {f: self.ref for f in ("member_id", "service_date", "country", "provider.name", "provider.contact",
                                                           "reason_for_treatment", "currency", "total")}}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def create(self, draft=None):
        return self.store.put_draft(draft or self.draft)["claim_id"]

    def approved(self, draft=None):
        claim_id = self.create(draft)
        review = self.store.review(claim_id)
        self.assertEqual([], review["errors"])
        self.store.approve(claim_id, review["review_token"], "I confirm this specific synthetic application")
        return claim_id

    def submitted(self):
        claim_id = self.approved()
        attempt = self.store.begin(claim_id)
        self.store.finish(attempt["attempt_id"], "DEMO-CLAIM-01", self.artifact)
        return claim_id

    def test_scan_is_idempotent_keeps_original_and_records_aliases(self):
        (self.root / "inbox/another-photo-name.png").write_bytes(PNG)
        result = self.store.scan()
        self.assertEqual([], result["imported"])
        self.assertEqual(2, len(result["duplicates"]))
        self.assertEqual(1, len(self.store.documents()))
        self.assertTrue((self.root / "inbox/bill.png").exists())
        self.assertEqual(2, len(json.loads(self.store.documents()[0]["sources"])))

    def test_intake_prepares_only_unassigned_materials_and_preserves_attempts(self):
        self.assertEqual(1, len(self.store.intake()["new_materials"]))
        claim_id = self.approved()
        attempt = self.store.begin(claim_id)["attempt_id"]
        with patch.object(self.store, "prepare", side_effect=AssertionError("Must reuse associated bill")):
            result = self.store.intake()
        self.assertEqual([], result["new_materials"])
        self.assertEqual(attempt, result["unresolved_attempts"][0]["id"])
        self.assertEqual("open", self.store.attempt(attempt)["state"])

    def test_duplicate_draft_is_idempotent_even_after_submission(self):
        claim_id = self.submitted()
        result = self.store.put_draft(copy.deepcopy(self.draft))
        self.assertEqual(claim_id, result["claim_id"])
        self.assertTrue(result["existing"])
        self.assertEqual(1, len(self.store.claims()))

    def test_sources_and_multilingual_fields_survive_roundtrip(self):
        review = self.store.review(self.create())
        self.assertTrue(review["ready_for_approval"])
        self.assertEqual(self.draft["provider"], review["snapshot"]["draft"]["provider"])
        self.assertEqual(self.ref, review["snapshot"]["draft"]["evidence"]["total"])
        self.assertTrue(any(w["field"] == "filing_deadline_rule" for w in review["warnings"]))

    def test_patient_access_and_policy_must_be_verified(self):
        member = copy.deepcopy(self.member)
        member["access_status"] = "needs_authorization"
        member["id"] = "spouse"
        member["full_name"] = "Demo Spouse"
        self.store.put_record("members", member)
        draft = copy.deepcopy(self.draft)
        draft["member_id"] = "spouse"
        review = self.store.review(self.create(draft))
        self.assertTrue(any("patient" in x["message"] for x in review["errors"]))
        self.assertEqual("spouse", review["snapshot"]["member"]["id"])

    def test_missing_reason_bad_date_and_source_page_block_approval(self):
        draft = copy.deepcopy(self.draft)
        draft["reason_for_treatment"] = ""
        draft["service_date"] = "01/02/26"
        draft["evidence"]["total"] = [{**self.ref[0], "page": 2}]
        claim_id = self.create(draft)
        result = self.store.review(claim_id)
        fields = {x["field"] for x in result["errors"]}
        self.assertTrue({"reason_for_treatment", "service_date", "evidence.total"}.issubset(fields))
        with self.assertRaises(ClaimError):
            self.store.approve(claim_id, result["review_token"], "confirm")

    def test_exact_decimal_totals_and_conflicts(self):
        draft = copy.deepcopy(self.draft)
        draft["currency"], draft["total"] = "USD", "0.30"
        draft["charges"][0]["amount"], draft["charges"][1]["amount"] = "0.1", "0.2"
        claim_id = self.create(draft)
        self.assertFalse(self.store.review(claim_id)["errors"])
        draft["total"] = "0.31"
        self.store.put_draft(draft, claim_id)
        self.assertTrue(any(e["field"] == "total" for e in self.store.review(claim_id)["errors"]))

    def test_mixed_currencies_and_ambiguous_values_need_resolution(self):
        draft = copy.deepcopy(self.draft)
        draft["charges"][1]["currency"] = "USD"
        draft["uncertainties"] = [{"field": "diagnosis", "message": "Unreadable source"}]
        review = self.store.review(self.create(draft))
        self.assertTrue(any(e["field"].endswith("currency") for e in review["errors"]))
        self.assertTrue(any(e["field"] == "uncertainties" for e in review["errors"]))

    def test_review_token_is_bound_to_current_content(self):
        claim_id = self.create()
        token = self.store.review(claim_id)["review_token"]
        draft = copy.deepcopy(self.draft)
        draft["reason_for_treatment"] = "User-corrected reason"
        self.store.put_draft(draft, claim_id)
        with self.assertRaises(ClaimError):
            self.store.approve(claim_id, token, "confirm old review")
        with self.assertRaises(ClaimError):
            self.store.begin(claim_id)

    def test_entry_only_authorization_never_authorizes_submission(self):
        claim_id = self.create()
        member = copy.deepcopy(self.member)
        member["reimbursement"] = {}
        self.store.put_record("members", member)
        review = self.store.review(claim_id)
        with self.assertRaises(ClaimError):
            self.store.entry_guard(claim_id)
        self.store.authorize_entry(claim_id, review["review_token"], "Enter and upload these specific bills, but do not submit")
        guarded = self.store.entry_guard(claim_id)
        self.assertTrue(guarded["may_enter_or_upload"])
        self.assertFalse(guarded["may_submit"])
        self.assertIsNone(self.store.claim(claim_id)["approved_hash"])
        with self.assertRaises(ClaimError):
            self.store.begin(claim_id)

    def test_entry_only_guard_is_invalidated_by_changed_content(self):
        claim_id = self.create()
        review = self.store.review(claim_id)
        self.store.authorize_entry(claim_id, review["review_token"], "Enter these bills only")
        changed = copy.deepcopy(self.draft)
        changed["reason_for_treatment"] = "changed symptoms"
        self.store.put_draft(changed, claim_id)
        with self.assertRaises(ClaimError):
            self.store.entry_guard(claim_id)

    def test_named_upload_copy_keeps_the_exact_approved_bytes(self):
        claim_id = self.create()
        review = self.store.review(claim_id)
        self.store.authorize_entry(claim_id, review["review_token"], "Enter and upload only")
        attachment = self.store.entry_guard(claim_id)["attachments"][0]
        path = Path(attachment["path"])
        self.assertEqual("bill.png", path.name)
        self.assertEqual(PNG, path.read_bytes())
        path.write_bytes(PNG + b"changed")
        with self.assertRaises(ClaimError):
            self.store.entry_guard(claim_id)

    def test_entry_only_authorization_does_not_bypass_clinical_errors(self):
        draft = copy.deepcopy(self.draft)
        draft["total"] = "10001"
        claim_id = self.create(draft)
        review = self.store.review(claim_id)
        with self.assertRaises(ClaimError):
            self.store.authorize_entry(claim_id, review["review_token"], "Enter only")

    def test_member_and_saved_payment_change_invalidates_approval(self):
        claim_id = self.approved()
        member = copy.deepcopy(self.member)
        member["reimbursement"]["label"] = "USD ACH ****5678"
        self.store.put_record("members", member)
        self.assertIsNone(self.store.claim(claim_id)["approved_hash"])
        with self.assertRaises(ClaimError):
            self.store.begin(claim_id)

    def test_original_tampering_blocks_transmission(self):
        claim_id = self.approved()
        document = self.store.documents()[0]
        (self.root / document["stored_path"]).write_bytes(PNG + b"changed")
        with self.assertRaises(ClaimError):
            self.store.begin(claim_id)
        self.assertTrue(self.store.review(claim_id)["errors"])

    def test_replaced_inbox_source_blocks_old_approved_material(self):
        claim_id = self.approved()
        (self.root / "inbox/bill.png").write_bytes(PNG + b"replacement")
        with self.assertRaises(ClaimError):
            self.store.begin(claim_id)
        self.assertTrue(any("Inbox source" in e["message"] for e in self.store.review(claim_id)["errors"]))

    def test_replaced_inbox_during_attempt_forces_reconciliation(self):
        attempt = self.store.begin(self.approved())
        (self.root / "inbox/bill.png").write_bytes(PNG + b"replacement")
        with self.assertRaises(ClaimError):
            self.store.guard(attempt["attempt_id"])
        self.assertEqual("unknown", self.store.attempt(attempt["attempt_id"])["state"])

    def test_two_verified_patients_have_separate_claims(self):
        first = self.create()
        member = copy.deepcopy(self.member)
        member.update(id="child", full_name="Demo Child", member_number="SYNTHETIC-02", relationship="child")
        self.store.put_record("members", member)
        draft = copy.deepcopy(self.draft)
        draft["member_id"] = "child"
        second = self.create(draft)
        self.assertNotEqual(first, second)
        self.assertFalse(self.store.review(second)["errors"])
        self.assertEqual([], self.store.review(second)["duplicate_candidates"])

    def test_unapproved_destination_and_invalid_sources_are_blocked(self):
        draft = copy.deepcopy(self.draft)
        draft["destination_origin"] = "https://bcbsglobalsolutions.com.attacker.example"
        draft["evidence"]["total"] = [{"source": "document", "document_id": {}, "page": 1, "quote": "bad id"}]
        review = self.store.review(self.create(draft))
        self.assertTrue(any(e["field"] == "destination_origin" for e in review["errors"]))
        self.assertTrue(any(e["field"] == "evidence.total" for e in review["errors"]))

    def test_null_attachments_produce_validation_errors(self):
        draft = copy.deepcopy(self.draft)
        draft["attachments"] = None
        self.assertTrue(self.store.review(self.create(draft))["errors"])

    def test_evidence_tampering_blocks_approval(self):
        claim_id = self.create()
        artifact = self.store.db.execute("SELECT stored_path FROM artifacts WHERE id=?", (self.artifact,)).fetchone()
        (self.root / artifact[0]).write_text("changed")
        self.assertTrue(self.store.review(claim_id)["errors"])

    def test_inputs_changed_during_attempt_force_unknown(self):
        attempt = self.store.begin(self.approved())
        member = copy.deepcopy(self.member)
        member["full_name"] = "Corrected member name"
        self.store.put_record("members", member)
        with self.assertRaises(ClaimError):
            self.store.guard(attempt["attempt_id"])
        self.assertEqual("unknown", self.store.attempt(attempt["attempt_id"])["state"])
        self.assertEqual("submission_unknown", self.store.claim(attempt["claim_id"])["status"])

    def test_only_one_attempt_can_begin_in_two_connections(self):
        claim_id = self.approved()
        self.store.begin(claim_id)
        with Store(self.root) as other:
            with self.assertRaises(ClaimError):
                other.begin(claim_id)

    def test_interrupted_attempt_cannot_be_retried_without_reconciliation(self):
        claim_id = self.approved()
        attempt = self.store.begin(claim_id)
        recovered = self.store.recover()
        self.assertEqual(attempt["attempt_id"], recovered["unresolved_attempts"][0]["id"])
        for call in (lambda: self.store.begin(claim_id), lambda: self.store.put_draft(self.draft, claim_id),
                     lambda: self.store.guard(attempt["attempt_id"]),
                     lambda: self.store.approve(claim_id, "old", "retry")):
            with self.assertRaises(ClaimError):
                call()
        self.store.finish(attempt["attempt_id"], "DEMO-RECOVERED", self.artifact)
        self.assertEqual("submitted", self.store.claim(claim_id)["status"])

    def test_not_submitted_resolution_requires_evidence_and_new_approval(self):
        claim_id = self.approved()
        attempt = self.store.begin(claim_id)
        self.store.unknown(attempt["attempt_id"], "Synthetic timeout")
        with self.assertRaises(ClaimError):
            self.store.resolve_not_submitted(attempt["attempt_id"], "missing", "retry", "verified")
        result = self.store.resolve_not_submitted(attempt["attempt_id"], self.artifact, "I confirm retry", "Synthetic definitive failure evidence")
        self.assertTrue(result["requires_new_approval"])
        with self.assertRaises(ClaimError):
            self.store.begin(claim_id)
        review = self.store.review(claim_id)
        self.store.approve(claim_id, review["review_token"], "I confirm this new attempt")
        self.assertNotEqual(attempt["attempt_id"], self.store.begin(claim_id)["attempt_id"])

    def test_semantic_duplicates_need_user_explanation(self):
        first = self.create()
        draft = copy.deepcopy(self.draft)
        draft["visit_reference"] = "another scan of an apparently identical visit"
        second = self.create(draft)
        review = self.store.review(second)
        self.assertTrue(any(x["field"] == "duplicate" for x in review["errors"]))
        self.store.distinguish(second, [first], "These are two distinct visits; invoice number was reused")
        self.assertFalse(self.store.review(second)["errors"])
        changed = copy.deepcopy(self.draft)
        changed["reason_for_treatment"] = "corrected"
        self.store.put_draft(changed, first)
        self.assertTrue(any(x["field"] == "duplicate" for x in self.store.review(second)["errors"]))

    def test_unknown_submission_cannot_be_bypassed_by_new_draft(self):
        first = self.approved()
        attempt = self.store.begin(first)
        self.store.unknown(attempt["attempt_id"], "timeout")
        replacement = copy.deepcopy(self.draft)
        replacement["visit_reference"] = "replacement"
        second = self.create(replacement)
        self.store.distinguish(second, [first], "This should not bypass an unknown attempt")
        review = self.store.review(second)
        self.assertTrue(any(x["field"] == "unresolved_submission" for x in review["errors"]))
        with self.assertRaises(ClaimError):
            self.store.approve(second, review["review_token"], "confirm")

    def test_supplements_use_original_patient_and_official_number(self):
        original = self.submitted()
        supplement = {"kind": "supplement", "parent_claim_id": original, "member_id": "self", "attachments": [self.doc],
                      "note": "Requested additional documentation", "evidence": {"note": self.ref}, "uncertainties": []}
        claim_id = self.approved(supplement)
        attempt = self.store.begin(claim_id)
        self.assertEqual("DEMO-CLAIM-01", attempt["snapshot"]["parent_portal_claim_number"])
        with self.assertRaises(ClaimError):
            self.store.finish(attempt["attempt_id"], "WRONG-NEW-CLAIM", self.artifact)
        self.store.finish(attempt["attempt_id"], "DEMO-CLAIM-01", self.artifact)
        self.assertEqual("submitted", self.store.claim(original)["status"])

    def test_complete_status_does_not_mean_funds_received(self):
        claim_id = self.submitted()
        details = {"portal_claim_number": "DEMO-CLAIM-01", "raw_status": "Complete", "observed_at": now(),
                   "artifact_id": self.artifact, "amounts": [{"label": "Portal paid amount", "amount": "7.35", "currency": "USD"}]}
        result = self.store.portal_update(claim_id, details)
        self.assertEqual("processed", result["status"])
        self.assertFalse(result["funds_received_confirmed"])
        self.assertIsNone(self.store.claim(claim_id)["funds_received"])
        self.store.confirm_received(claim_id, "I saw the deposit", "7.35", "USD")
        self.assertTrue(self.store.claim(claim_id)["funds_received"])
        self.assertEqual("KRW", self.store.claim(claim_id)["draft"]["currency"])

    def test_unknown_and_stale_statuses_cannot_invent_terminal_results(self):
        claim_id = self.submitted()
        details = {"portal_claim_number": "DEMO-CLAIM-01", "raw_status": "New unfamiliar status", "observed_at": now(), "artifact_id": self.artifact}
        self.assertTrue(self.store.portal_update(claim_id, details)["unrecognized_status"])
        self.assertEqual("submitted", self.store.claim(claim_id)["status"])
        details["observed_at"] = "2000-01-01T00:00:00+00:00"
        with self.assertRaises(ClaimError):
            self.store.portal_update(claim_id, details)
        details["portal_claim_number"] = "OTHER-PATIENT"
        with self.assertRaises(ClaimError):
            self.store.portal_update(claim_id, details)

    def test_submitted_draft_is_immutable_and_finalization_idempotent(self):
        claim_id = self.approved()
        attempt = self.store.begin(claim_id)
        self.store.finish(attempt["attempt_id"], "DEMO-1", self.artifact)
        self.assertTrue(self.store.finish(attempt["attempt_id"], "DEMO-1", self.artifact)["existing"])
        with self.assertRaises(ClaimError):
            self.store.put_draft(self.draft, claim_id)
        with self.assertRaises(ClaimError):
            self.store.cancel(claim_id, "cannot retract")
        self.assertTrue(self.store.history(claim_id))

    def test_eclaim_receipt_does_not_invent_a_claim_number_or_allow_retry(self):
        claim_id = self.approved()
        attempt = self.store.begin(claim_id)["attempt_id"]
        receipt_file = self.root / "receipt.txt"
        receipt_file.write_text("SYNTHETIC eClaim submitted: ECLAIM-DEMO-1")
        receipt = self.store.add_artifact(receipt_file, "submission_receipt")["id"]
        self.store.unknown(attempt, "Timeout; success then found on website")
        self.store.finish_eclaim(attempt, "ECLAIM-DEMO-1", receipt)
        claim = self.store.claim(claim_id)
        self.assertEqual("submitted", claim["status"])
        self.assertIsNone(claim["portal_claim_number"])
        self.assertEqual("ECLAIM-DEMO-1", claim["portal_details"]["eclaim_id"])
        self.assertEqual("succeeded", self.store.attempt(attempt)["state"])
        self.assertTrue(self.store.finish_eclaim(attempt, "ECLAIM-DEMO-1", receipt)["existing"])
        observation = {"eclaim_id": "ECLAIM-DEMO-1", "raw_status": "In Process", "observed_at": now(), "artifact_id": receipt}
        self.store.portal_update(claim_id, observation)
        self.assertEqual("in_process", self.store.claim(claim_id)["status"])
        self.assertEqual("ECLAIM-DEMO-1", self.store.claim(claim_id)["portal_details"]["eclaim_id"])
        with self.assertRaises(ClaimError):
            self.store.portal_update(claim_id, {**observation, "eclaim_id": "WRONG-ID"})
        self.store.portal_update(claim_id, {**observation, "portal_claim_number": "ACTUAL-CLAIM-NUMBER"})
        self.assertEqual("ACTUAL-CLAIM-NUMBER", self.store.claim(claim_id)["portal_claim_number"])
        for action in (lambda: self.store.begin(claim_id), lambda: self.store.guard(attempt),
                       lambda: self.store.finish_eclaim(attempt, "OTHER-ID", receipt)):
            with self.assertRaises(ClaimError):
                action()

    def test_eclaim_finish_requires_an_intact_submission_receipt(self):
        attempt = self.store.begin(self.approved())["attempt_id"]
        with self.assertRaises(ClaimError):
            self.store.finish_eclaim(attempt, "ECLAIM-DEMO", self.artifact)

    def test_credentials_are_rejected_and_private_permissions_applied(self):
        member = copy.deepcopy(self.member)
        member["password"] = "never-store-this"
        with self.assertRaises(ClaimError):
            self.store.put_record("members", member)
        self.assertEqual(0o700, stat.S_IMODE((self.root / "data").stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE((self.root / "data/claims.sqlite3").stat().st_mode))

    def test_symlinks_and_invalid_images_are_not_prepared(self):
        (self.root / "inbox/link.png").symlink_to(self.root / "inbox/bill.png")
        (self.root / "inbox/bad.png").write_bytes(b"this is not an image")
        result = self.store.scan()
        self.assertTrue(any(x["reason"] == "symlink" for x in result["skipped"]))
        bad = result["imported"][0]["document_id"]
        with self.assertRaises(ClaimError):
            self.store.prepare(bad)
        self.assertEqual("error", next(x for x in self.store.documents() if x["id"] == bad)["state"])

    @unittest.skipUnless(all(shutil.which(t) for t in ("pdfinfo", "pdftotext", "pdftoppm")), "Poppler is not installed")
    def test_text_and_scanned_pdfs_render_with_page_provenance(self):
        for scanned in (False, True):
            path = self.root / ("inbox/scan.pdf" if scanned else "inbox/text.pdf")
            path.write_bytes(make_pdf(scanned))
            document_id = self.store.scan()["imported"][0]["document_id"]
            prepared = self.store.prepare(document_id)
            self.assertEqual(1, prepared["page_count"])
            self.assertTrue(Path(prepared["pages"][0]["path"]).read_bytes().startswith(b"\x89PNG"))
            text = Path(prepared["text_path"]).read_text()
            self.assertEqual(not scanned, "SYNTHETIC BILL" in text)
            self.assertEqual(prepared, self.store.prepare(document_id))

    def test_cli_returns_actionable_json_and_does_not_send_network_requests(self):
        for arguments in (["status"], ["doctor"], ["document", "list"]):
            out = io.StringIO()
            with redirect_stdout(out), patch("socket.socket", side_effect=AssertionError("No network calls are permitted in local helpers")):
                code = main(["--root", str(self.root)] + arguments)
            self.assertEqual(0, code)
            self.assertIsInstance(json.loads(out.getvalue()), (dict, list))
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["--root", str(self.root), "draft", "show", "missing"])
        self.assertEqual(1, code)
        self.assertFalse(json.loads(err.getvalue())["ok"])

    @unittest.skipUnless(shutil.which("sips"), "HEIC conversion requires macOS sips")
    def test_heic_conversion_to_viewable_page(self):
        # Generate a 64px synthetic RGB PNG without installing an imaging library.
        import struct
        def chunk(kind, content):
            return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress((b"\x00" + bytes([220, 230, 240]) * 64) * 64)) + chunk(b"IEND", b""))
        source = self.root / "synthetic.png"
        source.write_bytes(png)
        heic = self.root / "inbox/synthetic.heic"
        converted = subprocess.run([shutil.which("sips"), "-s", "format", "heic", str(source), "--out", str(heic)], capture_output=True)
        if converted.returncode or not heic.exists():
            self.skipTest("This system's sips has no HEIC encoder")
        doc_id = self.store.scan()["imported"][0]["document_id"]
        prepared = self.store.prepare(doc_id)
        self.assertEqual(1, prepared["page_count"])
        self.assertTrue(Path(prepared["pages"][0]["path"]).read_bytes().startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
