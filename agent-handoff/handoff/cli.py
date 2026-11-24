import argparse
import json
import os
from pathlib import Path
import plistlib
import signal
import sys
from . import adapters, engine
from .model import AGENTS, Busy, SafetyError, Store, atomic_bytes, atomic_json, read_json


def report(s):
    if s is None:
        return {'status': 'idle'}
    return {k: s.get(k) for k in ('task_id', 'status', 'revision', 'attempts', 'max_attempts', 'checkpoint', 'last_error')}


def main(argv=None):
    p = argparse.ArgumentParser(description='Local Handoff control plane; no GUI automation or quota scraping')
    p.add_argument('--home', default=str(Path.home() / 'Library/Application Support/AgentHandoff'))
    subs = p.add_subparsers(dest='cmd', required=True)
    subs.add_parser('init')
    subs.add_parser('doctor', help='Probe CLI help only; does not check login or quota')
    c = subs.add_parser('create')
    c.add_argument('--id', required=True); c.add_argument('--source', required=True)
    c.add_argument('--manifest', required=True); c.add_argument('--goal-file', required=True)
    c.add_argument('--agents', default='mock'); c.add_argument('--max-attempts', type=int, default=6)
    for name in ('status', 'run', 'pause', 'resume', 'revoke'):
        t = subs.add_parser(name); t.add_argument('task_id')
    g = subs.add_parser('authorize')
    g.add_argument('task_id'); g.add_argument('--agents', required=True)
    g.add_argument('--ack-cloud', action='store_true'); g.add_argument('--ack-local-tools', action='store_true')
    subs.add_parser('tick')
    subs.add_parser('list')
    c = subs.add_parser('complete-manual')
    c.add_argument('task_id'); c.add_argument('--result', required=True); c.add_argument('--ack-agent-stopped', action='store_true')
    c = subs.add_parser('recover')
    c.add_argument('task_id'); c.add_argument('--ack-orphans-stopped', action='store_true')
    c = subs.add_parser('accept')
    c.add_argument('task_id'); c.add_argument('--ack-reviewed', action='store_true')
    c = subs.add_parser('restore')
    c.add_argument('task_id'); c.add_argument('--checkpoint', required=True); c.add_argument('--ack-overwrite-copy', action='store_true')
    c = subs.add_parser('export-patch')
    c.add_argument('task_id'); c.add_argument('--output', required=True)
    c = subs.add_parser('schedule-plist', help='Generate launchd file only; never loads it')
    c.add_argument('--output', required=True); c.add_argument('--interval', type=int, default=60)
    args = p.parse_args(argv)
    try:
        store = Store(args.home)
        cmd = args.cmd
        if cmd == 'init':
            store.init()
            if not (store.home / 'config.json').exists():
                atomic_json(store.home / 'config.json', engine.DEFAULT_CONFIG)
            out = {'home': str(store.home), 'external_execution': 'disabled until task authorization'}
        elif cmd == 'doctor':
            out = {agent: adapters.probe(agent) for agent in sorted(AGENTS)}
        elif cmd == 'create':
            agents = args.agents.split(',')
            s = store.create(args.id, args.source, read_json(args.manifest),
                             Path(args.goal_file).read_text(encoding='utf-8'), agents, args.max_attempts)
            out = report(s)
        elif cmd == 'authorize':
            out = report(engine.grant(store, args.task_id, args.agents.split(','), args.ack_cloud, args.ack_local_tools))
        elif cmd == 'revoke':
            out = report(engine.revoke(store, args.task_id))
        elif cmd == 'status':
            out = store.load(args.task_id)
        elif cmd == 'list':
            out = [report(store.load(i)) for i in store.ids()]
        elif cmd == 'run':
            out = report(engine.run_once(store, args.task_id))
        elif cmd == 'tick':
            out = report(engine.tick(store))
        elif cmd in ('accept', 'pause', 'resume'):
            out = report(engine.change_status(store, args.task_id, cmd, getattr(args, 'ack_reviewed', False)))
        elif cmd == 'recover':
            out = report(engine.recover(store, args.task_id, args.ack_orphans_stopped))
        elif cmd == 'complete-manual':
            out = report(engine.complete_manual(store, args.task_id, read_json(args.result), args.ack_agent_stopped))
        elif cmd == 'restore':
            out = report(engine.restore(store, args.task_id, args.checkpoint, args.ack_overwrite_copy))
        elif cmd == 'export-patch':
            out = {'patch': engine.export_patch(store, args.task_id, args.output), 'applied': False}
        elif cmd == 'schedule-plist':
            if not 30 <= args.interval <= 86400:
                raise SafetyError('interval must be 30..86400 seconds')
            outpath = Path(args.output).expanduser().absolute()
            if outpath.exists() or outpath.is_symlink():
                raise SafetyError('plist output already exists')
            entrypoint = str(Path(__file__).resolve().parents[1] / 'handoff.py')
            data = {'Label': 'local.universal-handoff',
                    'ProgramArguments': [sys.executable, entrypoint, '--home', str(store.home), 'tick'],
                    'StartInterval': args.interval, 'RunAtLoad': False,
                    'EnvironmentVariables': {'PATH': os.environ.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin')},
                    'ProcessType': 'Background', 'Umask': 0o077}
            atomic_bytes(outpath, plistlib.dumps(data))
            out = {'plist': str(outpath), 'loaded': False}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    except Busy as e:
        print(json.dumps({'error': str(e), 'retryable': True}), file=sys.stderr)
        return 3
    except (SafetyError, OSError, ValueError) as e:
        # File contents, provider output and raw JSON parser errors may hold secrets.
        msg = str(e) if isinstance(e, SafetyError) else type(e).__name__ + ': inspect local paths/configuration'
        print(json.dumps({'error': msg}), file=sys.stderr)
        return 2


def terminate(_signum, _frame):
    raise KeyboardInterrupt()


def entrypoint():
    signal.signal(signal.SIGTERM, terminate)
    try:
        return main()
    except KeyboardInterrupt:
        print('{"error":"interrupted; inspect status before resuming"}', file=sys.stderr)
        return 130
