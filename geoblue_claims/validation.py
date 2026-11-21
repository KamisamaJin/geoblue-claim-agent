"""Conservative structural and arithmetic checks, not coverage adjudication."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from .common import ClaimError, identifier, require_text

FORBIDDEN_KEYS = {"password", "passwd", "secret", "api_key", "access_token", "refresh_token",
                  "cookies", "cookie", "iban", "bank_account_number", "routing_number", "swift"}


def reject_credentials(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in FORBIDDEN_KEYS:
                raise ClaimError("Credentials and raw bank details must not be stored in project records")
            reject_credentials(child)
    elif isinstance(value, list):
        for child in value:
            reject_credentials(child)


def iso_date(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ClaimError(field + " must be an unambiguous YYYY-MM-DD date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ClaimError(field + " is not a valid date") from exc


def money(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        raise ClaimError(field + " must be an exact decimal string, without separators or currency symbols")
    try:
        amount = Decimal(value)
        if not amount.is_finite() or len(amount.as_tuple().digits) > 24 or amount.as_tuple().exponent < -8:
            raise InvalidOperation
        return amount
    except InvalidOperation as exc:
        raise ClaimError(field + " is not a supported finite amount") from exc


def validate_profile(value):
    reject_credentials(value)
    identifier(value.get("id"), "member id")
    for field in ("full_name", "member_number", "account_label", "policy_id", "relationship"):
        require_text(value.get(field), field)
    iso_date(value.get("date_of_birth"), "date_of_birth")
    if value.get("access_status", "unverified") not in {"verified", "unverified", "needs_authorization"}:
        raise ClaimError("access_status must be verified, unverified or needs_authorization")
    method = value.get("reimbursement", {})
    if not isinstance(method, dict):
        raise ClaimError("reimbursement must be an object")
    if method and method.get("method") not in {"ach", "wire", "check"}:
        raise ClaimError("reimbursement.method must be ach, wire or check")


def validate_policy(value):
    reject_credentials(value)
    identifier(value.get("id"), "policy id")
    require_text(value.get("name"), "policy name")
    if value.get("filing_deadline_rule") is not None:
        require_text(value["filing_deadline_rule"], "filing_deadline_rule")


def validate_draft(draft, member, policy, documents, artifacts, parent=None):
    errors, warnings = [], []

    def error(field, message):
        errors.append({"field": field, "message": message})

    def check(call, field):
        try:
            return call()
        except (ClaimError, TypeError, KeyError) as exc:
            error(field, str(exc))
            return None

    def refs(value, field, attached_only=True):
        if not isinstance(value, list) or not value:
            error(field, "At least one source reference is required")
            return
        for ref in value:
            if not isinstance(ref, dict):
                error(field, "Source reference must be an object")
                continue
            source = ref.get("source")
            if source == "document":
                doc_id = ref.get("document_id")
                doc = documents.get(doc_id) if isinstance(doc_id, str) else None
                if not doc or (attached_only and doc_id not in attachments):
                    error(field, "Document reference must name a known, attached document")
                elif (type(ref.get("page")) is not int or not 1 <= ref["page"] <= (doc.get("page_count") or 0)):
                    error(field, "Source page must exist in a prepared document")
                if not isinstance(ref.get("quote"), str) or not ref["quote"].strip():
                    error(field, "Document source must include the original-language quote")
            elif source == "artifact":
                if (not isinstance(ref.get("artifact_id"), str) or ref["artifact_id"] not in artifacts
                        or not isinstance(ref.get("note"), str) or not ref["note"].strip()):
                    error(field, "Artifact source requires a known artifact_id and note")
            elif source == "user":
                if not isinstance(ref.get("note"), str) or not ref["note"].strip():
                    error(field, "User source requires the actual user-provided information")
            else:
                error(field, "Source must be document, artifact or user")

    check(lambda: reject_credentials(draft), "draft")
    destination = draft.get("destination_origin", "")
    try:
        url = urlsplit(destination)
        hostname = url.hostname or ""
        if (url.scheme != "https" or url.username or url.password or url.port
                or url.path not in {"", "/"} or url.query or url.fragment
                or not (hostname == "bcbsglobalsolutions.com" or hostname.endswith(".bcbsglobalsolutions.com"))):
            raise ValueError
    except (TypeError, ValueError):
        error("destination_origin", "Use the exact verified HTTPS BCBS Global Solutions origin, without paths, query strings or credentials")
    kind = draft.get("kind", "initial")
    if kind not in {"initial", "supplement"}:
        error("kind", "Must be initial or supplement")
    attachments = draft.get("attachments", [])
    if (not isinstance(attachments, list) or not attachments
            or any(not isinstance(x, str) for x in attachments)):
        error("attachments", "At least one document id is required")
        attachments = []
    elif len(set(attachments)) != len(attachments):
        error("attachments", "Attachment ids must be unique")
    for doc_id in attachments:
        doc = documents.get(doc_id)
        if not doc or not doc.get("page_count") or doc.get("state") != "prepared":
            error("attachments", "All attachments must be imported and successfully prepared: " + doc_id)

    if not member:
        error("member_id", "Select an existing member profile")
    else:
        if member.get("access_status") != "verified":
            error("member_id", "Verify this patient's portal access; complete any required authorization first")
        refs(member.get("evidence"), "member.evidence", False)
        reimbursement = member.get("reimbursement", {})
        if reimbursement.get("saved_on_portal") is not True or not isinstance(reimbursement.get("label"), str) or not reimbursement["label"].strip():
            error("reimbursement", "Verify and select an existing saved reimbursement method")
        refs(reimbursement.get("evidence"), "reimbursement.evidence", False)
    if not policy or policy.get("verified") is not True:
        error("policy", "Verify the patient's actual group policy")
    else:
        refs(policy.get("evidence"), "policy.evidence", False)
        if policy.get("filing_deadline_rule"):
            refs(policy.get("deadline_evidence"), "policy.deadline_evidence", False)
        else:
            warnings.append({"field": "filing_deadline_rule", "message": "Plan-specific filing deadline is not yet known; no deadline has been assumed"})

    uncertainties = draft.get("uncertainties", [])
    if not isinstance(uncertainties, list):
        error("uncertainties", "Must be a list")
    elif uncertainties:
        error("uncertainties", "Resolve all missing, ambiguous or conflicting information before approval")

    evidence = draft.get("evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
        error("evidence", "Must be an object")
    if kind == "supplement":
        if any(key in draft for key in ("charges", "total", "currency", "service_date", "provider", "reason_for_treatment")):
            error("supplement", "A supplement adds requested documents/notes; it cannot replace the original clinical or charge data")
        if not parent or not parent.get("portal_claim_number"):
            error("parent_claim_id", "Supplements require a previously submitted initial claim")
        elif parent["draft"]["member_id"] != draft.get("member_id"):
            error("member_id", "A supplement must use the original patient's profile")
        check(lambda: require_text(draft.get("note"), "note"), "note")
        refs(evidence.get("note"), "evidence.note")
        return errors, warnings

    if draft.get("parent_claim_id"):
        error("parent_claim_id", "An initial claim cannot have a parent")
    for field in ("member_id", "reason_for_treatment"):
        check(lambda f=field: require_text(draft.get(f), f), field)
    check(lambda: iso_date(draft.get("service_date"), "service_date"), "service_date")
    if not isinstance(draft.get("country"), str) or not re.fullmatch(r"[A-Z]{2}", draft["country"]):
        error("country", "Use the treatment country's two-letter code")
    if not isinstance(draft.get("currency"), str) or not re.fullmatch(r"[A-Z]{3}", draft["currency"]):
        error("currency", "Use the original three-letter currency code; never infer a conversion")
    provider = draft.get("provider", {})
    if not isinstance(provider, dict):
        provider = {}
    for field in ("name", "contact"):
        check(lambda f=field: require_text(provider.get(f), "provider." + f), "provider." + field)
    total = check(lambda: money(draft.get("total"), "total"), "total")
    if total is not None and total <= 0:
        error("total", "Claim total must be greater than zero")
    charges = draft.get("charges", [])
    if not isinstance(charges, list) or not charges:
        error("charges", "Include itemized charges, with receipts linked as evidence rather than additional charges")
        charges = []
    amounts, charge_ids = [], []
    for index, charge in enumerate(charges):
        field = "charges[%d]" % index
        if not isinstance(charge, dict):
            error(field, "Charge must be an object")
            continue
        check(lambda: identifier(charge.get("id"), "charge id"), field + ".id")
        charge_ids.append(charge.get("id"))
        check(lambda: require_text(charge.get("description"), "description"), field + ".description")
        amount = check(lambda: money(charge.get("amount"), field + ".amount"), field + ".amount")
        if amount is not None:
            amounts.append(amount)
        if charge.get("currency", draft.get("currency")) != draft.get("currency"):
            error(field + ".currency", "Separate different currencies into separate drafts")
        refs(charge.get("evidence"), field + ".evidence")
    if len(charge_ids) != len(set(x for x in charge_ids if isinstance(x, str))):
        error("charges", "Charge ids must be unique")
    if total is not None and len(amounts) == len(charges) and sum(amounts, Decimal("0")) != total:
        error("total", "Itemized amounts do not equal the claim total")
    for field in ("member_id", "service_date", "country", "provider.name", "provider.contact",
                  "reason_for_treatment", "currency", "total"):
        refs(evidence.get(field), "evidence." + field)
    refs(draft.get("payment_proof"), "payment_proof")
    if draft.get("filing_deadline"):
        if not policy or not policy.get("filing_deadline_rule"):
            error("filing_deadline", "A calculated filing deadline requires the actual plan-specific rule")
        check(lambda: iso_date(draft["filing_deadline"], "filing_deadline"), "filing_deadline")
        refs(evidence.get("filing_deadline"), "evidence.filing_deadline", False)
        warnings.append({"field": "filing_deadline", "message": "Confirm the calculated deadline against the recorded plan rule; this tool does not adjudicate exceptions"})
    return errors, warnings
