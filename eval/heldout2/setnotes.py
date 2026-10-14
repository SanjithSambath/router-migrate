#!/usr/bin/env python3
"""Set a heldout2 recipe's notes (and optional extra JSON fields): setnotes.py <name> "<notes>" ['{"k": v}']"""
import json
import os
import sys

p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recipes", sys.argv[1] + ".json")
d = json.load(open(p))
d["notes"] = sys.argv[2]
if len(sys.argv) > 3:
    d.update(json.loads(sys.argv[3]))
json.dump(d, open(p, "w"), indent=2)
open(p, "a").write("\n")
