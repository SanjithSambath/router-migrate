"""Adversarial review: run the Python codemod (scan -> plan -> apply via the real CLI) on tricky synthetic repos,
then import / run the result with Router OFF (RAMP_ROUTER_API_KEY unset) to see what breaks.

Usage: .venv/bin/python research/review_scratch/codemod_cases.py
Writes repos under research/review_scratch/codemod_repos/ (recreated each run).
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CLI = os.path.join(ROOT, "bin", "router-migrate")
PY = os.path.join(ROOT, ".venv", "bin", "python")
OUT = os.path.join(HERE, "codemod_repos")

ENV = {k: v for k, v in os.environ.items() if k not in ("RAMP_ROUTER_API_KEY",)}
ENV["OPENAI_API_KEY"] = "sk-test-not-real"


def write(repo, files):
    d = os.path.join(OUT, repo)
    shutil.rmtree(d, ignore_errors=True)
    for rel, src in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(src)
    return d


def migrate(d):
    for cmd in ("scan", "plan", "apply"):
        r = subprocess.run([CLI, cmd, d], capture_output=True, text=True, env=ENV)
        if r.returncode:
            print("   %s failed: %s" % (cmd, r.stderr[-400:]))
    plan = json.load(open(os.path.join(d, ".router-migrate", "plan.json")))
    return plan


def outcomes(plan):
    return [(s["file"], s["line"], s["surface"], s["outcome"]) for s in plan["sites"]]


def run(d, args, cwd=None):
    r = subprocess.run([PY] + args, cwd=cwd or d, capture_output=True, text=True, env=ENV)
    tail = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode, (tail[-1] if tail else "")


CLIENT = "from openai import OpenAI\n\nclient = OpenAI()\n\ndef ask(q):\n    return client.chat.completions.create(model='gpt-4o-mini', messages=[{'role':'user','content':q}])\n"


def case(name, files, checks):
    print("== %s" % name)
    d = write(name, files)
    before = [run(d, a, cwd) for a, cwd in checks]
    plan = migrate(d)
    print("   plan outcomes:", outcomes(plan))
    after = [run(d, a, cwd) for a, cwd in checks]
    for (a, _), b, c in zip(checks, before, after):
        print("   check %-45s before: exit=%s %-40s | after: exit=%s %s" % (" ".join(a)[:45], b[0], b[1][:40], c[0], c[1][:120]))
    return d, plan


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)

    # 1. PEP 420 namespace package (no __init__.py), imported as app.main from the repo root (FastAPI/uvicorn layout).
    case("ns_package", {"app/main.py": CLIENT, "pyproject.toml": "[project]\nname='x'\n"},
         [(["-c", "import app.main; print('import ok')"], None)])

    # 2. Script inside a package, run directly (python pkg/sub/tool.py): relative import fails, absolute fallback looks in pkg/sub/.
    case("script_in_package", {"pkg/__init__.py": "", "pkg/sub/__init__.py": "",
                               "pkg/sub/tool.py": CLIENT + "\nif __name__ == '__main__':\n    print('script ok')\n"},
         [(["pkg/sub/tool.py"], None), (["-c", "import pkg.sub.tool; print('import ok')"], None)])

    # 3. A pre-existing router_runtime.py that belongs to the user (e.g. a request router) is silently overwritten.
    d = write("preexisting_runtime", {"svc/__init__.py": "", "svc/router_runtime.py": "def route(x):\n    return 'user code'\n",
                                      "svc/llm.py": CLIENT, "svc/api.py": "from .router_runtime import route\n"})
    print("== preexisting_runtime")
    print("   before: svc/router_runtime.py starts with %r" % open(os.path.join(d, "svc/router_runtime.py")).read()[:30])
    plan = migrate(d)
    print("   after:  svc/router_runtime.py starts with %r" % open(os.path.join(d, "svc/router_runtime.py")).read()[:30])
    print("   import svc.api ->", run(d, ["-c", "import svc.api; print('ok')"]))

    # 4. Idempotency: run scan/plan/apply a second time on an already-migrated repo.
    d = write("idempotent", {"svc/__init__.py": "", "svc/llm.py": CLIENT})
    print("== idempotent")
    p1 = migrate(d)
    s1 = open(os.path.join(d, "svc/llm.py")).read()
    p2 = migrate(d)
    s2 = open(os.path.join(d, "svc/llm.py")).read()
    print("   run1 outcomes:", outcomes(p1), "summary migrated=%s" % p1["summary"]["migrated"])
    print("   run2 outcomes:", outcomes(p2), "summary migrated=%s" % p2["summary"]["migrated"])
    print("   file unchanged by run2:", s1 == s2)
    # add a second client to the already-patched file and re-run
    open(os.path.join(d, "svc/llm.py"), "a").write("\nfrom anthropic import Anthropic\nclaude = Anthropic()\n\ndef ask2(q):\n    return claude.messages.create(model='claude-haiku-4-5', max_tokens=10, messages=[{'role':'user','content':q}])\n")
    p3 = migrate(d)
    print("   run3 (new Anthropic client added) outcomes:", outcomes(p3))
    print("   run3 Anthropic() wrapped:", "_router_wrap_anthropic(Anthropic())" in open(os.path.join(d, "svc/llm.py")).read())

    # 5. Header shapes: shebang + encoding + docstring + __future__ + TYPE_CHECKING + try/except import with fallback assignment.
    src = ("#!/usr/bin/env python\n# -*- coding: utf-8 -*-\n\"\"\"Doc.\"\"\"\nfrom __future__ import annotations\n"
           "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from openai.types import Model\n"
           "try:\n    from openai import OpenAI\nexcept ImportError:  # optional dep\n    OpenAI = None\n"
           "__all__ = ['client']\nclient = OpenAI() if OpenAI else None\n\ndef f():\n    return client.chat.completions.create(model='gpt-4o', messages=[])\n")
    d, _ = case("headers", {"lib/__init__.py": "", "lib/m.py": src}, [(["-c", "import lib.m; print('import ok')"], None)])
    print("   --- patched lib/m.py (first 20 lines) ---")
    print("   " + "\n   ".join(open(os.path.join(d, "lib/m.py")).read().splitlines()[:20]))

    # 6. Client created inside a library factory and passed in: call sites linked repo-wide by attribute name.
    case("by_name_link", {"a/__init__.py": "", "a/openai_client.py": "from openai import OpenAI\nclient = OpenAI()\n",
                          "b/__init__.py": "",
                          "b/groq_agent.py": "import openai\nclass Agent:\n    def __init__(self, client):\n        self.client = client  # e.g. openai.OpenAI(base_url='https://api.groq.com/openai/v1') built by the caller\n"
                                             "    def run(self):\n        return self.client.chat.completions.create(model='llama-3.3-70b', messages=[])\n"},
         [])


if __name__ == "__main__":
    main()
