"""Offline CLI fixture. Never invokes a model or reads outside cwd."""
import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--vendor', required=True)
p.add_argument('prompt')
a = p.parse_args()
start = a.prompt.index('{"protocol_version"')
r, _ = json.JSONDecoder().raw_decode(a.prompt[start:])
step = Path('offline-relay.txt')
was_present = step.exists()
step.write_text('finished\n' if was_present else 'started\n')
r.update(outcome='done' if was_present else 'continue', summary='Offline fixture made one step',
         completed=['Wrote offline-relay.txt'], next_steps=[] if was_present else ['Continue'],
         decisions=[], blockers=[], tests=['Offline fixture; no project tests run'])
text = json.dumps(r)
if a.vendor == 'codex':
    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}}))
    print(json.dumps({'type': 'turn.completed'}))
elif a.vendor == 'antigravity':
    print(json.dumps({'status': 'SUCCESS', 'response': text}))
elif a.vendor in ('claude', 'cursor'):
    print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': text}))
else:
    print(text)
