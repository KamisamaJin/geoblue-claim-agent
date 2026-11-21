import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import documents as document_tools
from .common import (ClaimError, atomic_write, canonical, digest, fingerprint, identifier,
                     now, private_dir, require_text, write_json)
from .validation import money, reject_credentials, validate_draft, validate_policy, validate_profile

LOCKED = {"submitting", "submission_unknown", "submitted", "in_process", "action_required",
          "payment_processing", "processed", "duplicate"}
PORTAL_STATES = {"action required": "action_required", "in process": "in_process",
                 "payment processing": "payment_processing", "complete": "processed", "duplicate": "duplicate"}
ARTIFACT_KINDS = {"portal_verification", "submission_receipt", "eob", "status_evidence", "resolution_evidence"}


def load(value):
    return json.loads(value) if value else None


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.data = private_dir(self.root / "data")
        self.inbox = private_dir(self.root / "inbox")
        path = self.data / "claims.sqlite3"
        if path.is_symlink():
            raise ClaimError("Database must not be a symbolic link")
        self.db = sqlite3.connect(str(path), timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ClaimError("Unsupported database version; use the matching application version")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS members(id TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS policies(id TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents(
                id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, original_name TEXT NOT NULL,
                stored_path TEXT NOT NULL, sources TEXT NOT NULL, page_count INTEGER,
                state TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artifacts(
                id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, stored_path TEXT NOT NULL,
                kind TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS claims(
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, member_id TEXT NOT NULL,
                parent_id TEXT REFERENCES claims(id), draft TEXT NOT NULL, draft_hash TEXT NOT NULL,
                status TEXT NOT NULL, approved_hash TEXT, approval_message TEXT,
                duplicate_ack TEXT NOT NULL DEFAULT '{}', portal_claim_number TEXT,
                portal_status TEXT, portal_details TEXT, portal_observed_at TEXT,
                funds_received TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS claims_member ON claims(member_id);
            CREATE INDEX IF NOT EXISTS claims_hash ON claims(draft_hash);
            CREATE TABLE IF NOT EXISTS attempts(
                id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims(id),
                state TEXT NOT NULL, snapshot_hash TEXT NOT NULL, snapshot TEXT NOT NULL,
                proof_artifact_id TEXT REFERENCES artifacts(id), details TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_attempt ON attempts(claim_id)
                WHERE state IN ('open', 'unknown');
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY, entity_id TEXT NOT NULL, action TEXT NOT NULL,
                details TEXT NOT NULL, created_at TEXT NOT NULL);
            PRAGMA user_version=1;
        """)
        path.chmod(0o600)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def event(self, entity, action, details):
        self.db.execute("INSERT INTO events(entity_id,action,details,created_at) VALUES(?,?,?,?)",
                        (entity, action, canonical(details), now()))

    def private_path(self, relative):
        candidate = self.root / relative
        # Check each component, not just the final file, before reading or writing private data.
        if candidate.is_absolute() and not candidate.is_relative_to(self.root):
            raise ClaimError("Private path escaped the project")
        for part in [candidate] + list(candidate.parents):
            if part == self.root:
                break
            if part.is_symlink():
                raise ClaimError("Private paths must not contain symbolic links")
        if not candidate.resolve().is_relative_to(self.data):
            raise ClaimError("Private path escaped the data directory")
        return candidate

    def record(self, table, record_id):
        if table not in {"members", "policies"}:
            raise ClaimError("Unknown record type")
        row = self.db.execute("SELECT body FROM " + table + " WHERE id=?", (record_id,)).fetchone()
        return load(row[0]) if row else None

    def put_record(self, table, body):
        (validate_profile if table == "members" else validate_policy)(body)
        existing = self.record(table, body["id"])
        if existing == body:
            return {"id": body["id"], "changed": False}
        with self.transaction():
            self.db.execute("INSERT INTO " + table + " VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at",
                            (body["id"], canonical(body), now()))
            # In-progress submissions keep their locked snapshot. guard() stops further transmission if inputs changed.
            for row in self.db.execute("SELECT id,member_id,status FROM claims WHERE approved_hash IS NOT NULL").fetchall():
                member = self.record("members", row["member_id"])
                affected = row["member_id"] == body["id"] if table == "members" else member and member.get("policy_id") == body["id"]
                if affected and row["status"] not in LOCKED:
                    self.db.execute("UPDATE claims SET status='ready',approved_hash=NULL,approval_message=NULL,updated_at=? WHERE id=?", (now(), row["id"]))
            self.event(body["id"], table + ".updated", {"previous_hash": fingerprint(existing), "new_hash": fingerprint(body)})
        return {"id": body["id"], "changed": True}

    def records(self, table):
        if table not in {"members", "policies"}:
            raise ClaimError("Unknown record type")
        return [load(row[0]) for row in self.db.execute("SELECT body FROM " + table + " ORDER BY id")]

    def scan(self):
        imported, duplicates, skipped = [], [], []
        for path in sorted(self.inbox.rglob("*")):
            if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != self.root):
                skipped.append({"path": str(path), "reason": "symlink"})
                continue
            if not path.is_file():
                continue
            if path.suffix.lower() not in document_tools.SUPPORTED:
                skipped.append({"path": str(path), "reason": "unsupported_format"})
                continue
            try:
                content = document_tools.file_bytes(path)
                sha = digest(content)
                source = str(path.relative_to(self.root))
                with self.transaction():
                    row = self.db.execute("SELECT * FROM documents WHERE sha256=?", (sha,)).fetchone()
                    if row:
                        sources = load(row["sources"])
                        if source not in sources:
                            sources.append(source)
                            self.db.execute("UPDATE documents SET sources=? WHERE id=?", (canonical(sources), sha))
                        duplicates.append({"document_id": sha, "path": str(path)})
                        continue
                    target = self.private_path("data/documents/" + sha + "/original" + path.suffix.lower())
                    atomic_write(target, content)
                    self.db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,NULL,?)",
                                    (sha, sha, path.name, str(target.relative_to(self.root)), canonical([source]), None, "imported", now()))
                    self.event(sha, "document.imported", {"sha256": sha})
                    imported.append({"document_id": sha, "name": path.name})
            except (ClaimError, OSError) as exc:
                skipped.append({"path": str(path), "reason": str(exc)})
        return {"imported": imported, "duplicates": duplicates, "skipped": skipped}

    def documents(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM documents ORDER BY created_at,id")]

    def intake(self):
        """One compact resumption call; do not render already-associated bills again."""
        scan = self.scan()
        assigned = set()
        claims = self.claims()
        for claim in claims:
            if claim["status"] == "cancelled":
                continue
            assigned.update(claim["draft"].get("attachments", []))
            assigned.update(claim["draft"].get("original_source_documents", []))
            docs, _ = self.source_ids(claim["draft"])
            assigned.update(docs)
        materials, errors = [], []
        for document in self.documents():
            if document["id"] in assigned:
                continue
            try:
                prepared = self.prepare(document["id"])
                materials.append({"document_id": document["id"], "name": document["original_name"],
                                  "text_path": prepared["text_path"], "pages": prepared["pages"]})
            except (ClaimError, OSError) as exc:
                errors.append({"document_id": document["id"], "error": str(exc)})
        return {"new_materials": materials, "errors": errors, "skipped": scan["skipped"],
                "imported_count": len(scan["imported"]), "duplicate_count": len(scan["duplicates"]),
                "pending_claims": [{"claim_id": c["id"], "status": c["status"]} for c in claims
                                   if c["status"] not in LOCKED and c["status"] != "cancelled"],
                "unresolved_attempts": [dict(r) for r in self.db.execute(
                    "SELECT id,claim_id,state FROM attempts WHERE state IN ('open','unknown')")],
                "ok": not errors}

    def prepare(self, document_id):
        row = self.db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if not row:
            raise ClaimError("Unknown document id")
        try:
            manifest = document_tools.prepare(self.private_path(row["stored_path"]), row["sha256"])
        except (ClaimError, OSError) as exc:
            self.db.execute("UPDATE documents SET state='error',page_count=NULL,error=? WHERE id=?", (str(exc), document_id))
            raise ClaimError(str(exc)) from exc
        self.db.execute("UPDATE documents SET state='prepared',page_count=?,error=NULL WHERE id=?", (manifest["page_count"], document_id))
        return {"document_id": document_id, **manifest}

    def add_artifact(self, path, kind):
        if kind not in ARTIFACT_KINDS:
            raise ClaimError("Unknown artifact kind")
        path = Path(path)
        content = document_tools.file_bytes(path)
        sha = digest(content)
        artifact_id = "artifact_" + sha
        existing = self.db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if existing:
            if digest(document_tools.file_bytes(self.private_path(existing["stored_path"]))) != sha:
                raise ClaimError("Stored evidence artifact changed; restore it before use")
            return dict(existing)
        suffix = path.suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
            suffix = ".bin"
        target = self.private_path("data/artifacts/" + sha + suffix)
        atomic_write(target, content)
        with self.transaction():
            self.db.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?,?)",
                            (artifact_id, sha, str(target.relative_to(self.root)), kind, now()))
            self.event(artifact_id, "artifact.saved", {"kind": kind, "sha256": sha})
        return dict(self.db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone())

    def claim(self, claim_id):
        row = self.db.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        if not row:
            raise ClaimError("Unknown claim: " + str(claim_id))
        result = dict(row)
        for field in ("draft", "duplicate_ack", "portal_details", "funds_received"):
            result[field] = load(result[field])
        return result

    def upload_path(self, document_id):
        """A hash-verified archive copy with a distinct, readable upload filename."""
        row = self.db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if not row:
            raise ClaimError("Unknown upload document")
        original = self.private_path(row["stored_path"])
        content = document_tools.file_bytes(original)
        if digest(content) != row["sha256"]:
            raise ClaimError("Stored original changed; stop uploading")
        filename = Path(row["original_name"]).name
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
        if not filename or filename.startswith("."):
            filename = document_id[:12] + original.suffix
        target = self.private_path(str((original.parent / "upload" / filename).relative_to(self.root)))
        if target.exists():
            if digest(document_tools.file_bytes(target)) != row["sha256"]:
                raise ClaimError("Named upload copy changed; stop uploading")
        else:
            atomic_write(target, content)
        return target

    def claims(self):
        return [self.claim(row[0]) for row in self.db.execute("SELECT id FROM claims ORDER BY created_at,id")]

    def put_draft(self, draft, claim_id=None):
        reject_credentials(draft)
        if not isinstance(draft, dict):
            raise ClaimError("Draft must be an object")
        draft = json.loads(canonical(draft))
        draft.setdefault("kind", "initial")
        draft.setdefault("uncertainties", [])
        draft.setdefault("destination_origin", "https://members.bcbsglobalsolutions.com")
        require_text(draft.get("member_id"), "member_id")
        if draft["kind"] not in {"initial", "supplement"}:
            raise ClaimError("kind must be initial or supplement")
        parent_id = draft.get("parent_claim_id")
        if draft["kind"] == "supplement":
            parent = self.claim(parent_id)
            if parent["kind"] != "initial" or not parent["portal_claim_number"] or parent["member_id"] != draft["member_id"]:
                raise ClaimError("Supplement must refer to a submitted initial claim for the same patient")
        elif parent_id:
            raise ClaimError("Initial claim cannot have a parent")
        body_hash = fingerprint(draft)
        with self.transaction():
            if claim_id:
                old = self.claim(claim_id)
                if old["status"] in LOCKED:
                    raise ClaimError("Submitted or unresolved attempts are immutable; reconcile the attempt or prepare a supplement")
                if old["kind"] != draft["kind"] or old["parent_id"] != parent_id:
                    raise ClaimError("Cannot change a draft's kind or parent")
                if old["draft"] == draft:
                    return {"claim_id": claim_id, "existing": True}
                self.db.execute("UPDATE claims SET member_id=?,draft=?,draft_hash=?,status='needs_info',approved_hash=NULL,approval_message=NULL,duplicate_ack='{}',updated_at=? WHERE id=?",
                                (draft["member_id"], canonical(draft), body_hash, now(), claim_id))
            else:
                existing = self.db.execute("SELECT id FROM claims WHERE draft_hash=? AND status!='cancelled'", (body_hash,)).fetchone()
                if existing:
                    return {"claim_id": existing[0], "existing": True}
                claim_id = "claim_" + uuid.uuid4().hex[:16]
                self.db.execute("INSERT INTO claims(id,kind,member_id,parent_id,draft,draft_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'needs_info',?,?)",
                                (claim_id, draft["kind"], draft["member_id"], parent_id, canonical(draft), body_hash, now(), now()))
            self.event(claim_id, "draft.saved", {"draft_hash": body_hash})
        return {"claim_id": claim_id, "existing": False}

    def duplicate_candidates(self, claim):
        if claim["kind"] != "initial":
            return []
        draft = claim["draft"]
        provider = draft.get("provider", {})
        name = str(provider.get("name", "")).casefold().strip() if isinstance(provider, dict) else ""

        def invoices(d):
            return {str(c["invoice_number"]).strip().casefold() for c in d.get("charges", [])
                    if isinstance(c, dict) and c.get("invoice_number")} if isinstance(d.get("charges", []), list) else set()

        result = []
        for other in self.claims():
            if other["id"] == claim["id"] or other["kind"] != "initial" or other["status"] == "cancelled" or other["member_id"] != claim["member_id"]:
                continue
            d = other["draft"]
            p = d.get("provider", {})
            other_name = str(p.get("name", "")).casefold().strip() if isinstance(p, dict) else ""
            if not name or name != other_name:
                continue
            same_invoice = bool(invoices(draft) & invoices(d))
            try:
                same_charge = (draft.get("service_date") == d.get("service_date") and draft.get("currency") == d.get("currency")
                               and money(draft.get("total"), "total") == money(d.get("total"), "total"))
            except ClaimError:
                same_charge = False
            if same_invoice or same_charge:
                result.append({"claim_id": other["id"], "draft_hash": other["draft_hash"],
                               "reason": "same_invoice" if same_invoice else "same_patient_provider_date_amount"})
        return result

    def distinguish(self, claim_id, other_ids, user_message):
        require_text(user_message, "Actual user explanation")
        with self.transaction():
            claim = self.claim(claim_id)
            if claim["status"] in LOCKED:
                raise ClaimError("Cannot change duplicate review after submission begins")
            candidates = {x["claim_id"]: x for x in self.duplicate_candidates(claim)}
            ack = claim["duplicate_ack"]
            for other_id in other_ids:
                if other_id not in candidates:
                    raise ClaimError("Not a current duplicate candidate: " + other_id)
                ack[other_id] = {"draft_hash": candidates[other_id]["draft_hash"], "user_message": user_message}
            self.db.execute("UPDATE claims SET duplicate_ack=?,approved_hash=NULL,approval_message=NULL,status='needs_info',updated_at=? WHERE id=?",
                            (canonical(ack), now(), claim_id))
            self.event(claim_id, "duplicate.distinguished", {"other_ids": other_ids, "user_message": user_message})
        return {"claim_id": claim_id, "distinct_from": other_ids}

    @staticmethod
    def source_ids(value):
        docs, artifacts = set(), set()
        if isinstance(value, dict):
            if value.get("source") == "document" and isinstance(value.get("document_id"), str):
                docs.add(value["document_id"])
            if value.get("source") == "artifact" and isinstance(value.get("artifact_id"), str):
                artifacts.add(value["artifact_id"])
            for child in value.values():
                d, a = Store.source_ids(child)
                docs.update(d)
                artifacts.update(a)
        elif isinstance(value, list):
            for child in value:
                d, a = Store.source_ids(child)
                docs.update(d)
                artifacts.update(a)
        return docs, artifacts

    def inspection(self, claim_id):
        claim = self.claim(claim_id)
        member = self.record("members", claim["member_id"])
        policy = self.record("policies", member.get("policy_id")) if member else None
        documents = {d["id"]: d for d in self.documents()}
        artifacts = {r["id"]: dict(r) for r in self.db.execute("SELECT * FROM artifacts")}
        parent = self.claim(claim["parent_id"]) if claim["parent_id"] else None
        errors, warnings = validate_draft(claim["draft"], member, policy, documents, artifacts, parent)
        doc_ids, artifact_ids = self.source_ids([claim["draft"], member, policy])
        attachments = claim["draft"].get("attachments", [])
        if isinstance(attachments, list):
            doc_ids.update(x for x in attachments if isinstance(x, str))
        evidence_hashes = {}
        for kind, ids, records in (("document", doc_ids, documents), ("artifact", artifact_ids, artifacts)):
            for item_id in sorted(ids):
                record = records.get(item_id)
                if record:
                    try:
                        actual = digest(document_tools.file_bytes(self.private_path(record["stored_path"])))
                        if actual != record["sha256"]:
                            raise ClaimError("Original or evidence file changed")
                        evidence_hashes[kind + ":" + item_id] = actual
                        if kind == "document" and claim["status"] in {"needs_info", "ready", "approved", "submitting", "submission_unknown"}:
                            for source in load(record["sources"]):
                                incoming = self.root / source
                                linked = any(p.is_symlink() for p in [incoming] + list(incoming.parents) if p != self.root)
                                if linked or (incoming.exists() and digest(document_tools.file_bytes(incoming)) != actual):
                                    raise ClaimError("Inbox source was replaced after import; review the revised material and update this draft")
                    except (ClaimError, OSError) as exc:
                        errors.append({"field": kind + ":" + item_id, "message": str(exc)})
        duplicates = self.duplicate_candidates(claim)
        duplicate_ids = {d["claim_id"] for d in duplicates}
        for other in self.claims():
            if other["id"] == claim_id or other["status"] not in {"submitting", "submission_unknown"}:
                continue
            other_attachments = other["draft"].get("attachments", [])
            shared_docs = (isinstance(attachments, list) and isinstance(other_attachments, list)
                           and any(isinstance(x, str) and x in other_attachments for x in attachments))
            same_parent = bool(claim["parent_id"] and claim["parent_id"] == other["parent_id"])
            if (other["id"] in duplicate_ids or same_parent
                    or (claim["member_id"] == other["member_id"] and shared_docs)):
                errors.append({"field": "unresolved_submission", "message": "Reconcile the related submission before creating a replacement: " + other["id"]})
        for duplicate in duplicates:
            ack = claim["duplicate_ack"].get(duplicate["claim_id"], {})
            if ack.get("draft_hash") != duplicate["draft_hash"]:
                errors.append({"field": "duplicate", "message": "Resolve possible duplicate with the user: " + duplicate["claim_id"]})
        snapshot = {"schema_version": 1, "claim_id": claim_id, "kind": claim["kind"],
                    "draft": claim["draft"], "member": member, "policy": policy,
                    "parent_portal_claim_number": parent["portal_claim_number"] if parent else None,
                    "evidence_hashes": evidence_hashes, "duplicate_review": claim["duplicate_ack"]}
        return {"claim_id": claim_id, "status": claim["status"], "errors": errors, "warnings": warnings,
                "duplicate_candidates": duplicates, "snapshot": snapshot, "review_token": fingerprint(snapshot)}

    def review(self, claim_id):
        with self.transaction():
            result = self.inspection(claim_id)
            claim = self.claim(claim_id)
            if claim["status"] not in LOCKED and claim["status"] != "cancelled":
                status = "needs_info" if result["errors"] else "ready"
                valid_approval = claim["approved_hash"] == result["review_token"] and not result["errors"]
                if valid_approval:
                    status = "approved"
                else:
                    self.db.execute("UPDATE claims SET approved_hash=NULL,approval_message=NULL WHERE id=?", (claim_id,))
                self.db.execute("UPDATE claims SET status=?,updated_at=? WHERE id=?", (status, now(), claim_id))
                result["status"] = status
        result["ready_for_approval"] = not result["errors"] and result["status"] == "ready"
        return result

    def approve(self, claim_id, token, user_message):
        require_text(user_message, "Actual user confirmation")
        with self.transaction():
            result = self.inspection(claim_id)
            if result["status"] in LOCKED or result["status"] == "cancelled":
                raise ClaimError("Cannot approve a submitted, cancelled or unresolved claim")
            if result["errors"]:
                raise ClaimError("Draft has unresolved validation errors; run review")
            if result["review_token"] != token:
                raise ClaimError("Review changed; show the current review to the user and obtain new confirmation")
            snapshot_path = self.private_path("data/claims/" + claim_id + "/approved/" + token + ".json")
            write_json(snapshot_path, result["snapshot"])
            self.db.execute("UPDATE claims SET status='approved',approved_hash=?,approval_message=?,updated_at=? WHERE id=?",
                            (token, user_message, now(), claim_id))
            self.event(claim_id, "claim.approved", {"review_token": token, "user_message": user_message})
        return {"claim_id": claim_id, "approved_hash": token}

    def authorize_entry(self, claim_id, token, user_message):
        """Authorize preparation separately from the later final-submission approval."""
        require_text(user_message, "Actual user authorization for website entry/upload only")
        with self.transaction():
            result = self.inspection(claim_id)
            if result["status"] in LOCKED or result["status"] == "cancelled":
                raise ClaimError("Cannot authorize entry for a submitted, cancelled or unresolved claim")
            blocking = [e for e in result["errors"] if e["field"] not in {"reimbursement", "reimbursement.evidence"}]
            if blocking or result["review_token"] != token:
                raise ClaimError("Clinical information or evidence changed or is incomplete; show a current review and obtain authorization")
            path = self.private_path("data/claims/" + claim_id + "/entry-only/" + token + ".json")
            write_json(path, result["snapshot"])
            self.event(claim_id, "entry.authorized", {"review_token": token, "user_message": user_message,
                                                      "scope": "form_entry_and_upload_only", "snapshot": result["snapshot"]})
        return {"claim_id": claim_id, "entry_token": token, "may_submit": False}

    def entry_guard(self, claim_id):
        with self.transaction():
            row = self.db.execute("SELECT details FROM events WHERE entity_id=? AND action='entry.authorized' ORDER BY id DESC LIMIT 1", (claim_id,)).fetchone()
            if not row:
                raise ClaimError("No actual user authorization for website form entry/upload is recorded")
            authorization = load(row[0])
            result = self.inspection(claim_id)
            blocking = [e for e in result["errors"] if e["field"] not in {"reimbursement", "reimbursement.evidence"}]
            if (blocking or result["review_token"] != authorization["review_token"]
                    or result["status"] in LOCKED or result["status"] == "cancelled"):
                raise ClaimError("Entry authorization is stale or blocked; stop entry and show a new review")
            attachments = []
            for doc_id in result["snapshot"]["draft"]["attachments"]:
                attachments.append({"document_id": doc_id, "path": str(self.upload_path(doc_id))})
        return {"claim_id": claim_id, "snapshot": result["snapshot"], "attachments": attachments,
                "may_enter_or_upload": True, "may_submit": False}

    def begin(self, claim_id):
        with self.transaction():
            claim = self.claim(claim_id)
            result = self.inspection(claim_id)
            if claim["status"] != "approved" or claim["approved_hash"] != result["review_token"] or result["errors"]:
                raise ClaimError("The complete final snapshot must be confirmed by the user before submission begins; use entry authorization for preparation")
            existing = self.db.execute("SELECT id FROM attempts WHERE claim_id=? AND state IN ('open','unknown')", (claim_id,)).fetchone()
            if existing:
                raise ClaimError("An unresolved submission exists; reconcile it before retrying")
            attempt_id = "attempt_" + uuid.uuid4().hex[:16]
            self.db.execute("INSERT INTO attempts(id,claim_id,state,snapshot_hash,snapshot,created_at,updated_at) VALUES(?,?,'open',?,?,?,?)",
                            (attempt_id, claim_id, result["review_token"], canonical(result["snapshot"]), now(), now()))
            self.db.execute("UPDATE claims SET status='submitting',updated_at=? WHERE id=?", (now(), claim_id))
            self.event(claim_id, "submission.begun", {"attempt_id": attempt_id, "snapshot_hash": result["review_token"]})
        return self.guard(attempt_id)

    def attempt(self, attempt_id):
        row = self.db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if not row:
            raise ClaimError("Unknown attempt")
        result = dict(row)
        result["snapshot"] = load(result["snapshot"])
        result["details"] = load(result["details"])
        return result

    def guard(self, attempt_id):
        failure = None
        with self.transaction():
            attempt = self.attempt(attempt_id)
            if attempt["state"] != "open":
                raise ClaimError("Attempt is not open; reconcile an unknown result rather than transmitting again")
            result = self.inspection(attempt["claim_id"])
            if result["errors"] or attempt["snapshot_hash"] != result["review_token"]:
                failure = "Inputs changed after submission began; stop transmitting and reconcile any possible external effects"
                self._unknown(attempt_id, failure)
        if failure:
            raise ClaimError(failure)
        attachments = []
        for doc_id in attempt["snapshot"]["draft"]["attachments"]:
            attachments.append({"document_id": doc_id, "path": str(self.upload_path(doc_id))})
        return {"attempt_id": attempt_id, "claim_id": attempt["claim_id"], "snapshot": attempt["snapshot"],
                "attachments": attachments, "may_transmit": True}

    def _unknown(self, attempt_id, reason):
        attempt = self.attempt(attempt_id)
        self.db.execute("UPDATE attempts SET state='unknown',details=?,updated_at=? WHERE id=?", (canonical({"reason": reason}), now(), attempt_id))
        self.db.execute("UPDATE claims SET status='submission_unknown',updated_at=? WHERE id=?", (now(), attempt["claim_id"]))
        self.event(attempt["claim_id"], "submission.unknown", {"attempt_id": attempt_id, "reason": reason})

    def unknown(self, attempt_id, reason):
        require_text(reason, "reason")
        with self.transaction():
            if self.attempt(attempt_id)["state"] not in {"open", "unknown"}:
                raise ClaimError("Attempt already resolved")
            self._unknown(attempt_id, reason)
        return {"attempt_id": attempt_id, "state": "unknown", "retry_allowed": False}

    def recover(self):
        with self.transaction():
            for row in self.db.execute("SELECT id FROM attempts WHERE state='open'").fetchall():
                self._unknown(row[0], "Previous run ended without a recorded outcome; website reconciliation required")
        return {"unresolved_attempts": [dict(r) for r in self.db.execute(
            "SELECT id,claim_id,state,created_at FROM attempts WHERE state='unknown' ORDER BY created_at")]}

    def proof(self, artifact_id):
        row = self.db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not row or digest(document_tools.file_bytes(self.private_path(row["stored_path"]))) != row["sha256"]:
            raise ClaimError("A saved, intact website evidence artifact is required")
        return row

    def finish(self, attempt_id, claim_number, artifact_id):
        require_text(claim_number, "Observed portal claim number")
        self.proof(artifact_id)
        with self.transaction():
            attempt = self.attempt(attempt_id)
            claim = self.claim(attempt["claim_id"])
            if attempt["state"] == "succeeded":
                if claim["portal_claim_number"] != claim_number:
                    raise ClaimError("Attempt already succeeded with a different claim number")
                return {"claim_id": claim["id"], "portal_claim_number": claim_number, "existing": True}
            if attempt["state"] not in {"open", "unknown"}:
                raise ClaimError("Attempt is already resolved as not submitted")
            if claim["parent_id"] and self.claim(claim["parent_id"])["portal_claim_number"] != claim_number:
                raise ClaimError("A supplement must reference the existing official claim number, not a new claim")
            if not claim["parent_id"]:
                account = attempt["snapshot"]["member"]["account_label"]
                for other in self.claims():
                    if other["kind"] == "initial" and other["id"] != claim["id"] and other["portal_claim_number"] == claim_number:
                        member = self.record("members", other["member_id"])
                        if member and member["account_label"] == account:
                            raise ClaimError("This official claim number is already associated with another claim; reconcile before recording success")
            # Record what was actually transmitted, even if local files changed after the final click.
            # The attempt snapshot remains the authoritative submitted payload.
            self.db.execute("UPDATE attempts SET state='succeeded',proof_artifact_id=?,updated_at=? WHERE id=?", (artifact_id, now(), attempt_id))
            self.db.execute("UPDATE claims SET status='submitted',portal_claim_number=?,updated_at=? WHERE id=?", (claim_number, now(), claim["id"]))
            self.event(claim["id"], "submission.succeeded", {"attempt_id": attempt_id, "portal_claim_number": claim_number, "artifact_id": artifact_id})
        return {"claim_id": claim["id"], "portal_claim_number": claim_number, "existing": False}

    def finish_eclaim(self, attempt_id, eclaim_id, artifact_id):
        """The immediate eClaim receipt precedes the insurer's separate Claim Number."""
        require_text(eclaim_id, "Observed eClaim ID")
        if self.proof(artifact_id)["kind"] != "submission_receipt":
            raise ClaimError("An actual submission receipt is required")
        with self.transaction():
            attempt = self.attempt(attempt_id)
            claim = self.claim(attempt["claim_id"])
            details = claim["portal_details"] or {}
            if claim["kind"] != "initial":
                raise ClaimError("Use the existing official claim number for supplements")
            if attempt["state"] == "succeeded":
                if details.get("eclaim_id") != eclaim_id:
                    raise ClaimError("Attempt already succeeded with a different eClaim ID")
                return {"claim_id": claim["id"], "eclaim_id": eclaim_id, "existing": True}
            if attempt["state"] not in {"open", "unknown"}:
                raise ClaimError("Attempt is already resolved as not submitted")
            account = attempt["snapshot"]["member"]["account_label"]
            for other in self.claims():
                if other["id"] != claim["id"] and (other["portal_details"] or {}).get("eclaim_id") == eclaim_id:
                    member = self.record("members", other["member_id"])
                    if member and member["account_label"] == account:
                        raise ClaimError("eClaim ID already belongs to another local claim; reconcile first")
            details.update(eclaim_id=eclaim_id, receipt_artifact_id=artifact_id)
            self.db.execute("UPDATE attempts SET state='succeeded',proof_artifact_id=?,updated_at=? WHERE id=?",
                            (artifact_id, now(), attempt_id))
            self.db.execute("UPDATE claims SET status='submitted',portal_details=?,updated_at=? WHERE id=?",
                            (canonical(details), now(), claim["id"]))
            self.event(claim["id"], "submission.succeeded", {"attempt_id": attempt_id, **details})
        return {"claim_id": claim["id"], "eclaim_id": eclaim_id, "portal_claim_number": claim["portal_claim_number"], "existing": False}

    def resolve_not_submitted(self, attempt_id, artifact_id, user_message, explanation):
        require_text(user_message, "User confirmation that retry is appropriate")
        require_text(explanation, "Definitive website/customer-service evidence explanation")
        self.proof(artifact_id)
        with self.transaction():
            attempt = self.attempt(attempt_id)
            if attempt["state"] not in {"open", "unknown"}:
                raise ClaimError("Attempt already resolved")
            details = {"user_message": user_message, "explanation": explanation}
            self.db.execute("UPDATE attempts SET state='not_submitted',proof_artifact_id=?,details=?,updated_at=? WHERE id=?",
                            (artifact_id, canonical(details), now(), attempt_id))
            self.db.execute("UPDATE claims SET status='needs_info',approved_hash=NULL,approval_message=NULL,updated_at=? WHERE id=?", (now(), attempt["claim_id"]))
            self.event(attempt["claim_id"], "submission.confirmed_not_submitted", {"attempt_id": attempt_id, **details})
        return {"claim_id": attempt["claim_id"], "requires_new_approval": True}

    def portal_update(self, claim_id, details):
        reject_credentials(details)
        raw = require_text(details.get("raw_status"), "raw_status")
        evidence = require_text(details.get("artifact_id"), "artifact_id")
        self.proof(evidence)
        timestamp = details.get("observed_at")
        try:
            if not isinstance(timestamp, str):
                raise ValueError
            observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if observed.tzinfo is None:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ClaimError("observed_at must be an ISO timestamp with timezone") from exc
        amounts = details.get("amounts", [])
        if not isinstance(amounts, list):
            raise ClaimError("Observed amounts must be a list")
        for amount in amounts:
            if not isinstance(amount, dict):
                raise ClaimError("Each observed amount must be an object")
            money(amount.get("amount"), "observed amount")
            if not re.fullmatch(r"[A-Z]{3}", str(amount.get("currency", ""))):
                raise ClaimError("Each observed amount needs its actual three-letter currency")
            require_text(amount.get("label"), "Observed amount label")
        eobs = details.get("eob_artifact_ids", [])
        if not isinstance(eobs, list) or any(not isinstance(x, str) for x in eobs):
            raise ClaimError("eob_artifact_ids must be a list of saved evidence ids")
        for eob in eobs:
            self.proof(eob)
        state = PORTAL_STATES.get(" ".join(raw.casefold().split()))
        with self.transaction():
            claim = self.claim(claim_id)
            previous = claim["portal_details"] or {}
            if (not (claim["portal_claim_number"] or previous.get("eclaim_id")) or claim["kind"] != "initial"):
                raise ClaimError("Status updates require a submitted initial claim; reconcile unresolved attempts first")
            eclaim_matches = bool(previous.get("eclaim_id") and details.get("eclaim_id") == previous["eclaim_id"])
            observed_number = details.get("portal_claim_number")
            if (details.get("eclaim_id") and not eclaim_matches
                    or observed_number and claim["portal_claim_number"] and observed_number != claim["portal_claim_number"]
                    or not eclaim_matches and (not claim["portal_claim_number"] or observed_number != claim["portal_claim_number"])):
                raise ClaimError("Status evidence must match the recorded official claim number")
            if observed_number:
                require_text(observed_number, "Observed portal claim number")
                account = self.record("members", claim["member_id"])["account_label"]
                for other in self.claims():
                    if other["kind"] == "initial" and other["id"] != claim_id and other["portal_claim_number"] == observed_number:
                        if self.record("members", other["member_id"])["account_label"] == account:
                            raise ClaimError("Official claim number belongs to another local claim")
            if claim["portal_observed_at"] and observed < datetime.fromisoformat(claim["portal_observed_at"]):
                raise ClaimError("Refusing to overwrite a newer portal observation with an older one")
            details = {**previous, **details}
            if previous.get("eclaim_id"):
                details["eclaim_id"] = previous["eclaim_id"]
            # Unknown status labels are retained verbatim without inventing a terminal interpretation.
            self.db.execute("UPDATE claims SET status=?,portal_status=?,portal_details=?,portal_observed_at=?,portal_claim_number=?,updated_at=? WHERE id=?",
                            (state or claim["status"], raw, canonical(details), observed.isoformat(), observed_number or claim["portal_claim_number"], now(), claim_id))
            self.event(claim_id, "portal.observed", {"raw_status": raw, "artifact_id": evidence})
        return {"claim_id": claim_id, "status": state or claim["status"], "unrecognized_status": state is None,
                "funds_received_confirmed": bool(claim["funds_received"])}

    def confirm_received(self, claim_id, user_message, amount=None, currency=None):
        require_text(user_message, "Actual user confirmation of receipt")
        if (amount is None) != (currency is None):
            raise ClaimError("Provide both actual received amount and currency, or neither")
        if amount is not None:
            if money(amount, "Received amount") <= 0 or not re.fullmatch(r"[A-Z]{3}", str(currency)):
                raise ClaimError("Actual received amount must be positive and have its actual currency")
        with self.transaction():
            claim = self.claim(claim_id)
            if not claim["portal_claim_number"] or claim["kind"] != "initial":
                raise ClaimError("Receipt confirmation requires a submitted initial claim")
            body = {"confirmed_at": now(), "user_message": user_message, "amount": amount, "currency": currency}
            self.db.execute("UPDATE claims SET funds_received=?,updated_at=? WHERE id=?", (canonical(body), now(), claim_id))
            self.event(claim_id, "funds.user_confirmed", body)
        return {"claim_id": claim_id, "funds_received_confirmed": True}

    def cancel(self, claim_id, reason):
        require_text(reason, "reason")
        with self.transaction():
            claim = self.claim(claim_id)
            if claim["status"] in LOCKED:
                raise ClaimError("This only cancels local unsubmitted drafts; it cannot retract a submission")
            self.db.execute("UPDATE claims SET status='cancelled',approved_hash=NULL,approval_message=NULL,updated_at=? WHERE id=?", (now(), claim_id))
            self.event(claim_id, "draft.cancelled", {"reason": reason})
        return {"claim_id": claim_id, "status": "cancelled"}

    def history(self, entity_id):
        return [{**dict(row), "details": load(row["details"])} for row in self.db.execute(
            "SELECT * FROM events WHERE entity_id=? ORDER BY id", (entity_id,))]
