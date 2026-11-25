#!/usr/bin/env python3
"""Install locally only; no pip, registry, network, agent launch or shell profile edits."""
import argparse
import json
from pathlib import Path
from chorotheia.installation import install

p = argparse.ArgumentParser()
p.add_argument("--home", default=str(Path.home() / ".chorotheia"))
p.add_argument("--user-home", default=str(Path.home()))
p.add_argument("--no-register", action="store_true")
p.add_argument("--dry-run", action="store_true")
p.add_argument("--handoff-skill")
a = p.parse_args()
print(json.dumps(install(Path(__file__).absolute().parent, a.home, a.user_home, not a.no_register, a.dry_run, a.handoff_skill), ensure_ascii=False, indent=2))
