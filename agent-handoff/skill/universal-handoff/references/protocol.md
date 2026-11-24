# Handoff v1

The controller passes a fixed `task_id`, random `run_id`, and `base_revision`. Return **only** this JSON object, with exactly these fields:

```json
{
  "protocol_version": 1,
  "task_id": "supplied-task-id",
  "run_id": "supplied-run-id",
  "base_revision": 12,
  "outcome": "continue",
  "summary": "Observed changes and current condition",
  "completed": ["Specific completed work"],
  "next_steps": ["Next bounded action"],
  "decisions": ["Decision and reason"],
  "blockers": [],
  "tests": ["Command, observed result; or explicitly not run and why"]
}
```

`continue` allows the authorized route to take another turn. `done` stops for human review. `blocked` stops for attention. Do not call a denied or unexecuted action successful. Completion claims and test strings are untrusted reports, not independent validation.

Summary: 1–4000 characters. Each list: at most 40 strings of at most 2000 characters. Whole result: at most 64,000 UTF-8 bytes. No extra fields, fences, secrets or full source dumps. Identifiers must match the supplied token; the base revision is fixed for that run even when the controller updates heartbeat revisions.

The controller rejects malformed, stale, replayed, cross-task and secret-bearing results. Native CLI envelope formats differ; controller adapters extract the final message before validating this portable result. Each agent starts with a fresh native session and the last validated portable summary.

State and code snapshots are separate. `state.json` is authoritative. `HANDOFF.md` is a derived human-readable view and may lag after a crash. Checkpoints preserve a stable, scanned file tree; they do not capture unsaved editor buffers or a model's live context. During a crash, the most recent portable summary can be older than the most recent code snapshot. Reinspect the files before resuming.
