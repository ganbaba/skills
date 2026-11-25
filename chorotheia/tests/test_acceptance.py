"""Acceptance tests use isolated projects/config homes; no native agents or accounts."""
import base64
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

from chorotheia import core, bootstrap
from chorotheia.guard import Tree, Transaction, SafetyError, digest, encode, lock
from chorotheia.memory import restore, redact, git_state
from chorotheia.adapters import run_hook, hook_configs
from chorotheia.installation import install, uninstall_global, upgrade_global

SOURCE = Path(__file__).resolve().parents[1]


def hold_lock(home_path, root, ready, release):
    with Tree(home_path, private=True) as h, lock(h, root):
        ready.set()
        release.wait(20)


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chorotheia-test-")
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.home_path = self.root / "private"
        self.home = Tree(self.home_path, create=True, private=True)
        self.tree = Tree(self.project)

    def tearDown(self):
        self.tree.close()
        self.home.close()
        self.temp.cleanup()

    def init(self):
        return core.initialize(self.tree, self.home)

    def cp(self, summary="Observed progress", **kw):
        return core.checkpoint(self.tree, self.home, summary, **kw)

    def cli(self, *args, expected=0):
        r = subprocess.run([sys.executable, "-B", "-m", "chorotheia", *args,
                            "--project", str(self.project), "--home", str(self.home_path)],
                           cwd=SOURCE, capture_output=True, text=True)
        self.assertEqual(r.returncode, expected, r.stdout + r.stderr)
        return json.loads(r.stdout or r.stderr)

    def test_new_init_structure_and_integrity(self):
        r = self.init()
        self.assertEqual(r["mode"], "new")
        self.assertTrue(r["integrity_verified"])
        for p in ["AGENTS.md", "HANDOFF.md", ".agent/CONTEXT.md", ".agent/STATE.md", ".agent/DECISIONS.md", ".agent/ERRORS.md", ".agent/CHECKPOINT.json", ".agent/manifest.json", ".agent/local/.gitignore", ".agent/archive/.keep"]:
            self.assertTrue((self.project / p).is_file(), p)
        self.assertTrue(core.audit(self.tree, self.home)["ok"])

    def test_repeated_init_exact_idempotence(self):
        self.init()
        before = self.tree.snapshot()
        r = self.init()
        self.assertFalse(r["changed"])
        self.assertEqual(self.tree.snapshot(), before)

    def test_retrofit_preserves_existing_bytes_and_modes(self):
        for p, b in {"README.md": b"original docs\n", "code.py": b"print('work in progress')\n", "AGENTS.md": b"user rules\n", "HANDOFF.md": b"existing controller\n"}.items():
            (self.project / p).write_bytes(b)
        before = self.tree.snapshot()
        r = self.init()
        self.assertEqual(r["mode"], "retrofit")
        after = self.tree.snapshot()
        for p, row in before.items():
            self.assertEqual(after[p], row)
        self.assertNotEqual(r["paths"]["agents"], "AGENTS.md")
        self.assertNotEqual(r["paths"]["handoff"], "HANDOFF.md")

    def test_foreign_agent_namespace(self):
        (self.project / ".agent").mkdir()
        (self.project / ".agent/manifest.json").write_text('{"other":true}')
        (self.project / ".agent/CONTEXT.md").write_text("foreign memory")
        self.assertEqual(self.init()["base"], ".agent/chorotheia")
        self.assertEqual((self.project / ".agent/CONTEXT.md").read_text(), "foreign memory")

    def test_agent_file_conflict_uses_fallback(self):
        (self.project / ".agent").write_text("existing file")
        self.assertEqual(self.init()["base"], ".chorotheia-agent")

    def test_symlink_namespace_does_not_escape(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.project / ".agent").symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.init()["base"], ".chorotheia-agent")
        self.assertEqual(list(outside.iterdir()), [])

    def test_all_conflict_paths_preserved(self):
        for p in [".agent/chorotheia", ".chorotheia-agent"]:
            (self.project / p).mkdir(parents=True)
        before = self.tree.snapshot()
        with self.assertRaises(SafetyError):
            self.init()
        self.assertEqual(before, self.tree.snapshot())

    def test_explicit_new_refuses_nonempty(self):
        (self.project / "business").write_text("keep")
        with self.assertRaises(SafetyError):
            core.initialize(self.tree, self.home, mode="new")

    def test_dry_run_zero_project_and_home_writes(self):
        before, hb = self.tree.snapshot(), self.home.snapshot()
        self.assertTrue(self.cli("init", "--dry-run")["dry_run"])
        self.assertEqual(before, self.tree.snapshot())
        self.assertEqual(hb, self.home.snapshot())

    def test_dry_run_missing_home_not_created(self):
        absent = self.root / "absent"
        r = subprocess.run([sys.executable, "-B", "-m", "chorotheia", "init", "--project", str(self.project), "--home", str(absent), "--dry-run"], cwd=SOURCE, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(absent.exists())

    def test_checkpoint_restore_preserves_task(self):
        self.init()
        c = self.cp("修复导出任务已完成第一步", next_steps=["继续验证中文文件名"], decisions=["本地优先"], tests=["fixture check: passed"])
        m, _, _ = core.require(self.tree)
        r = restore(self.tree, m, 2400)
        self.assertIn("修复导出任务", r["text"])
        self.assertIn("继续验证", r["text"])
        self.assertLessEqual(r["utf8_bytes"], 2400)
        self.assertTrue((self.project / (".agent/history/" + c["checkpoint"]["id"] + ".json")).exists())

    def test_restore_budget_and_secret_redaction(self):
        self.init()
        self.cp("password=do-not-export\n" + "上下文" * 1000, next_steps=["api_key=super-secret-value"])
        m, _, _ = core.require(self.tree)
        r = restore(self.tree, m, 1500)
        self.assertLessEqual(r["utf8_bytes"], 1500)
        self.assertNotIn("do-not-export", r["text"])
        self.assertNotIn("super-secret-value", (self.project / ".agent/CHECKPOINT.json").read_text())

    def test_sensitive_patterns(self):
        for s in ["sk-abcdefghijklmnop12345", "ghp_abcdefghijklmnop123456", "AKIA1234567890123456", "Bearer abcdefghi", "https://alice:secret@example.test", "-----BEGIN PRIVATE KEY-----\nkeybytes\n-----END PRIVATE KEY-----"]:
            self.assertNotEqual(redact(s), s)

    def test_history_evidence_separates_unknown(self):
        (self.project / "pyproject.toml").write_text('[project]\nname="x"\n')
        (self.project / "README.md").write_text("Previous test allegedly passed; api_key=abc")
        self.init()
        context = (self.project / ".agent/CONTEXT.md").read_text()
        self.assertIn('"confirmed"', context)
        self.assertIn('"inferred"', context)
        self.assertIn('"unknown"', context)
        self.assertNotIn("allegedly passed", context)

    def test_private_state_outside_repo(self):
        self.init()
        self.assertEqual((self.home_path.stat().st_mode & 0o777), 0o700)
        local_files = list((self.project / ".agent/local").iterdir())
        self.assertEqual([p.name for p in local_files], [".gitignore"])
        self.assertTrue(list((self.home_path / "projects").rglob("initial-integrity.json")))

    def test_path_traversal_and_symlink_root_refused(self):
        for p in ["../bad", "/tmp/bad", "x/../../bad", "x\\bad"]:
            with self.assertRaises(SafetyError):
                self.tree.write(p, b"bad")
        link = self.root / "link"
        link.symlink_to(self.project, target_is_directory=True)
        with self.assertRaises(SafetyError):
            Tree(link)

    def test_symlink_leaf_and_hardlink_refused(self):
        (self.project / "link").symlink_to(self.root / "never-created")
        with self.assertRaises(OSError):
            self.tree.read("link")
        src = self.root / "hard-source"
        src.write_bytes(b"keep")
        os.link(src, self.project / "hard")
        with self.assertRaises(SafetyError):
            self.tree.read("hard")
        self.assertEqual(src.read_bytes(), b"keep")

    def test_parent_symlink_injection_refused(self):
        self.init()
        outside = self.root / "outside"
        outside.mkdir()
        history = self.project / ".agent/history"
        history.rename(self.project / ".agent/history-saved")
        history.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            self.cp()
        self.assertEqual(list(outside.iterdir()), [])

    def test_untrusted_unsupported_protocol_ids_refused(self):
        for task, run, rev in [("../escape", "run", 1), ("task", "run with spaces", 1), ("task", "run", True)]:
            with self.assertRaises(SafetyError):
                core.handoff(self.tree, self.home, "codex", "test", protocol={"task_id": task, "run_id": run, "base_revision": rev, "outcome": "continue"})

    def test_exclusive_same_bytes_failure_never_deletes_existing(self):
        (self.project / "occupied").write_bytes(b"same")
        with self.assertRaises(SafetyError):
            with Transaction(self.tree, self.home, "collision") as tx:
                tx.put("occupied", b"same")
        self.assertEqual((self.project / "occupied").read_bytes(), b"same")

    def test_atomic_publish_never_overwrites(self):
        self.tree.write("file", b"first")
        with self.assertRaises(FileExistsError):
            self.tree.write("file", b"second")
        self.assertEqual(self.tree.read("file"), b"first")
        self.assertFalse(any(p.name.startswith(".chorotheia-tmp") for p in self.project.iterdir()))

    def test_compare_and_swap_rejects_stale_update(self):
        self.tree.write("file", b"original")
        expected = self.tree.hash("file")
        (self.project / "file").write_bytes(b"external")
        with self.assertRaises(SafetyError):
            self.tree.write("file", b"ours", expected)
        self.assertEqual(self.tree.read("file"), b"external")

    def test_transaction_failure_rolls_back_only_ours(self):
        self.tree.write("existing", b"before")
        with self.assertRaises(RuntimeError):
            with Transaction(self.tree, self.home, "fail") as tx:
                tx.put("new/file", b"ours")
                tx.put("existing", b"after", digest(b"before"))
                raise RuntimeError("injected failure")
        self.assertFalse((self.project / "new").exists())
        self.assertEqual(self.tree.read("existing"), b"before")

    def test_rollback_preserves_concurrent_external_edit(self):
        with self.assertRaises(RuntimeError):
            with Transaction(self.tree, self.home, "fail") as tx:
                tx.put("new", b"ours")
                (self.project / "new").write_bytes(b"user changed it")
                raise RuntimeError("injected failure")
        self.assertEqual(self.tree.read("new"), b"user changed it")

    def test_init_external_change_triggers_integrity_rollback(self):
        (self.project / "business.py").write_bytes(b"original")
        original = self.tree.snapshot
        calls = [0]
        def change():
            calls[0] += 1
            if calls[0] == 2:
                (self.project / "business.py").write_bytes(b"external work")
            return original()
        with patch.object(self.tree, "snapshot", side_effect=change):
            with self.assertRaises(SafetyError):
                self.init()
        self.assertEqual(self.tree.read("business.py"), b"external work")
        self.assertFalse((self.project / "AGENTS.md").exists())
        self.assertFalse((self.project / ".agent/manifest.json").exists())

    def test_cross_process_exclusive_lock(self):
        ctx = multiprocessing.get_context("spawn")
        ready, release = ctx.Event(), ctx.Event()
        p = ctx.Process(target=hold_lock, args=(str(self.home_path), str(self.project), ready, release))
        p.start()
        try:
            self.assertTrue(ready.wait(10))
            self.cli("init", expected=2)
        finally:
            release.set()
            p.join(10)
        self.assertEqual(p.exitcode, 0)
        self.cli("init")

    def test_lock_symlink_refused(self):
        rel = "locks/" + digest(str(self.project).encode()) + ".lock"
        with self.home.parent(rel, create=True) as (fd, leaf):
            os.symlink(str(self.root / "victim"), leaf, dir_fd=fd)
        with self.assertRaises(OSError):
            with lock(self.home, self.project):
                pass
        self.assertFalse((self.root / "victim").exists())

    def test_crash_journal_blocks_then_recovers(self):
        self.init()
        tx = Transaction(self.tree, self.home, "simulated-crash")
        tx.save()
        tx.put(".agent/history/crash.json", b"ours")
        with self.assertRaises(SafetyError):
            self.cp()
        r = core.repair(self.tree, self.home, rollback=tx.id)
        self.assertEqual(r["preserved_external_edits"], [])
        self.assertFalse((self.project / ".agent/history/crash.json").exists())
        self.cp()

    def test_actual_process_exit_recovery(self):
        self.init()
        code = """import os,sys
from chorotheia.guard import Tree,Transaction,lock
with Tree(sys.argv[1]) as p,Tree(sys.argv[2],private=True) as h,lock(h,p.root):
 t=Transaction(p,h,'process-exit');t.save();t.put('.agent/history/abrupt.json',b'ours');os._exit(86)
"""
        r = subprocess.run([sys.executable, "-B", "-c", code, str(self.project), str(self.home_path)], cwd=SOURCE)
        self.assertEqual(r.returncode, 86)
        transactions = core.pending(self.home, self.project)
        self.assertEqual(len(transactions), 1)
        core.repair(self.tree, self.home, rollback=transactions[0]["id"])
        self.assertFalse((self.project / ".agent/history/abrupt.json").exists())
        self.cp("Recovered after process termination")

    def test_ambiguous_crash_record_is_preserved(self):
        tx = Transaction(self.tree, self.home, "ambiguous")
        tx.save()
        tx.put("new", b"ours")
        tx.doc["entries"][0]["applied"] = False
        tx.save()
        r = core.repair(self.tree, self.home, rollback=tx.id)
        self.assertIn("new", r["preserved_external_edits"])
        self.assertTrue((self.project / "new").exists())

    def test_receipt_rolls_back_with_manifest(self):
        self.init()
        m, rel, mh = core.require(self.tree)
        before = self.tree.read(rel)
        rp = core.receipt_path(self.project)
        rb = self.home.read(rp)
        with self.assertRaises(RuntimeError):
            with Transaction(self.tree, self.home, "failure-after-receipt") as tx:
                m["extra"] = "changed"
                core.save_manifest(self.tree, self.home, tx, m, rel, mh)
                raise RuntimeError("crash")
        self.assertEqual(self.tree.read(rel), before)
        self.assertEqual(self.home.read(rp), rb)
        self.assertTrue(core.audit(self.tree, self.home)["ok"])

    def test_modified_memory_blocked_then_explicitly_adopted(self):
        self.init()
        (self.project / ".agent/STATE.md").write_text("Reviewed user note")
        with self.assertRaises(SafetyError):
            self.cp()
        self.assertEqual(self.tree.read(".agent/STATE.md"), b"Reviewed user note")
        self.assertTrue(core.repair(self.tree, self.home, adopt=True)["audit"]["ok"])
        self.cp()

    def test_modified_manifest_untrusted_ownership_refused(self):
        self.init()
        p = self.project / ".agent/manifest.json"
        m = json.loads(p.read_text())
        m["managed"][".agent/foreign-file"] = digest(b"keep")
        p.write_bytes(encode(m))
        with self.assertRaises(SafetyError):
            self.cp()
        with self.assertRaises(SafetyError):
            core.uninstall(self.tree, self.home)

    def test_manifest_path_escape_refused(self):
        self.init()
        p = self.project / ".agent/manifest.json"
        m = json.loads(p.read_text())
        m["paths"]["state"] = "../outside"
        p.write_bytes(encode(m))
        with self.assertRaises(SafetyError):
            core.require(self.tree)

    def test_missing_checkpoint_recovered_from_verified_history(self):
        self.init()
        self.cp("Recovery target")
        (self.project / ".agent/CHECKPOINT.json").unlink()
        r = core.repair(self.tree, self.home)
        self.assertIn(".agent/CHECKPOINT.json", r["recreated"])
        self.assertEqual(json.loads(self.tree.read(".agent/CHECKPOINT.json"))["summary"], "Recovery target")

    def test_handoff_preserves_existing_controller_and_business(self):
        (self.project / "HANDOFF.md").write_bytes(b"controller derived view")
        (self.project / "state.json").write_bytes(b"controller-state")
        self.init()
        r = core.handoff(self.tree, self.home, "copilot", "Continue export", next_steps=["Verify fixture"])
        self.assertFalse(r["controller_modified"])
        self.assertEqual(self.tree.read("HANDOFF.md"), b"controller derived view")
        self.assertEqual(self.tree.read("state.json"), b"controller-state")
        self.assertTrue((self.project / r["handoff"]).exists())

    def test_universal_handoff_protocol_export_exact_shape(self):
        before = self.tree.snapshot()
        protocol = {"task_id": "supplied-task", "run_id": "supplied-run", "base_revision": 12, "outcome": "continue"}
        r = core.handoff(self.tree, self.home, "codex", "Observed fixture", protocol=protocol, next_steps=["Continue"])
        self.assertEqual(set(r), {"protocol_version", "task_id", "run_id", "base_revision", "outcome", "summary", "completed", "next_steps", "decisions", "blockers", "tests"})
        self.assertEqual(r["base_revision"], 12)
        self.assertEqual(self.tree.snapshot(), before)
        protocol["run_id"] = None
        with self.assertRaises(SafetyError):
            core.handoff(self.tree, self.home, "codex", "test", protocol=protocol)

    def test_compact_and_recall_retain_history(self):
        self.init()
        for i in range(7):
            self.cp("Needle-task-%d" % i)
        before = {p.name: p.read_bytes() for p in (self.project / ".agent/history").iterdir()}
        r = core.compact(self.tree, self.home, keep=2)
        self.assertGreater(r["archive_count"], 0)
        self.assertTrue(core.recall(self.tree, "Needle-task-0")["results"])
        after = {p.name: p.read_bytes() for p in (self.project / ".agent/history").iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(core.compact(self.tree, self.home, keep=2)["archive_count"], 0)

    def test_schema_upgrade_backed_up_and_idempotent(self):
        self.init()
        m, rel, mh = core.require(self.tree)
        with Transaction(self.tree, self.home, "schema0-fixture") as tx:
            m["schema"] = 0
            m["version"] = "0.0.0-fixture"
            core.save_manifest(self.tree, self.home, tx, m, rel, mh)
        self.assertEqual(core.upgrade(self.tree, self.home, True)["to_schema"], 1)
        self.assertTrue(core.upgrade(self.tree, self.home)["changed"])
        self.assertFalse(core.upgrade(self.tree, self.home)["changed"])

    def test_project_uninstall_preserves_original_and_modified(self):
        (self.project / "README.md").write_bytes(b"user-doc")
        self.init()
        (self.project / ".agent/STATE.md").write_bytes(b"user edited memory")
        r = core.uninstall(self.tree, self.home)
        self.assertFalse(r["complete"])
        self.assertIn(".agent/STATE.md", r["preserved"])
        self.assertEqual(self.tree.read("README.md"), b"user-doc")
        self.assertEqual(self.tree.read(".agent/STATE.md"), b"user edited memory")

    def test_full_project_uninstall_removes_only_owned(self):
        (self.project / "business.py").write_bytes(b"keep")
        self.init()
        self.assertTrue(core.uninstall(self.tree, self.home)["complete"])
        self.assertEqual(list(self.project.iterdir()), [self.project / "business.py"])

    def test_git_dirty_staged_untracked_and_index_unchanged(self):
        def g(*a):
            return subprocess.check_output(["git", "-C", str(self.project), *a], stderr=subprocess.DEVNULL)
        g("init", "-q")
        (self.project / "tracked.py").write_bytes(b"base")
        g("add", "tracked.py")
        g("-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
        (self.project / "tracked.py").write_bytes(b"staged")
        g("add", "tracked.py")
        (self.project / "tracked.py").write_bytes(b"unstaged")
        (self.project / "untracked secret.env").write_bytes(b"private")
        baseline = self.tree.snapshot()
        initial_head = g("rev-parse", "HEAD")
        self.init()
        self.cp()
        after = self.tree.snapshot()
        for p, sig in baseline.items():
            self.assertEqual(after[p], sig, p)
        self.assertEqual(initial_head, g("rev-parse", "HEAD"))
        state = git_state(self.project)
        self.assertTrue(any(c["status"] == "MM" for c in state["changes"]))
        self.assertTrue(any(c["status"] == "??" for c in state["changes"]))

    def test_native_hook_payload_adapters(self):
        self.init()
        self.cp("Known task progress")
        fixtures = [("codex", "SessionStart", {"cwd": str(self.project)}),
                    ("antigravity", "SessionStart", {"workspacePaths": [str(self.project)]}),
                    ("copilot", "sessionStart", {"cwd": str(self.project)})]
        for agent, event, payload in fixtures:
            out = run_hook(self.home, agent, event, payload)
            self.assertIn("Known task progress", json.dumps(out))
        run_hook(self.home, "codex", "PreCompact", {"cwd": str(self.project)})
        run_hook(self.home, "codex", "PostCompact", {"cwd": str(self.project)})
        run_hook(self.home, "antigravity", "Stop", {"cwd": str(self.project), "transcriptPath": "/DO-NOT-READ"})
        with self.assertRaises(SafetyError):
            run_hook(self.home, "antigravity", "PreCompact", {"cwd": str(self.project)})

    def test_hooks_non_git_uninitialized_noop(self):
        self.assertEqual(run_hook(self.home, "codex", "SessionStart", {"cwd": str(self.project)}), {})
        self.assertEqual(list(self.project.iterdir()), [])
        self.assertEqual(run_hook(self.home, "antigravity", "SessionStart", {"workspacePaths": ["/a", "/b"]}), {})

    def test_hook_auto_init_can_be_disabled(self):
        subprocess.check_call(["git", "init", "-q", str(self.project)])
        self.home.write("config.toml", b'auto_init = "off"\ninitial_budget = 2000\n')
        self.assertEqual(run_hook(self.home, "codex", "SessionStart", {"cwd": str(self.project)}), {})
        self.assertFalse((self.project / ".agent").exists())

    def test_hook_git_auto_init(self):
        subprocess.check_call(["git", "init", "-q", str(self.project)])
        out = run_hook(self.home, "codex", "SessionStart", {"cwd": str(self.project)})
        self.assertIn("hookSpecificOutput", out)
        self.assertTrue((self.project / ".agent/manifest.json").exists())
        self.assertEqual(json.loads(self.tree.read(".agent/manifest.json"))["mode"], "new")

    def test_cli_end_to_end_commands(self):
        self.cli("init", "--mode", "audit", expected=2)
        self.cli("retrofit")
        self.cli("checkpoint", "--summary", "CLI observation", "--next", "Follow up", "--test", "not run: fixture")
        self.assertIn("CLI observation", self.cli("restore")["text"])
        self.cli("handoff", "--to", "antigravity", "--summary", "CLI relay")
        self.assertTrue(self.cli("recall", "CLI relay")["results"])
        self.cli("compact", "--keep", "1")
        self.assertTrue(self.cli("status")["ok"])
        self.assertTrue(self.cli("audit")["ok"])
        self.cli("repair", "--dry-run")
        self.cli("upgrade")
        self.cli("uninstall", "--dry-run")


class InstallationAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chorotheia-install-test-")
        self.root = Path(self.tmp.name).resolve()
        self.user = self.root / "user"
        self.user.mkdir()
        self.target = self.user / ".chorotheia"

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_backups_preserves_configs_launcher_and_sandbox_uninstall(self):
        (self.user / ".codex").mkdir()
        config = self.user / ".codex/config.toml"
        config.write_bytes(b'user_setting = "keep"\n')
        r = install(SOURCE, self.target, self.user)
        self.assertTrue(r["installed"])
        self.assertEqual(config.read_bytes(), b'user_setting = "keep"\n')
        self.assertTrue((self.target / r["backup"]).exists())
        launcher = self.target / "bin/chorotheia"
        self.assertEqual(subprocess.check_output([str(launcher), "--version"], text=True).strip(), "1.0.0")
        self.assertFalse(install(SOURCE, self.target, self.user)["changed"])
        self.assertTrue(upgrade_global(self.target)["ok"])
        (self.user / ".codex/skills/chorotheia/SKILL.md").write_bytes(b"user customized skill")
        r = uninstall_global(self.target)
        self.assertIn(str(self.user / ".codex/skills/chorotheia/SKILL.md"), r["preserved_modified"])
        self.assertEqual(config.read_bytes(), b'user_setting = "keep"\n')
        self.assertFalse(launcher.exists())
        self.assertTrue((self.target / "backups").exists())

    def test_existing_hook_configuration_is_not_overwritten(self):
        (self.user / ".codex").mkdir()
        hook = self.user / ".codex/hooks.json"
        hook.write_bytes(b'{"hooks":{"Stop":[]},"custom":"keep"}')
        r = install(SOURCE, self.target, self.user)
        self.assertIn(".codex/hooks.json", r["skipped_conflicts"])
        self.assertEqual(hook.read_bytes(), b'{"hooks":{"Stop":[]},"custom":"keep"}')

    def test_install_collision_refused(self):
        self.target.mkdir(mode=0o700)
        (self.target / "VERSION").write_bytes(b"foreign")
        with self.assertRaises(SafetyError):
            install(SOURCE, self.target, self.user)
        self.assertEqual((self.target / "VERSION").read_bytes(), b"foreign")

    def test_install_dry_run_creates_nothing(self):
        self.assertTrue(install(SOURCE, self.target, self.user, dry_run=True)["dry_run"])
        self.assertEqual(list(self.user.iterdir()), [])

    def test_local_maintenance_upgrade_is_hashed_backed_up_and_idempotent(self):
        install(SOURCE, self.target, self.user)
        src = self.root / "maintenance"
        for folder in ["chorotheia", "skills", "tests"]:
            shutil.copytree(SOURCE / folder, src / folder)
        init = src / "chorotheia/__init__.py"
        init.write_text(init.read_text()+"\n# Reviewed fixture maintenance\n")
        preview = upgrade_global(self.target, True, src)
        self.assertTrue(preview["would_update"])
        r = upgrade_global(self.target, False, src)
        self.assertTrue(r["changed"])
        self.assertTrue((self.target / ("backups/transactions/" + r["backup_transaction"] + ".json")).exists())
        self.assertFalse(upgrade_global(self.target, False, src)["changed"])

    def test_invalid_maintenance_bundle_preserves_installation(self):
        install(SOURCE, self.target, self.user)
        src = self.root / "invalid-maintenance"
        shutil.copytree(SOURCE / "chorotheia", src / "chorotheia")
        (src / "chorotheia/memory.py").write_bytes(b"invalid python !")
        with Tree(self.target) as t:
            before = t.snapshot()
            with self.assertRaises(SafetyError):
                upgrade_global(self.target, False, src)
            self.assertEqual(t.snapshot(), before)

    def test_installer_failure_cleans_new_registrations_only(self):
        (self.user / ".codex").mkdir()
        (self.user / ".codex/AGENTS.md").write_bytes(b"original rules")
        original = Tree.write
        def injected(store, rel, *args, **kwargs):
            if rel == ".gemini/config/hooks.json":
                raise OSError("injected disk failure")
            return original(store, rel, *args, **kwargs)
        with patch.object(Tree, "write", new=injected):
            with self.assertRaises(OSError):
                install(SOURCE, self.target, self.user)
        self.assertEqual((self.user / ".codex/AGENTS.md").read_bytes(), b"original rules")
        self.assertFalse((self.user / ".codex/hooks.json").exists())
        self.assertFalse((self.target / "bin/chorotheia").exists())
        self.assertTrue((self.target / "backups").exists())

    def test_global_uninstall_dry_run_zero_change(self):
        install(SOURCE, self.target, self.user)
        with Tree(self.user) as t:
            before = t.snapshot()
            uninstall_global(self.target, True)
            self.assertEqual(before, t.snapshot())

    def test_global_previews_do_not_open_write_locks(self):
        install(SOURCE, self.target, self.user)
        with patch("chorotheia.installation.lock", side_effect=AssertionError("write lock requested by preview")):
            self.assertTrue(uninstall_global(self.target, True)["dry_run"])
            self.assertTrue(upgrade_global(self.target, True)["ok"])

    def test_private_home_permissions_enforced(self):
        self.target.mkdir(mode=0o755)
        with self.assertRaises(SafetyError):
            install(SOURCE, self.target, self.user)

    def test_hook_configs_use_native_schemas_and_escaped_launcher(self):
        r = hook_configs(Path("/tmp/path with spaces/chorotheia"))
        self.assertIn("hooks", r["codex"])
        self.assertIn("chorotheia", r["antigravity"])
        self.assertEqual(r["copilot"]["version"], 1)
        self.assertTrue(r["codex"]["hooks"]["SessionStart"][0]["hooks"][0]["command"].startswith("'"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
