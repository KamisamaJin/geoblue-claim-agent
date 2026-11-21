import argparse
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from .common import ClaimError, read_json
from .store import Store


def parser():
    p = argparse.ArgumentParser(description="Local GeoBlue claim helpers; no command sends data to the insurer.")
    p.add_argument("--root", type=Path, default=Path.cwd(), help="Project directory (default: current directory)")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create private inbox and ledger")
    commands.add_parser("doctor", help="Check local prerequisites without modifying records")
    commands.add_parser("install-skill", help="Link this project's skill into .agents/skills")
    commands.add_parser("scan", help="Copy and hash new inbox files; leave originals unchanged")
    commands.add_parser("intake", help="Scan, prepare unassigned bills and show resumable work in one call")
    commands.add_parser("recover", help="Mark interrupted attempts unknown; use only at the start of a new run")
    commands.add_parser("status", help="Show compact claim and unresolved-attempt summaries")

    for noun in ("member", "policy"):
        group = commands.add_parser(noun).add_subparsers(dest="action", required=True)
        group.add_parser("list")
        put = group.add_parser("put")
        put.add_argument("--file", type=Path, required=True)
    docs = commands.add_parser("document").add_subparsers(dest="action", required=True)
    docs.add_parser("list")
    prep = docs.add_parser("prepare")
    choice = prep.add_mutually_exclusive_group(required=True)
    choice.add_argument("--id")
    choice.add_argument("--all", action="store_true")
    artifact = commands.add_parser("artifact").add_subparsers(dest="action", required=True)
    artifact.add_parser("list")
    add = artifact.add_parser("add")
    add.add_argument("--file", type=Path, required=True)
    add.add_argument("--kind", required=True, choices=["portal_verification", "submission_receipt", "eob", "status_evidence", "resolution_evidence"])

    drafts = commands.add_parser("draft").add_subparsers(dest="action", required=True)
    put = drafts.add_parser("put")
    put.add_argument("--file", type=Path, required=True)
    put.add_argument("--claim", help="Update this unsubmitted draft rather than creating a new one")
    for action in ("show", "review", "approve", "authorize-entry", "entry-guard", "distinguish", "cancel"):
        command = drafts.add_parser(action)
        command.add_argument("claim_id")
        if action in {"approve", "authorize-entry"}:
            command.add_argument("--token", required=True)
            command.add_argument("--user-message", required=True, help="Actual user approval from this chat")
        elif action == "distinguish":
            command.add_argument("--other", nargs="+", required=True)
            command.add_argument("--user-message", required=True)
        elif action == "cancel":
            command.add_argument("--reason", required=True)

    submissions = commands.add_parser("submission").add_subparsers(dest="action", required=True)
    begin = submissions.add_parser("begin")
    begin.add_argument("claim_id")
    for action in ("show", "guard", "unknown", "finish", "finish-eclaim", "not-submitted"):
        command = submissions.add_parser(action)
        command.add_argument("attempt_id")
        if action == "unknown":
            command.add_argument("--reason", required=True)
        if action in {"finish", "finish-eclaim", "not-submitted"}:
            command.add_argument("--artifact", required=True)
        if action == "finish":
            command.add_argument("--claim-number", required=True)
        elif action == "finish-eclaim":
            command.add_argument("--eclaim-id", required=True)
        elif action == "not-submitted":
            command.add_argument("--user-message", required=True)
            command.add_argument("--explanation", required=True)

    portal = commands.add_parser("portal-update")
    portal.add_argument("claim_id")
    portal.add_argument("--file", type=Path, required=True)
    received = commands.add_parser("received")
    received.add_argument("claim_id")
    received.add_argument("--user-message", required=True)
    received.add_argument("--amount")
    received.add_argument("--currency")
    history = commands.add_parser("history")
    history.add_argument("entity_id")
    return p


def install_skill(root):
    root = root.resolve()
    source = root / "skills/geoblue-claims"
    if not (source / "SKILL.md").is_file():
        raise ClaimError("Skill source is missing from this project")
    target = root / ".agents/skills/geoblue-claims"
    if target.is_symlink() and target.resolve() == source:
        return {"skill_path": str(target), "existing": True}
    if target.exists() or target.is_symlink():
        raise ClaimError("A different skill already exists at the installation path; it was not overwritten")
    if (root / ".agents").is_symlink() or target.parent.is_symlink():
        raise ClaimError("Skill installation parents must not be symbolic links")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(Path("../../skills/geoblue-claims"), target_is_directory=True)
    return {"skill_path": str(target), "existing": False}


def execute(store, args):
    cmd = args.command
    if cmd == "init":
        from .common import private_dir
        private_dir(store.data / "inputs")
        return {"root": str(store.root), "inbox": str(store.inbox), "data": str(store.data), "schema_version": 1}
    if cmd == "scan":
        return store.scan()
    if cmd == "intake":
        return store.intake()
    if cmd == "recover":
        return store.recover()
    if cmd in {"member", "policy"}:
        table = "members" if cmd == "member" else "policies"
        return store.records(table) if args.action == "list" else store.put_record(table, read_json(args.file))
    if cmd == "document":
        if args.action == "list":
            return store.documents()
        if args.id:
            return store.prepare(args.id)
        prepared, errors = [], []
        for document in store.documents():
            try:
                prepared.append(store.prepare(document["id"]))
            except (ClaimError, OSError) as exc:
                errors.append({"document_id": document["id"], "error": str(exc)})
        return {"prepared": prepared, "errors": errors, "ok": not errors}
    if cmd == "artifact":
        return ([dict(r) for r in store.db.execute("SELECT * FROM artifacts ORDER BY created_at,id")]
                if args.action == "list" else store.add_artifact(args.file, args.kind))
    if cmd == "draft":
        action = args.action
        if action == "put":
            return store.put_draft(read_json(args.file), args.claim)
        if action == "show":
            return store.claim(args.claim_id)
        if action == "review":
            return store.review(args.claim_id)
        if action == "approve":
            return store.approve(args.claim_id, args.token, args.user_message)
        if action == "authorize-entry":
            return store.authorize_entry(args.claim_id, args.token, args.user_message)
        if action == "entry-guard":
            return store.entry_guard(args.claim_id)
        if action == "distinguish":
            return store.distinguish(args.claim_id, args.other, args.user_message)
        return store.cancel(args.claim_id, args.reason)
    if cmd == "submission":
        action = args.action
        if action == "begin":
            return store.begin(args.claim_id)
        if action == "show":
            return store.attempt(args.attempt_id)
        if action == "guard":
            return store.guard(args.attempt_id)
        if action == "unknown":
            return store.unknown(args.attempt_id, args.reason)
        if action == "finish":
            return store.finish(args.attempt_id, args.claim_number, args.artifact)
        if action == "finish-eclaim":
            return store.finish_eclaim(args.attempt_id, args.eclaim_id, args.artifact)
        return store.resolve_not_submitted(args.attempt_id, args.artifact, args.user_message, args.explanation)
    if cmd == "portal-update":
        return store.portal_update(args.claim_id, read_json(args.file))
    if cmd == "received":
        return store.confirm_received(args.claim_id, args.user_message, args.amount, args.currency)
    if cmd == "history":
        return store.history(args.entity_id)
    if cmd == "status":
        claims = []
        for claim in store.claims():
            draft = claim["draft"]
            claims.append({"claim_id": claim["id"], "kind": claim["kind"], "member_id": claim["member_id"],
                           "service_date": draft.get("service_date"), "total": draft.get("total"), "currency": draft.get("currency"),
                           "status": claim["status"], "portal_claim_number": claim["portal_claim_number"],
                           "eclaim_id": (claim["portal_details"] or {}).get("eclaim_id"),
                           "portal_status": claim["portal_status"], "last_checked_at": claim["portal_observed_at"],
                           "funds_received_confirmed": bool(claim["funds_received"])})
        return {"claims": claims, "unresolved_attempts": [dict(r) for r in store.db.execute(
            "SELECT id,claim_id,state FROM attempts WHERE state IN ('open','unknown') ORDER BY created_at")]}
    raise ClaimError("Unknown command")


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            tools = {name: shutil.which(name) for name in ("pdfinfo", "pdftotext", "pdftoppm", "sips")}
            result = {"python": sys.version.split()[0], "tools": tools,
                      "pdf_ready": all(tools[n] for n in ("pdfinfo", "pdftotext", "pdftoppm")),
                      "heic_ready": bool(tools["sips"]), "sends_data_to_insurer": False}
        elif args.command == "install-skill":
            result = install_skill(args.root)
        else:
            with Store(args.root) as store:
                result = execute(store, args)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if isinstance(result, dict) and result.get("ok") is False else 0
    except (ClaimError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
