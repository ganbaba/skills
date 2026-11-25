"""Read-only local tool/config inventory. Never reads auth/session databases."""
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
from . import __version__
from .guard import digest


def inspect():
    home = Path.home()
    info = {"version": __version__, "python_minimum": "3.9", "tools": {}, "apps": {}, "configs": []}
    for cmd in ["python3", "git", "codex", "antigravity", "agy", "copilot", "handoff"]:
        exe = shutil.which(cmd)
        item = {"path": exe, "found": bool(exe)}
        if exe:
            try:
                r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
                item["version"] = r.stdout.strip()[:500]
                item["exit_code"] = r.returncode
            except Exception as e:
                item["error"] = type(e).__name__
        info["tools"][cmd] = item
    for name in ["ChatGPT", "Codex", "Antigravity", "Visual Studio Code"]:
        for parent in [Path("/Applications"), home / "Applications"]:
            p = parent / (name + ".app") / "Contents/Info.plist"
            if p.is_file():
                try:
                    j = plistlib.loads(p.read_bytes())
                    info["apps"][name] = {"version": j.get("CFBundleShortVersionString"), "build": j.get("CFBundleVersion")}
                except Exception:
                    pass
    names = [".codex/config.toml", ".codex/AGENTS.md", ".codex/hooks.json", ".gemini/config/hooks.json",
             ".gemini/config/GEMINI.md", ".gemini/GEMINI.md", ".copilot/copilot-instructions.md", ".copilot/settings.json",
             "Library/Application Support/Code/User/settings.json"]
    for rel in names:
        p = home / rel
        row = {"path": str(p), "exists": p.exists(), "symlink": p.is_symlink()}
        if p.is_file() and not p.is_symlink():
            row["sha256"] = digest(p.read_bytes())
        info["configs"].append(row)
    info["skill_roots"] = {str(home / p): [x.name for x in (home / p).iterdir()] if (home / p).is_dir() else []
                           for p in [".codex/skills", ".agents/skills", ".gemini/config/skills", ".copilot/skills"]}
    package = Path('/Applications/Visual Studio Code.app/Contents/Resources/app/extensions/copilot/package.json')
    if package.is_file():
        j = json.loads(package.read_text())
        info["apps"]["Copilot Chat (VS Code bundled)"] = {"version": j.get("version"), "standalone_cli": False}
    return info
