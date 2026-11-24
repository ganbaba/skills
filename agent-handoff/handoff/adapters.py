"""Documented CLI surfaces, feature-gated locally. No GUI or quota scraping."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from .model import SafetyError, result_validate

SPECS = {
    'codex': {'binary': 'codex', 'flags': ['exec', '--json', '--sandbox', 'workspace-write', '--ephemeral', '--ignore-user-config', '--skip-git-repo-check'],
              'required': ['--json', '--sandbox', '--ephemeral', '--ignore-user-config', '--skip-git-repo-check'], 'help': ['exec', '--help']},
    'antigravity': {'binary': 'agy', 'flags': ['-p', '--output-format', 'json', '--mode', 'default', '--sandbox'],
                    'required': ['--print', '--output-format', '--mode', '--sandbox'], 'help': ['--help']},
    'copilot': {'binary': 'copilot', 'flags': ['-p', '-s', '--no-ask-user', '--no-auto-update', '--allow-tool=read,write'],
                'required': ['--prompt', '--no-ask-user', '--no-auto-update', '--allow-tool'], 'help': ['--help']},
    'claude': {'binary': 'claude', 'flags': ['-p', '--bare', '--output-format', 'json', '--permission-mode', 'dontAsk', '--permission-prompts', 'none', '--tools', 'Read,Edit,Write,Glob,Grep', '--allowedTools', 'Read,Edit,Write,Glob,Grep', '--no-session-persistence'],
               'required': ['--print', '--bare', '--output-format', '--permission-mode', '--permission-prompts', '--tools', '--allowedTools', '--no-session-persistence'], 'help': ['--help']},
    'cursor': {'binary': 'agent', 'flags': ['-p', '--output-format', 'json'],
               'required': ['--print', '--output-format'], 'help': ['--help']},
}


def binary_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def probe(agent):
    if agent == 'mock':
        return {'available': True, 'local_only': True}
    spec = SPECS[agent]
    found = shutil.which(spec['binary'])
    if not found:
        return {'available': False, 'reason': 'cli_missing', 'binary': spec['binary']}
    path = str(Path(found).resolve())
    try:
        p = subprocess.run([path] + spec['help'], capture_output=True, timeout=15, stdin=subprocess.DEVNULL)
        help_text = (p.stdout + p.stderr).decode(errors='replace')
        missing = [flag for flag in spec['required'] if flag not in help_text]
        if p.returncode or missing:
            return {'available': False, 'reason': 'unsupported_cli_flags', 'missing': missing}
        # A feature probe is not a login, permission, entitlement, or quota check.
        return {'available': True, 'path': path, 'sha256': binary_hash(path), 'login': 'unknown', 'quota': 'unknown'}
    except (OSError, subprocess.TimeoutExpired):
        return {'available': False, 'reason': 'probe_failed'}


def command(agent, prompt, grant):
    spec = SPECS[agent]
    if not grant.get('cloud') or not grant.get('local_tools'):
        raise SafetyError('provider execution is not authorized')
    path = grant.get('path', '')
    if not path or not Path(path).is_file() or binary_hash(path) != grant.get('sha256'):
        raise SafetyError('CLI changed or disappeared; authorize again after reviewing update')
    # argv only. The prompt is one argument, never shell syntax.
    return [path] + spec['flags'] + [prompt]


def normalize(agent, output):
    try:
        if agent == 'codex':
            final = None
            completed = False
            for line in output.splitlines():
                event = json.loads(line)
                if event.get('type') in ('error', 'turn.failed'):
                    raise SafetyError('provider reported failure')
                if event.get('type') == 'turn.completed':
                    completed = True
                item = event.get('item', {})
                if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
                    final = item.get('text')
            if not completed or not isinstance(final, str):
                raise SafetyError('missing terminal event or final response')
        elif agent in ('claude', 'cursor', 'antigravity'):
            envelope = json.loads(output)
            if not isinstance(envelope, dict) or envelope.get('is_error') or envelope.get('permission_denials'):
                raise SafetyError('provider error or denied permission')
            if agent == 'antigravity':
                if envelope.get('status') != 'SUCCESS':
                    raise SafetyError('provider did not succeed')
                final = envelope['response']
            else:
                if envelope.get('type') != 'result' or envelope.get('subtype') != 'success':
                    raise SafetyError('provider did not succeed')
                final = envelope['result']
        else:
            # Copilot silent text avoids depending on a changing JSONL event schema.
            final = output.strip()
        return result_validate(json.loads(final))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise SafetyError('invalid provider output; raw output was not saved')
