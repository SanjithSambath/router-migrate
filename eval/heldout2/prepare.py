#!/usr/bin/env python3
"""Clone the heldout2 set at its pinned commits and build each repo's env from its recipe (wraps eval/prepare_set.py).

  .venv/bin/python eval/heldout2/prepare.py [--only a,b] [--no-setup]

Same layout as the other sets, under WORK = eval/work/heldout2: pristine/<name>, base/<name>, envs/<name>.
Registers the set in run_eval.SETS at runtime (run_eval.py itself needs the same one-line entry; see README in the report).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import run_eval as RE  # noqa: E402

RE.SETS.setdefault("heldout2", (HERE, os.path.join(RE.EVAL, "work", "heldout2")))
import prepare_set  # noqa: E402

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "--set", "heldout2"] + sys.argv[1:]
    prepare_set.main()
