#!/usr/bin/env python3
"""Explicit, local install. Does not log in, download, schedule, or edit projects."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--prefix', required=True, help='New, nonexistent installation directory')
    p.add_argument('--state-home', required=True, help='Local state directory, outside projects')
    p.add_argument('--codex-skill-dir', help='Optional new destination, e.g. ~/.codex/skills/universal-handoff')
    a = p.parse_args()
    if sys.version_info < (3, 9):
        p.error('Python 3.9 or later is required')
    source = Path(__file__).resolve().parent
    prefix = Path(a.prefix).expanduser().absolute()
    state = Path(a.state_home).expanduser().absolute()
    skill = Path(a.codex_skill_dir).expanduser().absolute() if a.codex_skill_dir else None
    if prefix.exists() or prefix.is_symlink() or (skill and (skill.exists() or skill.is_symlink())):
        p.error('installation/skill destination must not exist; nothing was overwritten')
    if prefix == source or source in prefix.parents:
        p.error('install destination must be outside the unpacked package')
    if skill and (skill == source or source in skill.parents or skill == prefix or prefix in skill.parents or skill in prefix.parents or skill == state or state in skill.parents or skill in state.parents):
        p.error('skill, package, installation and state destinations must be disjoint')
    if state == prefix or state in prefix.parents or prefix in state.parents:
        p.error('installation and state directories must be disjoint')
    for target in [prefix, state] + ([skill] if skill else []):
        if any(x.is_symlink() for x in (target,) + tuple(target.parents)):
            p.error('destination must not traverse symlinks')
    prefix.mkdir(parents=True, mode=0o700)
    for name in ('handoff.py', 'handoff', 'docs', 'schemas', 'skill', 'examples', 'tests', 'README.md', 'config.example.json', 'install.py', 'install.sh'):
        src = source / name
        if src.is_dir():
            shutil.copytree(src, prefix / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        else:
            shutil.copy2(src, prefix / name)
    (prefix / 'bin').mkdir(mode=0o700)
    launcher = prefix / 'bin/handoff'
    launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(prefix / 'handoff.py'))
                        + ' --home ' + shlex.quote(str(state)) + ' "$@"\n')
    launcher.chmod(0o700)
    subprocess.run([sys.executable, str(prefix / 'handoff.py'), '--home', str(state), 'init'], check=True)
    if skill:
        skill.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(prefix / 'skill/universal-handoff', skill)
    print('Installed launcher: ' + str(launcher))
    print('No provider called; scheduler not loaded; original projects untouched.')


if __name__ == '__main__':
    main()
