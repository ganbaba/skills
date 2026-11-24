import fcntl
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from handoff import adapters, engine
from handoff.cli import main
from handoff.model import (Busy, SafetyError, Store, atomic_bytes, atomic_json, checkpoint,
                           digest_tree, lock, read_json, result_validate, tree)

ROOT = Path(__file__).resolve().parents[1]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='handoff-test-')
        self.base = Path(self.tmp.name).resolve()
        self.source = self.base / 'original'
        self.source.mkdir()
        (self.source / 'hello.txt').write_text('original\n')
        self.store = Store(self.base / 'state')
        self.store.init()
        atomic_json(self.store.home / 'config.json', engine.DEFAULT_CONFIG)

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, task='demo', agents=None, budget=6):
        return self.store.create(task, self.source, ['hello.txt'], 'Test only', agents or ['mock'], budget)

    def result(self, s, outcome='continue'):
        return {'protocol_version': 1, 'task_id': s['task_id'], 'run_id': s['run']['id'],
                'base_revision': s['run']['base_revision'], 'outcome': outcome, 'summary': 'progress',
                'completed': [], 'next_steps': ['next'], 'decisions': [], 'blockers': [], 'tests': ['not run']}

    def cfg(self, **updates):
        c = dict(engine.DEFAULT_CONFIG)
        c.update(updates)
        atomic_json(self.store.home / 'config.json', c)
        return c

    def fake_grant(self, task, agent):
        s = self.store.load(task)
        s['grants'][agent] = {'cloud': True, 'local_tools': True, 'available': True}
        self.store.save(s)


class StorageTests(Fixture):
    def test_local_relay_and_original_unchanged(self):
        before = digest_tree(tree(self.source))[0]
        self.create()
        engine.grant(self.store, 'demo', ['mock'])
        one = engine.run_once(self.store, 'demo')
        self.assertEqual(one['status'], 'ready')
        one_checkpoint = one['checkpoint']
        two = engine.tick(self.store)
        self.assertEqual(two['status'], 'review')
        self.assertNotEqual(one_checkpoint, two['checkpoint'])
        self.assertEqual(digest_tree(tree(self.source))[0], before)
        self.assertEqual(engine.tick(self.store), None)
        engine.change_status(self.store, 'demo', 'accept', True)
        self.assertEqual(self.store.load('demo')['status'], 'completed')
        self.assertEqual(len(list((self.store.taskdir('demo') / 'runs').glob('*.json'))), 2)

    def test_no_authorization_no_invocation(self):
        self.create(agents=['codex'])
        with patch.object(adapters, 'command') as cmd:
            s = engine.run_once(self.store, 'demo')
            cmd.assert_not_called()
        self.assertEqual(s['attempts'], 0)
        self.assertEqual(s['last_error'], 'no_authorized_agent')

    def test_authorization_requires_both_acknowledgements(self):
        self.create(agents=['codex'])
        with self.assertRaises(SafetyError):
            engine.grant(self.store, 'demo', ['codex'], True, False)
        self.assertEqual(self.store.load('demo')['grants'], {})

    def test_manifest_path_traversal_hidden_secret_and_symlinks(self):
        (self.source / 'link.txt').symlink_to(self.base / 'outside')
        (self.source / 'key.txt').write_text('-----BEGIN PRIVATE KEY-----\n')
        (self.source / 'AGENTS.md').write_text('malicious instructions')
        for name in ('../outside', '/etc/passwd', '.env', 'link.txt', 'key.txt', 'AGENTS.md', 'hello.txt\n'):
            with self.subTest(name=name), self.assertRaises((SafetyError, OSError)):
                self.store.create('bad', self.source, [name], 'safe', ['mock'])
        self.assertFalse(self.store.taskdir('bad').exists())

    def test_hardlink_and_fifo_rejected(self):
        os.link(self.source / 'hello.txt', self.source / 'hard.txt')
        with self.assertRaises(SafetyError):
            self.create()
        os.unlink(self.source / 'hard.txt')
        os.mkfifo(self.source / 'pipe')
        with self.assertRaises(SafetyError):
            self.store.create('bad', self.source, ['pipe'], 'safe', ['mock'])

    def test_workspace_secret_blocks_even_mock(self):
        self.create(); engine.grant(self.store, 'demo', ['mock'])
        (self.store.taskdir('demo') / 'workspace/leak.txt').write_text('sk-' + 'a' * 30)
        s = engine.run_once(self.store, 'demo')
        self.assertEqual((s['status'], s['last_error'], s['attempts']), ('needs_attention', 'unsafe_workspace', 0))
        self.assertNotIn('sk-' + 'a' * 30, (self.store.taskdir('demo') / 'state.json').read_text())

    def test_atomic_state_failure_leaves_valid_previous_state(self):
        self.create()
        before = (self.store.taskdir('demo') / 'state.json').read_bytes()
        with patch('handoff.model.os.replace', side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError):
                atomic_json(self.store.taskdir('demo') / 'state.json', {'partial': True})
        self.assertEqual(before, (self.store.taskdir('demo') / 'state.json').read_bytes())
        self.assertFalse(list(self.store.taskdir('demo').glob('.atomic-*')))

    def test_lock_busy_across_processes_and_private_modes(self):
        self.create()
        p = self.store.taskdir('demo') / 'task.lock'
        with lock(p):
            code = 'from handoff.model import lock; from pathlib import Path;\nwith lock(Path(__import__("sys").argv[1])): pass'
            r = subprocess.run([sys.executable, '-c', code, str(p)], cwd=str(ROOT), capture_output=True)
            self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.store.taskdir('demo') / 'state.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.home.stat().st_mode & 0o777, 0o700)

    def test_budget_cannot_loop_forever(self):
        self.create(budget=1); engine.grant(self.store, 'demo', ['mock'])
        s = engine.run_once(self.store, 'demo')
        self.assertEqual(s['last_error'], 'attempt_budget_exhausted')
        with self.assertRaises(SafetyError):
            engine.change_status(self.store, 'demo', 'resume')

    def test_restore_copy_preserves_backup_and_source(self):
        initial = self.create()['baseline']; engine.grant(self.store, 'demo', ['mock'])
        engine.run_once(self.store, 'demo')
        s = engine.restore(self.store, 'demo', initial, True)
        self.assertEqual(s['status'], 'paused')
        self.assertFalse((self.store.taskdir('demo') / 'workspace/relay-demo.txt').exists())
        backups = list(self.store.taskdir('demo').glob('workspace-before-restore-*'))
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / 'relay-demo.txt').exists())
        self.assertEqual((self.source / 'hello.txt').read_text(), 'original\n')

    def test_empty_tree_checkpoint(self):
        self.create()
        (self.store.taskdir('demo') / 'workspace/hello.txt').unlink()
        key = checkpoint(self.store.taskdir('demo'))
        self.assertEqual(tree(self.store.taskdir('demo') / 'snapshots' / key / 'files'), {})

    def test_export_patch_and_no_writeback(self):
        self.create()
        ws = self.store.taskdir('demo') / 'workspace'
        (ws / 'hello.txt').write_text('changed\n')
        (ws / 'new.txt').write_text('new\n')
        dest = self.base / 'changes.patch'
        engine.export_patch(self.store, 'demo', dest)
        diff = dest.read_text()
        self.assertIn('-original\n+changed', diff)
        # Verify the patch can actually apply to a separate plain copy.
        if __import__('shutil').which('git'):
            r = subprocess.run(['git', 'apply', '--check', str(dest)], cwd=str(self.source), capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.source / 'hello.txt').read_text(), 'original\n')
        with self.assertRaises(SafetyError):
            engine.export_patch(self.store, 'demo', self.source / 'bad.patch')

    def test_no_symlink_state_home(self):
        target = self.base / 'elsewhere'; target.mkdir()
        link = self.base / 'link'; link.symlink_to(target)
        with self.assertRaises(SafetyError):
            Store(link / 'state')

    def test_corrupt_state_unknown_keys_rejected(self):
        self.create()
        statepath = self.store.taskdir('demo') / 'state.json'
        s = read_json(statepath)
        atomic_json(statepath, dict(s, unexpected_command='do not run'))
        with self.assertRaises(SafetyError):
            self.store.load('demo')

    def test_snapshot_tampering_rejected(self):
        s = self.create()
        snap = self.store.taskdir('demo') / 'snapshots' / s['baseline'] / 'files/hello.txt'
        snap.write_text('tampered\n')
        with self.assertRaises(SafetyError):
            checkpoint(self.store.taskdir('demo'))
        with self.assertRaises(SafetyError):
            engine.export_patch(self.store, 'demo', self.base / 'bad.patch')

    def test_snapshot_budget_stops_without_deletion(self):
        self.create()
        workspace = self.store.taskdir('demo') / 'workspace'
        (workspace / 'hello.txt').write_text('changed\n')
        with patch('handoff.model.MAX_SNAPSHOTS', 1), self.assertRaises(SafetyError):
            checkpoint(self.store.taskdir('demo'))
        self.assertEqual(len(list((self.store.taskdir('demo') / 'snapshots').iterdir())), 1)

    def test_export_cannot_follow_symlink_into_original(self):
        self.create()
        link = self.base / 'alias'; link.symlink_to(self.source)
        with self.assertRaises(SafetyError):
            engine.export_patch(self.store, 'demo', link / 'oops.patch')
        self.assertFalse((self.source / 'oops.patch').exists())

    def test_overlapping_source_does_not_modify_project(self):
        bad = Store(self.source / 'would-be-state')
        with self.assertRaises(SafetyError):
            bad.create('bad', self.source, ['hello.txt'], 'safe', ['mock'])
        self.assertEqual(sorted(p.name for p in self.source.iterdir()), ['hello.txt'])


class AdapterTests(Fixture):
    def test_all_vendor_envelopes(self):
        s = self.create()
        s['run'] = {'id': 'abc', 'base_revision': 1}
        r = self.result(s)
        text = json.dumps(r)
        examples = {
            'codex': '\n'.join(json.dumps(e) for e in [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}}, {'type': 'turn.completed'}]),
            'antigravity': json.dumps({'status': 'SUCCESS', 'response': text}),
            'claude': json.dumps({'type': 'result', 'subtype': 'success', 'result': text, 'is_error': False}),
            'cursor': json.dumps({'type': 'result', 'subtype': 'success', 'result': text, 'is_error': False}),
            'copilot': text,
        }
        for agent, raw in examples.items():
            with self.subTest(agent=agent):
                self.assertEqual(adapters.normalize(agent, raw), r)

    def test_error_denial_malformed_output_not_success(self):
        for agent, raw in [('codex', '{"type":"turn.failed"}'), ('cursor', '{}'),
                           ('claude', '{"is_error":true}'), ('antigravity', '{"status":"ERROR"}'),
                           ('claude', '{"permission_denials":[{}]}'), ('copilot', 'permission denied')]:
            with self.subTest(agent=agent), self.assertRaises(SafetyError):
                adapters.normalize(agent, raw)

    def test_extra_fields_and_secrets_rejected(self):
        s = self.create(); s['run'] = {'id': 'abc', 'base_revision': 1}
        r = self.result(s)
        with self.assertRaises(SafetyError):
            result_validate(dict(r, override_permissions=True))
        r['summary'] = 'sk-' + 'a' * 30
        with self.assertRaises(SafetyError):
            result_validate(r)

    def test_binary_pin_changes_and_argv_never_uses_shell(self):
        binary = self.base / 'fake'
        binary.write_text('first')
        g = {'cloud': True, 'local_tools': True, 'path': str(binary), 'sha256': adapters.binary_hash(binary)}
        prompt = '$(touch /private/tmp/DO_NOT_CREATE) `oops` ; echo hi'
        argv = adapters.command('codex', prompt, g)
        self.assertEqual(argv[-1], prompt)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', argv)
        binary.write_text('second')
        with self.assertRaises(SafetyError):
            adapters.command('codex', prompt, g)

    def test_missing_cli_returns_explicit_unavailability(self):
        with patch('handoff.adapters.shutil.which', return_value=None):
            self.assertEqual(adapters.probe('antigravity')['reason'], 'cli_missing')

    def test_feature_gate(self):
        fake = self.base / 'fake'; fake.write_text('fake')
        result = subprocess.CompletedProcess([], 0, b'--print --output-format', b'')
        with patch('handoff.adapters.shutil.which', return_value=str(fake)), patch('handoff.adapters.subprocess.run', return_value=result):
            evidence = adapters.probe('claude')
        self.assertFalse(evidence['available'])
        self.assertIn('--bare', evidence['missing'])


class RelayTests(Fixture):
    def test_manual_token_stale_replay_and_completion(self):
        self.create(agents=['antigravity'])
        with patch.object(adapters, 'probe', return_value={'available': False, 'reason': 'cli_missing'}):
            engine.grant(self.store, 'demo', ['antigravity'], True, True)
        s = engine.run_once(self.store, 'demo')
        self.assertEqual(s['status'], 'awaiting_manual')
        self.assertTrue((self.store.taskdir('demo') / 'MANUAL_PROMPT.txt').is_file())
        r = self.result(s, 'done')
        bad = dict(r, base_revision=r['base_revision'] - 1)
        with self.assertRaises(SafetyError):
            engine.complete_manual(self.store, 'demo', bad, True)
        with self.assertRaises(SafetyError):
            engine.complete_manual(self.store, 'demo', r, False)
        engine.complete_manual(self.store, 'demo', r, True)
        self.assertEqual(self.store.load('demo')['status'], 'review')
        with self.assertRaises(SafetyError):
            engine.complete_manual(self.store, 'demo', r, True)

    def test_invalid_result_never_marked_done_and_no_raw_logs(self):
        self.create(agents=['codex']); self.fake_grant('demo', 'codex')
        secret = 'sk-' + 'z' * 30
        with patch.object(adapters, 'command', return_value=['not-executed']), patch.object(engine, 'execute', return_value=(secret, None, True)):
            s = engine.run_once(self.store, 'demo')
        self.assertEqual(s['status'], 'needs_attention')
        for p in self.store.taskdir('demo').rglob('*.json'):
            self.assertNotIn(secret, p.read_text())

    def test_failure_failover_is_opt_in_and_cooldown(self):
        self.create(agents=['codex', 'mock']); self.fake_grant('demo', 'codex')
        engine.grant(self.store, 'demo', ['mock'])
        self.cfg(failover_on_failure=True)
        with patch.object(adapters, 'command', return_value=['not-executed']), patch.object(engine, 'execute', return_value=('', 'timeout', True)):
            s = engine.run_once(self.store, 'demo')
        self.assertEqual(s['status'], 'ready')
        self.assertGreater(s['cooldowns']['codex'], time.time())
        s = engine.run_once(self.store, 'demo')
        self.assertEqual(s['status'], 'review')
        self.assertEqual(s['attempts'], 2)

    def test_orphans_prevent_even_opt_in_failover(self):
        self.create(agents=['codex', 'mock']); self.fake_grant('demo', 'codex')
        self.cfg(failover_on_failure=True)
        with patch.object(adapters, 'command', return_value=['not-executed']), patch.object(engine, 'execute', return_value=('', 'orphan_processes', False)):
            s = engine.run_once(self.store, 'demo')
        self.assertEqual(s['status'], 'needs_attention')
        self.assertIsNotNone(s['run'])

    def test_recover_never_steals_live_lock_or_kills_stored_pid(self):
        self.create()
        s = self.store.load('demo')
        s['status'] = 'running'; s['run'] = {'id': 'interrupted', 'agent': 'mock', 'pgid': os.getpgrp(), 'base_revision': s['revision'], 'started_at': time.time(), 'heartbeat_at': time.time()}
        self.store.save(s)
        with self.assertRaises(SafetyError):
            engine.recover(self.store, 'demo', True)
        with lock(self.store.taskdir('demo') / 'task.lock'):
            with self.assertRaises(Busy):
                engine.recover(self.store, 'demo', True)
        s['run']['pgid'] = None; self.store.save(s)
        recovered = engine.recover(self.store, 'demo', True)
        self.assertEqual(recovered['status'], 'needs_attention')
        self.assertIsNone(recovered['run'])

    def test_cooling_task_does_not_starve_next(self):
        self.create('first'); self.create('second')
        engine.grant(self.store, 'first', ['mock']); engine.grant(self.store, 'second', ['mock'])
        s = self.store.load('first'); s['cooldowns']['mock'] = time.time() + 100; self.store.save(s)
        s['updated_at'] = 0; atomic_json(self.store.taskdir('first') / 'state.json', s)
        self.assertEqual(engine.tick(self.store)['task_id'], 'second')

    def test_revoke_pauses_and_blocks_dispatch(self):
        self.create(); engine.grant(self.store, 'demo', ['mock']); engine.revoke(self.store, 'demo')
        self.assertEqual(engine.run_once(self.store, 'demo')['attempts'], 0)

    def test_real_offline_cli_round_robin_all_five_vendors(self):
        vendors = ['codex', 'antigravity', 'copilot', 'claude', 'cursor']
        self.create(agents=vendors, budget=10)
        for vendor in vendors:
            self.fake_grant('demo', vendor)
        def fake_command(agent, prompt, grant):
            return [sys.executable, str(ROOT / 'tests/fake_cli.py'), '--vendor', agent, prompt]
        with patch.object(adapters, 'command', side_effect=fake_command):
            for i, vendor in enumerate(vendors):
                if i:
                    (self.store.taskdir('demo') / 'workspace/offline-relay.txt').unlink()
                result = engine.run_once(self.store, 'demo')
                self.assertEqual(result['status'], 'ready')
                receipts = [read_json(x) for x in (self.store.taskdir('demo') / 'runs').glob('*.json')]
                self.assertIn(vendor, [r['run']['agent'] for r in receipts])
            result = engine.run_once(self.store, 'demo')
            self.assertEqual(result['status'], 'review')
        self.assertEqual((self.source / 'hello.txt').read_text(), 'original\n')
        self.assertFalse((self.source / 'offline-relay.txt').exists())


class ProcessTests(Fixture):
    def run_process(self, code, c=None, callback=None):
        self.create()
        d = self.store.taskdir('demo')
        with lock(d / 'task.lock') as fd:
            return engine.execute([sys.executable, '-c', code], d / 'workspace', fd,
                                  c or self.cfg(timeout_seconds=2, checkpoint_seconds=1),
                                  lambda pid: None, callback or (lambda: None))

    def test_real_subprocess_timeout_cleanup(self):
        output, reason, clean = self.run_process('import time; time.sleep(30)', self.cfg(timeout_seconds=1))
        self.assertEqual((reason, clean, output), ('timeout', True, ''))

    def test_real_subprocess_output_limit(self):
        _, reason, clean = self.run_process('print("x"*10000)', self.cfg(max_output_bytes=1024))
        self.assertEqual((reason, clean), ('output_limit', True))

    def test_real_subprocess_stderr_discard_and_autosave(self):
        counter = []
        output, reason, clean = self.run_process('import sys,time; print("do not save me", file=sys.stderr); time.sleep(1.2); print("ok")',
                                                self.cfg(timeout_seconds=4, checkpoint_seconds=1), lambda: counter.append(1))
        self.assertEqual(output.strip(), 'ok')
        self.assertIsNone(reason); self.assertTrue(clean); self.assertGreaterEqual(len(counter), 1)

    def test_inherited_lock_survives_parent_descriptor_close(self):
        # A scheduler SIGKILL must not immediately allow a second writer while child survives.
        pth = self.store.home / 'inherited.lock'
        with lock(pth) as fd:
            p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], pass_fds=(fd,), start_new_session=True)
        try:
            with self.assertRaises(Busy):
                with lock(pth):
                    pass
        finally:
            engine.stop_group(p)
        with lock(pth):
            pass

    def test_environment_secrets_not_inherited(self):
        os.environ['HANDOFF_TEST_PRIVATE_TOKEN'] = 'should_not_be_inherited'
        try:
            output, reason, clean = self.run_process('import os; print(os.environ.get("HANDOFF_TEST_PRIVATE_TOKEN", "absent"))')
        finally:
            del os.environ['HANDOFF_TEST_PRIVATE_TOKEN']
        self.assertEqual(output.strip(), 'absent')


class CliTests(Fixture):
    def test_plist_valid_paths_and_no_loading(self):
        dest = self.base / 'launch agent.plist'
        code = main(['--home', str(self.store.home), 'schedule-plist', '--output', str(dest)])
        self.assertEqual(code, 0)
        data = plistlib.loads(dest.read_bytes())
        self.assertIn(str(self.store.home), data['ProgramArguments'])
        self.assertEqual(data['StartInterval'], 60)
        self.assertFalse(data['RunAtLoad'])
        self.assertNotIn('KeepAlive', data)

    def test_corrupt_config_rejected_before_launch(self):
        self.create(); atomic_json(self.store.home / 'config.json', {'unsafe': True})
        with self.assertRaises(SafetyError):
            engine.run_once(self.store, 'demo')


if __name__ == '__main__':
    unittest.main()
