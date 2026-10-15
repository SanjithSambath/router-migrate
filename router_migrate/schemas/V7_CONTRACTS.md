# v7 contracts (every workstream codes against these)

All state lives in `<repo>/.router-migrate/` (excluded from git via `.git/info/exclude`). Validation of the agent's
files is `router_migrate.report_v6.validate_report(doc) -> list[str]` (empty = valid; each error names the field).

## Git

- `start` requires no staged or unstaged changes to tracked files (untracked files are allowed and left alone).
- It records the starting commit as `ledger.base` and creates/checks out branch `router-migrate/migration` from HEAD.
  (If that branch already exists and is checked out, `start` reuses it and keeps `ledger.base`.)
- The migration agent commits on `router-migrate/migration`, one commit per decision a human might want to reverse.
- A built recommendation is a branch `router-migrate/improve/<name>` created from the migration branch; a proposed
  one has no branch until an apply session builds it.
- The apply session works on `router-migrate/migration` (merging recommendation branches, reverting commits, editing).
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
  "version": 7,
  "summary": "One paragraph: what moved, what the review found, what you would do first.",
  "reviewed": "One or two sentences: what the review looked at; when there is no recommendation, why.",
  "calls": [
    {"site": "src/ai/complete.ts:50", "feature": "reply", "model": "claude-sonnet-5", "task": "chat",
     "latency": "background|interactive|user_waiting", "now": "router|provider|unknown", "item": "c1"}
  ],
  "items": [
    {"id": "c1", "kind": "change", "title": "Route the shared Anthropic client",
     "summary": "One sentence: what changes, for which calls.", "calls": ["src/ai/complete.ts:50"],
     "gains": "…", "loses": "nothing", "price": "no change", "why": "A short paragraph.",
     "evidence": [{"check": "tests|typecheck|router-live|simulator|side-by-side|price|other",
                   "result": "pass|fail|info|not-run", "detail": "what ran and against what"}],
     "commit": "abc1234"},
    {"id": "r1", "kind": "recommendation", "rank": 1, "level": "rework", "actor": "code",
     "title": "Collapse the two-level agent so answers stream sooner", "summary": "One sentence.",
     "gains": "…", "loses": "…", "price": "…", "why": "…", "branch": null,
     "measured": [{"metric": "time to first token", "before": "4.5 s", "after": null, "n": 1,
                   "how": "check --run --live driving chat() with a 10-K question"}]},
    {"id": "r2", "kind": "recommendation", "rank": 2, "level": "quick-win", "actor": "router-setting",
     "where": "app.router.com > Routing > Shadow models", "title": "Shadow gpt-6-luna before switching the RAG model",
     "summary": "…", "gains": "…", "loses": "…", "why": "…"},
    {"id": "s1", "kind": "stays", "title": "Embeddings stay on OpenAI", "summary": "…", "why": "Router has no embeddings endpoint."},
    {"id": "n1", "kind": "note", "title": "Considered and rejected: Jev auto routing", "summary": "…"}
  ]
}
```

Kinds:
- `change`: in the code already, one commit on `router-migrate/migration` (moving a call, porting it, taking a trade-off).
- `recommendation`: what you would change, ranked by value. `actor` says who carries it out:
  `code` (an apply session builds it; built and tested already if it has a branch), `router-setting` (the human
  switches it on app.router.com; `where` says where; no branch), `product-call` (the human decides first, e.g. a
  different model or a product trade-off; an apply session then builds it).
- `stays`: a call that stays where it is, and why.
- `note`: anything else worth knowing, including what you considered and rejected.

Rules (enforced by `validate_report`):
- `version` = 7; `summary` and `reviewed` non-empty strings; `calls` and `items` lists.
- `id` unique, `[A-Za-z0-9_.-]{1,40}`; `title` non-empty, at most 80 characters; `summary` non-empty.
- `change`: `commit` required.
- `recommendation`: `level` in `quick-win | change | rework`; `actor` in `code | router-setting | product-call`;
  `gains`, `loses`, `why` non-empty; `branch` null or `router-migrate/improve/<name>`; `router-setting` needs `where`
  and has no branch; `rank` a positive integer (optional: defaults to report order, 1 = most valuable).
- Only recommendations have a `branch`.
- `measured` (optional, any item): a list of `{metric, before, after, n, how}`: `before`/`after` a number or string
  (one may be null, e.g. not built yet), `n` the number of samples (>= 1), `how` what ran. Show the number you measured,
  never one you estimated.
- `evidence[].check` and `.result` from the sets above; `detail` a string.
- `depends_on` / `excludes` reference existing ids.
- `calls[].latency` in the set above or null; `calls[].item` references an existing id or is null.

v6 reports still load: `report_v6.normalize` maps `move`/`trade-off`/`port` to `change` (a trade-off or port still
needs a real `loses`), `improvement` to a `code` recommendation, `needs-decision` to a `product-call` recommendation.

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
{"requests": {"<item id>": {"action": "apply|dismiss|postpone|undo|done", "reason": "…", "until": "YYYY-MM-DD",
                             "at": "…", "by": "ui|cli", "etag": "…", "session": null}}}
```
- `apply`: recommendations with actor `code` or `product-call` (an apply session builds or merges it).
- `done`: a `router-setting` recommendation the human has switched on (it never goes to an apply session).
- `undo`: anything in the code (changes, applied recommendations). `dismiss`/`postpone`: recommendations.
- `etag` = `report_v6.item_etag(item)`; a request whose etag no longer matches is shown as stale.
- `session` is set to the apply session id when a session takes the request, with `state: "running"`; when the
  session's `apply --done` passes, `state` becomes `"done"`. A request with a session is no longer pending.

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
| `in code` | a `change` not undone |
| `proposed` | a `code` / `product-call` recommendation with no request and not applied |
| `your step` | a `router-setting` recommendation not yet marked done, dismissed or postponed |
| `done` | a `router-setting` the human marked done |
| `approved` | request `apply` not yet taken by a finished session |
| `applied` | an apply session result `done` for `apply` |
| `undo requested` | request `undo` not yet taken by a finished session |
| `undone` | an apply session result `done` for `undo` |
| `dismissed` / `postponed` | request of that action |
| `in session` | its request was taken by an apply session that hasn't finished |
| `declined` | the latest session result for it is `declined` or `failed` (the note says why) |
| `check failed` | a recommendation whose branch failed validation |
| `info` | `stays`, `note` with no request |

## Interfaces between workstreams

- Dashboard bundle: `router_migrate.dashboard.items.bundle(root) -> dict` (dashboard workstream), built only from the
  files above plus git (diffs: `git show --stat --patch <commit>` / `git diff <migration>...<branch>`, truncated).
- Requests: `router_migrate.report_v6.record_request(root, item_id, action, reason=None, until=None, by="ui") -> dict`.
- Apply hand-off: `router_migrate.harness.launch_apply(root) -> {"mode": "headless", "session": "A2", "log": path}` when
  the `claude` CLI is logged in, else `{"mode": "command", "command": "/router-migrate apply"}`.
- Apply blocked: `router_migrate.report_v6.apply_blocked(validation) -> str | None` (the migration's check failed or
  its tests are still running).
