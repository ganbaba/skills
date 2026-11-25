"""Capability-aware hook payload adapters; no native agent or network invocation."""
import json
import os
from pathlib import Path
import shlex
from .guard import Tree, SafetyError, lock
from .core import initialize, require, checkpoint
from .memory import git, restore

CAPABILITIES = {
    "codex": {"documented_events": ["SessionStart", "PreCompact", "PostCompact", "Stop"],
              "source": "https://learn.chatgpt.com/docs/hooks", "runtime_verified": False,
              "activation": "Native hook trust review may be required; no trust bypass"},
    "antigravity": {"documented_events": ["SessionStart", "Stop"],
                    "unsupported_events": ["PreCompact", "PostCompact"],
                    "source": "https://antigravity.google/docs/hooks", "runtime_verified": False,
                    "fallback": "Global skill and explicit phase checkpoints before manual compaction"},
    "copilot": {"documented_events": ["sessionStart", "preCompact", "sessionEnd", "agentStop"],
                "source": "https://docs.github.com/en/copilot/reference/hooks-reference", "runtime_verified": False,
                "activation": "Shared Copilot harness config for CLI/VS Code; Local harness not assumed; native session still requires verification"},
}


def hook_configs(launcher):
    def command(agent, event):
        return shlex.quote(str(launcher)) + " hook --agent " + agent + " --event " + event
    codex = {"description": "Chorotheia local memory; runs only within initialized/eligible Git projects", "hooks": {}}
    for event in ["SessionStart", "PreCompact", "PostCompact", "Stop"]:
        codex["hooks"][event] = [{"hooks": [{"type": "command", "command": command("codex", event), "timeout": 30}]}]
    ag = {"chorotheia": {event: [{"type": "command", "command": command("antigravity", event), "timeout": 30}]
                          for event in ["SessionStart", "Stop"]}}
    cp = {"version": 1, "hooks": {event: [{"type": "command", "bash": command("copilot", event), "timeoutSec": 30}]
                                     for event in ["sessionStart", "preCompact", "sessionEnd", "agentStop"]}}
    return {"codex": codex, "antigravity": ag, "copilot": cp}


def run_hook(home, agent, event, payload):
    if event not in CAPABILITIES[agent]["documented_events"]:
        raise SafetyError("Unsupported lifecycle event for this adapter")
    settings = {"auto_init": "git-project-session-start", "initial_budget": 2400}
    if home.exists("config.toml"):
        # This v1 config deliberately uses a small TOML subset compatible with 3.9.
        # Unknown metadata keys are ignored; supported values are JSON/TOML scalars.
        for line in home.read("config.toml").decode().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if key.strip() in settings:
                    settings[key.strip()] = json.loads(value.strip())
    if settings["auto_init"] not in {"git-project-session-start", "off"}:
        raise SafetyError("Unsupported config auto_init value")
    # Do not read transcript paths, lastUserInput or tool-call arguments from payloads.
    raw = payload.get("cwd")
    if agent == "antigravity" and not raw:
        workspaces = payload.get("workspacePaths", [])
        if len(workspaces) == 1:
            raw = workspaces[0]
    if not isinstance(raw, str) or not os.path.isabs(raw):
        return {}
    path = Path(raw)
    if path == Path.home() or path == home.root or path == Path("/"):
        return {}
    top = git(path, "rev-parse", "--show-toplevel")
    if top:
        path = Path(os.fsdecode(top).strip())
    # Prevent initializing in a handoff control plane, global config or hidden store.
    if any(p.startswith(".") for p in path.parts[1:]) or path in [Path.home(), Path("/")]:
        return {}
    start = event in {"SessionStart", "sessionStart", "PostCompact"}
    with Tree(path) as tree, lock(home, path):
        from .bootstrap import discover
        m, _, _ = discover(tree)
        if not m:
            # Only Git projects auto-initialize. Non-Git folders require explicit init.
            # A payload is not authorization for arbitrary non-project directories.
            if start and top and event != "PostCompact" and settings["auto_init"] != "off":
                initialize(tree, home, capabilities=CAPABILITIES)
                m, _, _ = require(tree)
            else:
                return {}
        if start:
            text = restore(tree, m, settings["initial_budget"])["text"]
            if agent == "codex":
                return {"hookSpecificOutput": {"hookEventName": "SessionStart" if event == "SessionStart" else "PostCompact", "additionalContext": text}}
            if agent == "antigravity":
                return {"injectSteps": [{"systemMessage": {"systemMessage": text}}]}
            return {"additionalContext": text}
        # Snapshot known notes; don't pretend the hook can see or summarize live reasoning.
        current = json.loads(tree.read(m["paths"]["checkpoint"]))
        checkpoint(tree, home, current.get("summary", "Unknown"),
                   next_steps=current.get("next_steps", []), decisions=current.get("decisions", []),
                   errors=current.get("errors", []), tests=current.get("tests", []), agent=agent + ":" + event)
        return {}
