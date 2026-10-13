"""Vendored runtime templates (router_runtime.py / routerRuntime.ts) and their catalog embedding."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PLACEHOLDER = "__ROUTER_CATALOG__"


def compact_catalog(catalog):
    """{models: {id: {o: owned_by, t: temperature_supported, r: reasoning}}, aliases: {alias: id}} for the runtimes."""
    models, aliases = {}, {}
    for m in catalog.models:
        if m.get("requires_byok") or "responses" not in (m.get("surfaces") or []) and "messages" not in (m.get("surfaces") or []):
            continue
        models[m["id"]] = {"o": m.get("owned_by"), "t": m.get("temperature"), "r": bool(m.get("reasoning"))}
        for a in m.get("aliases") or []:
            aliases.setdefault(a, m["id"])
    try:
        dated = {k: v for k, v in json.load(open(os.path.join(os.path.dirname(HERE), "data", "dated_equivalents.json")))["dated_to_catalog"].items() if v in models}
    except (OSError, ValueError, KeyError):
        dated = {}
    return {"models": models, "aliases": aliases, "dated": dated}


def render(name, catalog):
    src = open(os.path.join(HERE, name)).read()
    blob = json.dumps(compact_catalog(catalog), separators=(",", ":"), sort_keys=True)
    if name.endswith(".ts"):  # embedded in String.raw`...`: keep it template-safe; JSON.parse decodes the \u escapes
        blob = blob.replace("`", "\\u0060").replace("${", "$\\u007b")
    elif name.endswith(".py"):  # embedded in r"""...""" 
        blob = blob.replace('"""', '\\u0022\\u0022\\u0022')
    return src.replace(PLACEHOLDER, blob)
