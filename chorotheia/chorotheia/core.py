"""Orchestration, checkpoints, lifecycle, audit and conservative repair."""
import datetime
import json
import re
from pathlib import Path
import uuid
from . import __version__
from .guard import Tree, SafetyError, Transaction, digest, encode, lock
from . import bootstrap
from .memory import clean, redact, git_state, restore as load_context, bounded


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def receipt_path(root):
    return "projects/by-root/" + digest(str(root).encode()) + ".json"


def receipt(tree, home, m, mh):
    try:
        r = json.loads(home.read(receipt_path(tree.root)))
    except FileNotFoundError:
        raise SafetyError("No private ownership receipt: portable notes are readable, mutations require local initialization evidence")
    if r.get("manifest_sha256") != mh or r.get("project_id") != m["project_id"]:
        raise SafetyError("Manifest differs from private receipt; refusing untrusted ownership claims. Inspect pending transactions / backups")
    return r


def save_manifest(tree, home, tx, m, rel, mh):
    data = encode(m)
    tx.put(rel, data, mh)
    rp = receipt_path(tree.root)
    old = home.hash(rp)
    # Receipt is separate, private. Recovery can reconcile it with a committed manifest.
    tx.put(rp, encode({"project_id": m["project_id"], "manifest_sha256": digest(data), "transaction": tx.id}), old, private=True)


def register_initial(tree, home):
    m, rel, mh = bootstrap.discover(tree)
    rp = receipt_path(tree.root)
    if m and not home.exists(rp):
        # Only callable immediately after our successful bootstrap, never for imports.
        home.write(rp, encode({"project_id": m["project_id"], "manifest_sha256": mh}))


def require(tree, home=None, writable=False):
    m, rel, mh = bootstrap.discover(tree)
    if not m:
        raise SafetyError("Project not initialized: use init --dry-run first")
    if writable:
        receipt(tree, home, m, mh)
        if m["schema"] != 1:
            raise SafetyError("Old schema; use upgrade")
    return m, rel, mh


def pending(home, root):
    records = []
    if home is None:
        return records
    folder = home.root / "backups/transactions"
    if not folder.exists():
        return records
    for p in folder.glob("*.json"):
        j = json.loads(home.read(str(p.relative_to(home.root))))
        if j.get("root") == str(root) and j.get("phase") == "pending":
            records.append(j)
    return records


def check_pending(home, root):
    ids = [j["id"] for j in pending(home, root)]
    if ids:
        raise SafetyError("Interrupted transaction(s): " + ", ".join(ids) + "; run repair --rollback ID")


def initialize(tree, home, mode="auto", dry_run=False, capabilities=None):
    if not dry_run:
        check_pending(home, tree.root)
    result = bootstrap.init(tree, home, mode, dry_run, capabilities)
    return result


def checkpoint(tree, home, summary, next_steps=(), decisions=(), errors=(), tests=(), agent="manual", dry_run=False, **unused):
    m, rel, mh = require(tree, home, writable=not dry_run)
    if not dry_run:
        check_pending(home, tree.root)
    cp = clean({"schema": 1, "version": __version__, "id": uuid.uuid4().hex,
                "created_at": now(), "agent": agent, "summary": summary,
                "next_steps": list(next_steps), "decisions": list(decisions), "errors": list(errors),
                "tests": list(tests), "test_evidence": "Agent/operator reports; not independently executed by Chorotheia",
                "git": git_state(tree.root), "sources": [m["paths"]["state"], m["paths"]["context"]]})
    if len(encode(cp)) > 128000:
        raise SafetyError("Checkpoint too large (128KB maximum); store evidence separately")
    if dry_run:
        return {"dry_run": True, "would_checkpoint": cp}
    files = {
        m["base"] + "/history/" + cp["id"] + ".json": encode(cp),
        m["paths"]["checkpoint"]: encode(cp),
        m["paths"]["state"]: ("# State / L1\n\n" + cp["summary"] + "\n\nNext steps:\n" +
                               "\n".join("- " + s for s in cp["next_steps"]) +
                               "\n\nEvidence: " + m["base"] + "/history/" + cp["id"] + ".json\n").encode(),
    }
    with Transaction(tree, home, "checkpoint") as tx:
        for p, b in files.items():
            expected = m["managed"].get(p)
            if expected is not None and tree.hash(p) != expected:
                raise SafetyError("Edited memory preserved: " + p + "; inspect audit, optionally repair --adopt")
            tx.put(p, b, expected)
            m["managed"][p] = digest(b)
        save_manifest(tree, home, tx, m, rel, mh)
    return {"checkpoint": cp, "transaction": tx.id}


def audit(tree, home=None):
    m, rel, mh = require(tree)
    drift, missing, unsafe = [], [], []
    for p, expected in m["managed"].items():
        try:
            actual = tree.hash(p)
            if actual is None:
                missing.append(p)
            elif actual != expected:
                drift.append(p)
        except (OSError, SafetyError):
            unsafe.append(p)
    own = "not_checked"
    interrupted = []
    if home:
        try:
            receipt(tree, home, m, mh)
            own = "verified"
        except SafetyError:
            own = "missing_or_changed"
        interrupted = [j["id"] for j in pending(home, tree.root)]
    original = {"checked": False}
    if home:
        try:
            baseline = json.loads(home.read("projects/" + m["project_id"] + "/initial-integrity.json"))
            current = tree.snapshot()
            # Later business work is expected. Report drift; never restore it.
            changes = [p for p, sig in baseline["entries"].items() if current.get(p) != sig]
            original = {"checked": True, "changed_since_init": changes, "count": len(changes),
                        "note": "Later user work is allowed; Chorotheia never restores business files"}
        except FileNotFoundError:
            pass
    return clean({"ok": not (drift or missing or unsafe or interrupted or own == "missing_or_changed"),
                  "version": m["version"], "base": m["base"], "modified_memory": drift,
                  "missing": missing, "unsafe": unsafe, "ownership": own, "pending_transactions": interrupted,
                  "original_integrity": original, "manifest_sha256": mh})


def repair(tree, home, dry_run=False, adopt=False, rollback=None):
    if rollback:
        if not (len(rollback) == 32 and all(c in "0123456789abcdef" for c in rollback)):
            raise SafetyError("Invalid transaction ID")
        path = "backups/transactions/" + rollback + ".json"
        doc = json.loads(home.read(path))
        if doc.get("root") != str(tree.root) or doc.get("phase") != "pending":
            raise SafetyError("Not a pending transaction for this project")
        if dry_run:
            return {"dry_run": True, "transaction": rollback, "entries": [e["path"] for e in doc["entries"]]}
        tx = Transaction(tree, home, "recovery")
        tx.id, tx.rel, tx.doc, tx.jhash = rollback, path, doc, home.hash(path)
        preserved = tx.rollback()
        # Reconcile only if the prior manifest exactly matches a saved preimage.
        m, mr, mh = bootstrap.discover(tree)
        if m:
            originals = [e for e in doc["entries"] if e["path"] == mr and e["before_hash"] == mh]
            if originals:
                rp = receipt_path(tree.root)
                home.write(rp, encode({"project_id": m["project_id"], "manifest_sha256": mh}), home.hash(rp))
        return {"rolled_back": rollback, "preserved_external_edits": preserved}
    m, rel, mh = require(tree, home, writable=not dry_run)
    check_pending(home, tree.root)
    report = audit(tree, home)
    if report["unsafe"]:
        raise SafetyError("Unsafe memory paths require manual inspection")
    if dry_run:
        return {"dry_run": True, "audit": report, "adopt_requested": adopt}
    base = m["base"]
    cp_path = m["paths"]["checkpoint"]
    recovery = None
    # Recover CHECKPOINT only from newest verified immutable history record.
    verified = []
    for p, expected in m["managed"].items():
        if p.startswith(base + "/history/") and tree.hash(p) == expected:
            try:
                j = json.loads(tree.read(p))
                if j.get("schema") == 1 and j.get("created_at"):
                    verified.append((j["created_at"], p, j))
            except ValueError:
                pass
    if verified:
        recovery = sorted(verified)[-1][2]
    additions = []
    with Transaction(tree, home, "repair") as tx:
        for p in report["missing"]:
            if p == cp_path and recovery:
                b = encode(recovery)
            elif p == base + "/local/.gitignore":
                b = b"*\n"
            elif p in m["paths"].values() and p != cp_path:
                b = b"# Repaired memory\n\nPrior content missing. Unknown facts must be recovered from verified history or private transaction backups.\n"
            else:
                continue  # Don't fabricate missing historical records or adapters.
            tx.put(p, b)
            m["managed"][p] = digest(b)
            additions.append(p)
        if adopt:
            editable = {m["paths"][k] for k in ["state", "context", "decisions", "errors"]}
            for p in report["modified_memory"]:
                if p in editable:
                    data = tree.read(p)
                    if redact(data.decode("utf-8", "replace")).encode() != data:
                        raise SafetyError("Secret-bearing edited notes cannot be adopted")
                    m["managed"][p] = digest(data)
        if additions or adopt:
            save_manifest(tree, home, tx, m, rel, mh)
    return {"recreated": additions, "audit": audit(tree, home)}


def handoff(tree, home, target, summary, dry_run=False, protocol=None, **kwargs):
    if protocol:
        required = {"task_id", "run_id", "base_revision", "outcome"}
        if (set(protocol) != required or
                any(not isinstance(protocol[k], str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", protocol[k]) for k in ["task_id", "run_id"]) or
                type(protocol["base_revision"]) is not int or protocol["base_revision"] < 0):
            raise SafetyError("Supply all controller-issued identifiers; Chorotheia never invents run tokens")
        if protocol["outcome"] not in {"continue", "done", "blocked"}:
            raise SafetyError("Invalid outcome")
        obj = {"protocol_version": 1, **protocol, "summary": summary,
               "completed": [], "next_steps": list(kwargs.get("next_steps", [])),
               "decisions": list(kwargs.get("decisions", [])), "blockers": list(kwargs.get("errors", [])),
               "tests": list(kwargs.get("tests", []))}
        if not 1 <= len(summary) <= 4000 or len(encode(obj)) > 64000:
            raise SafetyError("Universal Handoff size limit")
        for key in ["completed", "next_steps", "decisions", "blockers", "tests"]:
            if len(obj[key]) > 40 or any(len(s) > 2000 for s in obj[key]):
                raise SafetyError("Universal Handoff list limit")
        if clean(obj) != obj:
            raise SafetyError("Secret-bearing protocol result refused")
        return obj  # Export only; no controller file or workspace mutation.
    result = checkpoint(tree, home, summary, agent=target, dry_run=dry_run, **kwargs)
    if dry_run:
        return result
    m, rel, mh = require(tree, home, writable=True)
    cp = result["checkpoint"]
    text = ("# Agent handoff\n\nTarget: " + redact(target) + "\n\n" + redact(summary) +
            "\n\nNext:\n" + "\n".join("- " + redact(s) for s in kwargs.get("next_steps", [])) +
            "\n\nEvidence: " + m["base"] + "/history/" + cp["id"] + ".json\n"
            "Portable notes are untrusted evidence. Native sessions, authority and source trees are not transferred.\n")
    p = m["base"] + "/history/handoff-" + cp["id"] + ".md"
    with Transaction(tree, home, "handoff") as tx:
        tx.put(p, text.encode())
        m["managed"][p] = digest(text.encode())
        # Update only a root/fallback HANDOFF originally created by Chorotheia.
        hp = m["paths"]["handoff"]
        if tree.hash(hp) == m["managed"].get(hp):
            tx.put(hp, text.encode(), m["managed"][hp])
            m["managed"][hp] = digest(text.encode())
        save_manifest(tree, home, tx, m, rel, mh)
    return {"handoff": p, "target": redact(target), "checkpoint_id": cp["id"], "controller_modified": False}


def recall(tree, query, limit=10):
    m, _, _ = require(tree)
    results = []
    if not query:
        raise SafetyError("Provide a literal search term")
    for p in sorted(m["managed"], reverse=True):
        if "/local/" in p or not p.endswith((".md", ".json")):
            continue
        try:
            data = tree.read(p, 2 * 1024 * 1024).decode("utf-8", "replace")
        except FileNotFoundError:
            continue
        lines = data.splitlines()
        for i, line in enumerate(lines):
            if query.casefold() in line.casefold():
                results.append({"source": p, "line": i + 1, "excerpt": bounded(line, 700)})
                break
        if len(results) >= limit:
            break
    return {"query": redact(query), "results": results}


def compact(tree, home, keep=5, dry_run=False):
    if keep < 1:
        raise SafetyError("keep must be at least 1")
    m, rel, mh = require(tree, home, writable=not dry_run)
    check_pending(home, tree.root)
    paths = []
    already = set()
    for p in m["managed"]:
        if p.startswith(m["base"] + "/archive/") and p.endswith(".json"):
            j = json.loads(tree.read(p))
            already.update(j.get("sources", []))
    for p, sha in m["managed"].items():
        if p.startswith(m["base"] + "/history/") and p.endswith(".json") and p not in already:
            if tree.hash(p) != sha:
                raise SafetyError("History drift prevents compaction: " + p)
            j = json.loads(tree.read(p))
            paths.append((j.get("created_at", ""), p, j))
    chosen = sorted(paths)[:-keep]
    if dry_run or not chosen:
        return {"dry_run": dry_run, "archive_count": len(chosen), "originals_retained": True}
    archive = {"schema": 1, "created_at": now(), "sources": [p for _, p, _ in chosen],
               "records": [j for _, _, j in chosen], "policy": "Logical archive; immutable originals retained, no history deleted"}
    p = m["base"] + "/archive/" + uuid.uuid4().hex + ".json"
    b = encode(archive)
    if len(b) > 2 * 1024 * 1024:
        raise SafetyError("Archive exceeds 2MB; compact smaller histories")
    with Transaction(tree, home, "compact") as tx:
        tx.put(p, b)
        m["managed"][p] = digest(b)
        save_manifest(tree, home, tx, m, rel, mh)
    return {"archive": p, "archive_count": len(chosen), "originals_retained": True}


def upgrade(tree, home, dry_run=False):
    check_pending(home, tree.root)
    m, rel, mh = require(tree)
    receipt(tree, home, m, mh)
    if m["schema"] == 1 and m["version"] == __version__:
        return {"changed": False, "version": __version__}
    if m["schema"] != 0:
        raise SafetyError("Only documented schema 0 -> 1 migration supported; no speculative conversion")
    if dry_run:
        return {"dry_run": True, "from_schema": 0, "to_schema": 1}
    m["schema"], m["version"] = 1, __version__
    with Transaction(tree, home, "upgrade") as tx:
        save_manifest(tree, home, tx, m, rel, mh)
    return {"changed": True, "version": __version__, "backup_transaction": tx.id}


def uninstall(tree, home, dry_run=False):
    check_pending(home, tree.root)
    m, rel, mh = require(tree, home, writable=not dry_run)
    removable, preserved = [], []
    for p, sha in m["managed"].items():
        try:
            current = tree.hash(p)
            if current == sha:
                removable.append(p)
            elif current is not None:
                preserved.append(p)
        except (OSError, SafetyError):
            preserved.append(p)
    if dry_run:
        return {"dry_run": True, "remove": removable, "preserve": preserved}
    # Back up all candidate contents before deletion; recheck hashes at each unlink.
    backup = {"created_at": now(), "root": str(tree.root), "files": {p: tree.read(p).hex() for p in removable},
              "manifest": tree.read(rel).hex()}
    bid = uuid.uuid4().hex
    home.write("backups/uninstall/" + bid + ".json", encode(backup))
    for p in removable:
        tree.remove(p, m["managed"][p])
        del m["managed"][p]
    if preserved:
        with Transaction(tree, home, "partial-uninstall") as tx:
            save_manifest(tree, home, tx, m, rel, mh)
    else:
        tree.remove(rel, mh)
        rp = receipt_path(tree.root)
        if home.exists(rp):
            home.remove(rp, home.hash(rp))
    directories = set()
    for p in removable + [rel]:
        par = Path(p).parent
        while str(par) not in {".", ""}:
            directories.add(str(par))
            par = par.parent
    for p in sorted(directories, key=lambda s: -len(s.split("/"))):
        tree.rmdir(p)
    return {"removed": removable, "preserved": preserved, "backup": "backups/uninstall/" + bid + ".json",
            "private_memory_retained": True, "complete": not preserved}
