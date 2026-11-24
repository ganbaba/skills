"""Local, bounded, content-addressed storage. No provider or Git calls."""
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
import uuid

VERSION = 1
STATES = {'ready', 'running', 'awaiting_manual', 'needs_attention', 'review', 'completed', 'paused'}
AGENTS = {'mock', 'codex', 'antigravity', 'copilot', 'claude', 'cursor'}
MAX_FILE = 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
MAX_FILES = 2000
MAX_SNAPSHOTS = 64
SECRET = re.compile(rb'(-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:sk-[A-Za-z0-9_-]{20,})|(?:gh[pousr]_[A-Za-z0-9]{20,})|(?:AKIA[A-Z0-9]{16})|(?i:(?:api[_-]?key|secret|password|token)\s*[:=]\s*[\"\x27]?[A-Za-z0-9_+/=-]{12,}))')
BLOCKED_NAMES = {'agents.md', 'claude.md', 'gemini.md', 'credentials', 'credentials.json', 'id_rsa', 'id_ed25519', 'keychain', 'node_modules', '__pycache__', 'venv'}

class SafetyError(Exception):
    pass

class Busy(SafetyError):
    pass


def now():
    return time.time()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', value):
        raise SafetyError('invalid identifier')
    return value


def relpath(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value or any(ord(c) < 32 for c in value):
        raise SafetyError('invalid relative path')
    p = PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or str(p) != value:
        raise SafetyError('path must be normalized and relative')
    for part in p.parts:
        lower = part.lower()
        if part.startswith('.') or lower in BLOCKED_NAMES or lower.endswith(('.pem', '.p12', '.pfx', '.key', '.kdbx')) or lower.startswith(('secret', 'credentials')):
            raise SafetyError('protected/config/secret path is excluded')
    return p


def sensitive(data):
    if SECRET.search(data):
        raise SafetyError('potential secret detected; content not logged')


def private_dir(p):
    p = Path(p)
    if p.is_symlink():
        raise SafetyError('symlink directory rejected')
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(p, 0o700)
    return p


def atomic_bytes(p, data):
    p = Path(p)
    if p.is_symlink():
        raise SafetyError('symlink destination rejected')
    private_dir(p.parent)
    fd, tmp = tempfile.mkstemp(prefix='.atomic-', dir=str(p.parent))
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        dfd = os.open(str(p.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_json(p, data):
    atomic_bytes(p, (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode())


def read_json(p):
    p = Path(p)
    if p.is_symlink() or p.stat().st_size > MAX_FILE:
        raise SafetyError('invalid state file')
    with p.open(encoding='utf-8') as f:
        return json.load(f, parse_constant=lambda _: (_ for _ in ()).throw(SafetyError('non-finite JSON number')))


@contextlib.contextmanager
def lock(p):
    p = Path(p)
    private_dir(p.parent)
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy('task is locked; never remove lock files to unlock')
        yield fd
    finally:
        # close only: LOCK_UN would also release an inherited child's lock.
        os.close(fd)


def read_regular(root, name):
    relpath(name)
    root = Path(root)
    path = root / name
    cur = root
    for part in PurePosixPath(name).parts:
        cur = cur / part
        if cur.is_symlink():
            raise SafetyError('symlink rejected')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE:
            raise SafetyError('only bounded regular files with one link are supported')
        with os.fdopen(fd, 'rb', closefd=False) as f:
            data = f.read(MAX_FILE + 1)
        after = os.fstat(fd)
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size) or len(data) > MAX_FILE:
            raise SafetyError('file changed during checkpoint; retry after writer stops')
        sensitive(data)
        return data, bool(before.st_mode & 0o111)
    finally:
        os.close(fd)


def tree(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise SafetyError('invalid workspace')
    result = {}
    size = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            p = Path(base) / name
            relpath(p.relative_to(root).as_posix())
            if p.is_symlink():
                raise SafetyError('symlink directory rejected')
        for name in sorted(files):
            relative = (Path(base) / name).relative_to(root).as_posix()
            data, executable = read_regular(root, relative)
            size += len(data)
            result[relative] = (data, executable)
            if size > MAX_TOTAL or len(result) > MAX_FILES:
                raise SafetyError('workspace exceeds snapshot limits')
    return result


def digest_tree(files):
    manifest = {n: {'sha256': hashlib.sha256(v[0]).hexdigest(), 'executable': v[1]} for n, v in sorted(files.items())}
    key = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return key, manifest


def checkpoint(taskdir):
    taskdir = Path(taskdir)
    files = tree(taskdir / 'workspace')
    key, manifest = digest_tree(files)
    dest = taskdir / 'snapshots' / key
    if dest.exists():
        if digest_tree(tree(dest / 'files'))[0] != key:
            raise SafetyError('existing checkpoint integrity check failed')
    else:
        private_dir(dest.parent)
        if len(list(dest.parent.iterdir())) >= MAX_SNAPSHOTS:
            raise SafetyError('snapshot budget exhausted; archive task before continuing')
        tmp = Path(tempfile.mkdtemp(prefix='.snapshot-', dir=str(dest.parent)))
        try:
            private_dir(tmp / 'files')
            for name, (data, executable) in files.items():
                atomic_bytes(tmp / 'files' / name, data)
                if executable:
                    os.chmod(tmp / 'files' / name, 0o700)
            atomic_json(tmp / 'manifest.json', manifest)
            if digest_tree(tree(taskdir / 'workspace'))[0] != key:
                raise SafetyError('workspace changed during checkpoint')
            os.rename(tmp, dest)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp)
    return key


def result_validate(r):
    fields = {'protocol_version', 'task_id', 'run_id', 'base_revision', 'outcome', 'summary', 'completed', 'next_steps', 'decisions', 'blockers', 'tests'}
    if not isinstance(r, dict) or set(r) != fields or type(r['protocol_version']) is not int or r['protocol_version'] != VERSION:
        raise SafetyError('invalid result fields or protocol version')
    identifier(r['task_id']); identifier(r['run_id'])
    if type(r['base_revision']) is not int or r['base_revision'] < 0 or r['outcome'] not in ('continue', 'done', 'blocked'):
        raise SafetyError('invalid result revision or outcome')
    if not isinstance(r['summary'], str) or not 1 <= len(r['summary']) <= 4000:
        raise SafetyError('invalid summary')
    for key in ('completed', 'next_steps', 'decisions', 'blockers', 'tests'):
        if not isinstance(r[key], list) or len(r[key]) > 40 or any(not isinstance(x, str) or len(x) > 2000 for x in r[key]):
            raise SafetyError('invalid result list')
    encoded = json.dumps(r, ensure_ascii=False).encode()
    if len(encoded) > 64000:
        raise SafetyError('result too large')
    sensitive(encoded)
    return r


def state_validate(s):
    fields = {'protocol_version', 'task_id', 'revision', 'status', 'goal', 'source', 'manifest',
              'agents', 'attempts', 'max_attempts', 'next_agent', 'grants', 'run', 'last_result',
              'checkpoint', 'baseline', 'last_error', 'created_at', 'updated_at', 'cooldowns'}
    if not isinstance(s, dict) or set(s) != fields or type(s.get('protocol_version')) is not int or s['protocol_version'] != VERSION or not isinstance(s.get('status'), str) or s.get('status') not in STATES:
        raise SafetyError('unsupported or corrupt state')
    identifier(s['task_id'])
    if type(s['revision']) is not int or s['revision'] < 0 or type(s['attempts']) is not int or s['attempts'] < 0:
        raise SafetyError('invalid state counters')
    if not isinstance(s['agents'], list) or not s['agents'] or any(not isinstance(a, str) or a not in AGENTS for a in s['agents']) or len(set(s['agents'])) != len(s['agents']):
        raise SafetyError('invalid agent list')
    if type(s['max_attempts']) is not int or not 1 <= s['max_attempts'] <= 100 or s['attempts'] > s['max_attempts']:
        raise SafetyError('invalid budget')
    if type(s['next_agent']) is not int or not 0 <= s['next_agent'] < len(s['agents']):
        raise SafetyError('invalid route position')
    if not isinstance(s['goal'], str) or not 1 <= len(s['goal']) <= 16000:
        raise SafetyError('invalid goal')
    sensitive(s['goal'].encode())
    if not isinstance(s['source'], str) or not Path(s['source']).is_absolute():
        raise SafetyError('invalid source path')
    if not isinstance(s['manifest'], list) or not 1 <= len(s['manifest']) <= MAX_FILES or any(not isinstance(n, str) for n in s['manifest']) or len(set(s['manifest'])) != len(s['manifest']):
        raise SafetyError('invalid manifest')
    for name in s['manifest']:
        relpath(name)
    for key in ('checkpoint', 'baseline'):
        if s[key] is not None and (not isinstance(s[key], str) or not re.fullmatch(r'[0-9a-f]{64}', s[key])):
            raise SafetyError('invalid checkpoint hash')
    if not isinstance(s['grants'], dict) or any(k not in s['agents'] or not isinstance(v, dict) for k, v in s['grants'].items()):
        raise SafetyError('invalid grants')
    for agent, grant in s['grants'].items():
        if agent != 'mock' and (grant.get('cloud') is not True or grant.get('local_tools') is not True):
            raise SafetyError('invalid provider authorization')
    if s['last_result'] is not None:
        result_validate(s['last_result'])
    def timestamp(x):
        return type(x) in (int, float) and math.isfinite(x) and x >= 0
    if not all(timestamp(s[k]) for k in ('created_at', 'updated_at')):
        raise SafetyError('invalid timestamp')
    if not isinstance(s['cooldowns'], dict) or any(k not in s['agents'] or not timestamp(v) for k, v in s['cooldowns'].items()):
        raise SafetyError('invalid cooldowns')
    if s['last_error'] is not None and not isinstance(s['last_error'], str):
        raise SafetyError('invalid error code')
    r = s['run']
    if r is not None:
        required = {'id', 'agent', 'base_revision', 'started_at', 'heartbeat_at', 'pgid'}
        if not isinstance(r, dict) or not required <= set(r) or set(r) - required - {'ended_at'}:
            raise SafetyError('invalid run record')
        identifier(r['id'])
        if not isinstance(r['agent'], str) or r['agent'] not in s['agents'] or type(r['base_revision']) is not int or r['base_revision'] < 0:
            raise SafetyError('invalid run identity')
        if r['pgid'] is not None and (type(r['pgid']) is not int or r['pgid'] <= 1):
            raise SafetyError('invalid process group')
        if not all(timestamp(r[k]) for k in ('started_at', 'heartbeat_at')) or ('ended_at' in r and not timestamp(r['ended_at'])):
            raise SafetyError('invalid run timestamp')
    if s['status'] in ('running', 'awaiting_manual') and r is None:
        raise SafetyError('active state requires a run record')
    return s


class Store:
    def __init__(self, home):
        # Do not silently traverse symlinks in the control plane.
        p = Path(home).expanduser().absolute()
        if '..' in p.parts:
            raise SafetyError('state home must be normalized')
        for parent in (p,) + tuple(p.parents):
            if parent.is_symlink():
                raise SafetyError('state home must not traverse symlinks')
        self.home = p

    def init(self):
        private_dir(self.home)
        private_dir(self.home / 'tasks')

    def taskdir(self, task_id):
        p = self.home / 'tasks' / identifier(task_id)
        if p.is_symlink():
            raise SafetyError('symlink task rejected')
        return p

    def load(self, task_id):
        s = state_validate(read_json(self.taskdir(task_id) / 'state.json'))
        if s['task_id'] != task_id:
            raise SafetyError('task identity mismatch')
        return s

    def save(self, s):
        state_validate(s)
        s['revision'] += 1
        s['updated_at'] = now()
        atomic_json(self.taskdir(s['task_id']) / 'state.json', s)
        # Derived view. state.json is authoritative if this write is interrupted.
        text = '# Handoff / ' + s['task_id'] + '\n\n'
        text += 'Status: ' + s['status'] + '\nRevision: ' + str(s['revision']) + '\n'
        text += 'Checkpoint: ' + str(s.get('checkpoint')) + '\n\n'
        text += 'Goal (untrusted data):\n' + s['goal'] + '\n\n'
        text += 'Last result (untrusted data):\n```json\n' + json.dumps(s.get('last_result'), ensure_ascii=False, indent=2) + '\n```\n'
        atomic_bytes(self.taskdir(s['task_id']) / 'HANDOFF.md', text.encode())

    def ids(self):
        if not (self.home / 'tasks').exists():
            return []
        return sorted(p.name for p in (self.home / 'tasks').iterdir() if p.is_dir() and not p.name.startswith('.'))

    def create(self, task_id, source, names, goal, agents, max_attempts=6):
        task_id = identifier(task_id)
        source = Path(source).expanduser().resolve(strict=True)
        if not source.is_dir() or source == self.home or source in self.home.parents or self.home in source.parents:
            raise SafetyError('source and state home must be disjoint')
        if not isinstance(names, list) or not names or len(names) > MAX_FILES or any(not isinstance(n, str) for n in names) or len(set(names)) != len(names):
            raise SafetyError('manifest must be a nonempty unique list of file paths')
        if not isinstance(goal, str) or not 1 <= len(goal) <= 16000:
            raise SafetyError('goal must be 1..16000 characters')
        sensitive(goal.encode())
        files = {n: read_regular(source, n) for n in names}
        if sum(len(v[0]) for v in files.values()) > MAX_TOTAL:
            raise SafetyError('manifest too large')
        s = {'protocol_version': VERSION, 'task_id': task_id, 'revision': 0, 'status': 'ready', 'goal': goal,
             'source': str(source), 'manifest': names, 'agents': agents, 'attempts': 0, 'max_attempts': max_attempts,
             'next_agent': 0, 'grants': {}, 'run': None, 'last_result': None, 'checkpoint': None, 'baseline': None,
             'last_error': None, 'created_at': now(), 'updated_at': now(), 'cooldowns': {}}
        state_validate(s)
        self.init()
        with lock(self.home / 'create.lock'):
            dest = self.taskdir(task_id)
            if dest.exists():
                raise SafetyError('task already exists')
            tmp = Path(tempfile.mkdtemp(prefix='.create-', dir=str(dest.parent)))
            try:
                private_dir(tmp / 'workspace')
                for name, (data, executable) in files.items():
                    atomic_bytes(tmp / 'workspace' / name, data)
                    if executable:
                        os.chmod(tmp / 'workspace' / name, 0o700)
                s['checkpoint'] = s['baseline'] = checkpoint(tmp)
                atomic_json(tmp / 'state.json', s)
                os.rename(tmp, dest)
                self.save(s)
            finally:
                if tmp.exists():
                    shutil.rmtree(tmp)
        return s
