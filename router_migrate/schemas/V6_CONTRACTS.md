# v6 contracts (frozen for the v6 build; every workstream codes against these)

All state lives in `<repo>/.router-migrate/` (excluded from git via `.git/info/exclude`). Validation of the agent's
files is `router_migrate.report_v6.validate_report(doc) -> list[str]` (empty = valid; each error names the field).

## Git

- `start` requires no staged or unstaged changes to tracked files (untracked files are allowed and left alone).
- It records the starting commit as `ledger.base` and creates/checks out branch `router-migrate/migration` from HEAD.
  (If that branch already exists and is checked out, `start` reuses it and keeps `ledger.base`.)
- The migration agent commits on `router-migrate/migration`, one commit per decision a human might want to reverse.
- Each improvement is a branch `router-migrate/improve/<name>` created from the migration branch.
- The apply session works on `router-migrate/migration` (merging improvement branches, reverting commits, editing).
- `finish` requires the working tree clean (everything committed) and the migration branch checked out.
- The tool never merges into the user's own branches.

## Migration key

- Read from env `ROUTER_MIGRATE_KEY`, else the file `$ROUTER_MIGRATE_KEY_FILE`, else `~/.config/router-migrate/key`.
  `router_migrate.report_v6.migration_key() -> str | None`. Never printed, logged, written to state, or put in the
  agent's own environment.
- It is injected only into subprocesses the tool runs on the agent's behalf: `check --run CMD --live` (as
  `RAMP_ROUTER_API_KEY`) and live catalog fetches. The outside check never uses it (always router-sim).
- `ledger.live = true|false` records whether a key was available.

## `MIGRATION.json` (written by the agent)

```json
{
  "version": 6,
  "summary": "One paragraph: what the agent did and why.",
  "reviewed": "One or two sentences: what the improve review looked at; when items has no improvement, why nothing was proposed.",
  "calls": [
    {"site": "src/ai/complete.ts:50", "feature": "reply", "model": "claude-sonnet-5", "task": "chat",
     "latency": "background|interactive|user_waiting", "now": "router|provider|unknown", "item": "m1"}
  ],
  "items": [
    {"id": "m1", "kind": "move", "title": "Route the shared Anthropic client",
     "summary": "One sentence: what changes, for which calls.",
     "calls": ["src/ai/complete.ts:50", "src/ai/preflight.ts:52"],
     "gains": "…", "loses": "nothing", "price": "no change",
     "why": "A short paragraph.",
     "evidence": [{"check": "tests|typecheck|router-live|simulator|side-by-side|price|other",
                   "result": "pass|fail|info|not-run", "detail": "what ran and against what"}],
     "commit": "abc1234", "branch": null, "depends_on": [], "excludes": []}
  ]
}
```

Rules (enforced by `validate_report`):
- `version` = 6; `summary` and `reviewed` non-empty strings; `calls` and `items` lists.
- `kind` in `move | trade-off | port | stays | needs-decision | improvement | note`.
- `id` unique, `[A-Za-z0-9_.-]{1,40}`; `title` non-empty, at most 80 characters; `summary` non-empty.
- `trade-off`, `port`: `loses` non-empty and not "nothing".
- `improvement`: `branch` set and starting with `router-migrate/improve/`; `gains`, `loses`, `why` non-empty.
- `note`, `stays`, `needs-decision`: no `branch`.
- `evidence[].check` and `.result` from the sets above; `detail` a string.
- `depends_on` / `excludes` reference existing ids.
- `calls[].latency` in the set above or null; `calls[].item` references an existing id or is null.
- Optional on every item: `commit` (string or null), `price` (string), `evidence` (list), `depends_on`, `excludes`.

## `validation.json` (written by `finish` and after apply sessions)

```json
{"at": "2026-10-14T18:00:00",
 "migration": {"ref": "router-migrate/migration", "commit": "…", "ok": true,
               "failures": [{"gate": "tests|typecheck|parse|tests-edited|router|report|dirty", "detail": "…"}],
               "flags": [{"kind": "deleted-file|dependencies|provider-key|runtime-edit", "detail": "…", "files": ["…"]}],
               "tests": {"status": "passed|failed|running|skipped", "counts": {}, "baseline": {}},
               "typecheck": {"errors": 0, "baseline": 0},
               "router": {"requests": 0, "valid": 0, "invalid": []},
               "routes": {"verified": ["FILE:LINE"], "unverified": ["FILE:LINE"], "not_routing": ["FILE:LINE"], "stays": [{"site": "…", "reason": "…"}]}},
 "branches": {"router-migrate/improve/<name>": {"ref": "…", "commit": "…", "ok": true, "failures": [], "flags": [], "tests": {}, "typecheck": {}, "router": {}}}}
```

## `requests.json` (written by the dashboard server and `router-migrate request`)

```json
{"requests": {"<item id>": {"action": "apply|dismiss|postpone|undo", "reason": "…", "until": "YYYY-MM-DD",
                             "at": "…", "by": "ui|cli", "etag": "…", "session": null}}}
```
- `apply` is valid for `improvement` items; `undo` for any item already in the code (migration items, applied
  improvements); `dismiss`/`postpone` for improvements and needs-decision items.
- `etag` = `report_v6.item_etag(item)`; a request whose etag no longer matches is shown as stale.
- `session` is set to the apply session id once a session has taken the request.

## `APPLY.json` (written by the agent at the end of an apply session) and `apply_log.json`

The agent writes `.router-migrate/APPLY.json`:
```json
{"summary": "One paragraph.", "results": [{"item": "i1", "action": "apply|undo", "status": "done|declined|failed",
  "commits": ["…"], "note": "what it did, or why not"}]}
```
`router-migrate apply --done` validates it (`report_v6.validate_apply(doc, requested_ids)`), runs the outside check,
and appends a session to `apply_log.json`:
```json
{"sessions": [{"id": "A1", "started": "…", "finished": "…", "requests": ["i1", "m2"], "summary": "…",
               "results": [ … ], "check": {"ok": true, "failures": [], "tests": {}}}]}
```

## Item status (derived by `report_v6.item_status(item, requests, apply_log, validation)`)

| Status | When |
|---|---|
| `in code` | migration kinds (`move`, `trade-off`, `port`) not undone |
| `proposed` | `improvement` with no request and not applied |
| `approved` | request `apply` not yet taken by a finished session |
| `applied` | an apply session result `done` for `apply` |
| `undo requested` | request `undo` not yet taken by a finished session |
| `undone` | an apply session result `done` for `undo` |
| `dismissed` / `postponed` | request of that action |
| `declined` | the latest session result for it is `declined` or `failed` (the note says why) |
| `check failed` | an improvement whose branch failed validation |
| `info` | `stays`, `needs-decision`, `note` with no request |

## Interfaces between workstreams

- Dashboard bundle: `router_migrate.dashboard.items.bundle(root) -> dict` (dashboard workstream), built only from the
  files above plus git (diffs: `git show --stat --patch <commit>` / `git diff <migration>...<branch>`, truncated).
- Requests: `router_migrate.report_v6.record_request(root, item_id, action, reason=None, until=None, by="ui") -> dict`.
- Apply hand-off: `router_migrate.harness.launch_apply(root) -> {"mode": "headless", "session": "A2", "log": path}` when
  the `claude` CLI is logged in, else `{"mode": "command", "command": "/router-migrate apply"}`.
- Apply blocked: `router_migrate.report_v6.apply_blocked(validation) -> str | None` (the migration's check failed or
  its tests are still running).
