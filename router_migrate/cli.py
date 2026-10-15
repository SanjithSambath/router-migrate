"""router-migrate: move a codebase's production LLM traffic onto Ramp's Router, without breaking it. An agent does the work
(migrate, review the product and rank what to change, apply what you approve, undo what you reject); this CLI gives it facts, a sandbox, Router access and an
outside check it can't influence.

In Claude Code (any model): /router-migrate, or
  router-migrate start  [PATH]        clean tree required; branch router-migrate/migration; prints the agent's instructions
  ...the session migrates, reviews the product as its CTO, commits, and writes .router-migrate/MIGRATION.json...
  router-migrate finish [PATH]        the outside check on the migration and every built recommendation; the dashboard
  router-migrate apply  [PATH]        hand your approvals and undos to the agent; it ends with `router-migrate apply --done`

Headless (CI, evals):
  router-migrate run PATH --agent claude [--model M]   start -> agent -> finish
  router-migrate apply PATH --agent claude              the apply session, headless

Agent tools (run from the repository root; FILE:LINE accepts a comma-separated list):
  router-migrate check --all | FILE:LINE    does each call route to Router now, and why not
  router-migrate check --run "CMD" [--live] run CMD against the local Router simulator, or the real Router with the migration key
  router-migrate check --final [--tests]    dry run of the outside check
  router-migrate levers [MODEL|FILE:LINE]   Router levers, catalog prices and same-family models (live with a key)
  router-migrate wrap | unwrap FILE:LINE    exact codemods at a client construction (optional)
  router-migrate adapt FILE:LINE --LEVER --loses "..."   a policy trade-off: drop a parameter, map a model (optional)
  router-migrate policy                     compile a hand-edited router.policy.json for the runtime

Review (or use the dashboard):
  router-migrate dashboard [PATH]           REPORT.html + the live dashboard
  router-migrate request ITEM apply|undo|dismiss|postpone|done

Maintainers:
  router-migrate facts --refresh [--dry-run]  re-measure the fact sheet's anchored facts on real Router (needs the key)

The migration key (optional; enables live checks): ~/.config/router-migrate/key or ROUTER_MIGRATE_KEY. Never printed.
Nothing changes in production until RAMP_ROUTER_API_KEY is set in the app's environment; ROUTER_MIGRATE_DISABLE=1 turns it off.
Earlier pipelines (scan, plan, verify, report, up, recommend, decide, status, probe, claim, card, improve, judge) were
archived: see archive/README.md.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

from . import __version__

HERE = os.path.dirname(os.path.abspath(__file__))
TS_TOOL = os.path.join(HERE, "ts", "tool.mjs")


def node_ok():
    return shutil.which("node") is not None and os.path.exists(TS_TOOL)


def run_ts(args, timeout=600):
    """The TS/JS scanner and codemods (router_migrate/ts/tool.mjs): `scan ROOT` or `apply ROOT --runtime F --sites F`."""
    r = subprocess.run(["node", TS_TOOL] + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("ts tool failed: %s" % (r.stderr.strip()[-2000:] or r.stdout[-500:]))
    return json.loads(r.stdout)


ORDER = ["start", "finish", "run", "check", "levers", "wrap", "unwrap", "adapt", "policy", "dashboard", "request", "apply"]


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if args[:1] == ["facts"]:  # maintainers: re-measure the fact sheet's anchored lines with the migration key
        from .facts import cli as facts_cli
        return facts_cli(args[1:])
    ap = argparse.ArgumentParser(prog="router-migrate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version="router-migrate " + __version__)
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("run", help="headless: start -> agent -> finish (--agent claude [--model M])")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--agent", choices=["claude"], required=True, help="run a headless Claude Code session as the agent")
    s.add_argument("--arm", choices=["min", "free"], help="eval label (eval/run.py passes it; the run is the same)")
    s.add_argument("--model", help="agent model (default: the agent CLI's default)")
    s.add_argument("--agent-timeout", type=int, default=2700, help="seconds before the agent run is stopped")
    s.add_argument("--test-cmd", help="test command (default: auto-detect)")
    s.add_argument("--typecheck-cmd", help="typecheck command; its error count is the baseline finish compares against")
    s.add_argument("--no-dashboard", action="store_true", help="write REPORT.html but don't serve the live dashboard")
    s.add_argument("--no-browser", action="store_true", help="serve the dashboard without opening a browser")
    s = sub.add_parser("apply", help="hand your approvals and undos to the agent (prints the session for Claude Code; "
                                     "--agent claude runs it; --done finishes it)")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--done", action="store_true", help="the apply session is finished: validate APPLY.json, re-check, log it")
    s.add_argument("--agent", choices=["claude"], help="run the apply session headless instead of printing it")
    s.add_argument("--model")
    s.add_argument("--agent-timeout", type=int, default=2700)
    s.add_argument("--session", help=argparse.SUPPRESS)
    from . import agent as agentmod
    agentmod.add_parsers(sub)
    acts = {x.dest: x for x in sub._choices_actions}
    sub._choices_actions = [acts[n] for n in ORDER if n in acts]  # in the order a user meets them
    sub.metavar = "{%s}" % ",".join(ORDER)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    return agentmod.dispatch(a)


if __name__ == "__main__":
    sys.exit(main())
