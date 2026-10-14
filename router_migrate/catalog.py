"""Router model catalog: bundled snapshot of GET /v1/models, optional live refresh, and model-name mapping.

Mapping rules come from live measurements (see ~/code/router-live-check):
  - only undated catalog ids (or listed aliases) are accepted; dated ids such as gpt-4o-mini-2024-07-18 return 404
  - provider-prefixed ids (openai:gpt-4o-mini) return 404 as `model`
  - response.model echoes the dated provider id, so it must never be fed back as a request model
"""
import json
import os
import re
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(_HERE, "data", "catalog.json")
ROUTER_V1 = "https://api.router.com/v1"

EQUIV_PATH = os.path.join(_HERE, "data", "dated_equivalents.json")
_PREFIX = re.compile(r"^(openai|anthropic)[:/]")


def _equivalents():
    try:
        return json.load(open(EQUIV_PATH))["dated_to_catalog"]
    except (OSError, ValueError, KeyError):
        return {}


class Catalog:
    def __init__(self, models, source):
        self.models = models
        self.source = source
        self.by_id = {m["id"]: m for m in models}
        self.equiv = _equivalents()
        self.alias = {}
        for m in models:
            for a in m.get("aliases") or []:
                self.alias.setdefault(a, m["id"])

    @classmethod
    def load(cls, live=False, api_key=None, base=ROUTER_V1, timeout=15):
        """Bundled snapshot by default; with live=True and a key, fetch GET /v1/models (falls back to the snapshot)."""
        if live and api_key:
            try:
                req = urllib.request.Request(
                    base.rstrip("/") + "/models",
                    headers={"Authorization": "Bearer " + api_key, "User-Agent": "router-migrate/0.1"},
                )
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    rows = json.load(r).get("data", [])
                return cls([_compact(x) for x in rows], "live " + base + "/models")
            except Exception as e:  # network or auth problem: the snapshot is still a correct, measured catalog
                snap = cls.load()
                snap.source += " (live fetch failed: %s)" % type(e).__name__
                return snap
        d = json.load(open(SNAPSHOT))
        return cls(d["models"], "bundled snapshot (%s)" % d["meta"]["source"])

    def resolve(self, name):
        """Map a model name used in code to a Router request id. Returns (router_id or None, reason)."""
        if not isinstance(name, str) or not name:
            return None, "model name is not a static string"
        if name in self.by_id:
            m = self.by_id[name]
            if m.get("requires_byok"):
                return None, "catalog model requires bring-your-own-key"
            return name, "exact catalog id"
        if name in self.alias:
            return self.alias[name], "catalog alias"
        ci = next((k for k in self.by_id if k.lower() == name.lower() and not self.by_id[k].get("requires_byok")), None)
        if ci:
            return ci, "catalog id (case differs)"
        base = _PREFIX.sub("", name)
        if base in self.equiv and self.equiv[base] in self.by_id:
            return self.equiv[base], "dated snapshot Router itself serves as this catalog id (measured)"
        if base != name and base in self.by_id:
            return base, "provider prefix removed"
        if re.search(r"-(\d{4}-\d{2}-\d{2}|\d{8}|\d{4})$", base):
            return None, "dated snapshot with no measured Router equivalent (the undated id may be a newer snapshot)"
        return None, "not in Router catalog"

    def info(self, router_id):
        return self.by_id.get(router_id)


def _compact(r):
    rt = r.get("router") or {}
    caps = rt.get("capabilities") or {}
    reasoning = caps.get("reasoning") or {}
    return {
        "id": r["id"], "owned_by": r.get("owned_by"), "aliases": rt.get("aliases") or [],
        "provider_model": rt.get("provider_model"), "catalog_id": rt.get("catalog_id"),
        "providers": [p.get("provider") for p in (rt.get("providers") or [])],
        "surfaces": rt.get("surfaces") or [], "status": rt.get("status"),
        "requires_byok": bool(rt.get("requires_byok")), "temperature": caps.get("temperature"),
        "reasoning": bool(reasoning.get("supported")),
        "efforts": [e.get("value") for e in (reasoning.get("efforts") or [])],
        "tools": (caps.get("tools") or {}).get("supported"), "structured_outputs": caps.get("structured_outputs"),
        "input_modalities": ((caps.get("modalities") or {}).get("input")) or [],
        "max_output_tokens": (rt.get("limits") or {}).get("max_output_tokens"),
        "pricing": {k: (rt.get("pricing") or {}).get(k) for k in ("input", "output", "cache_read_input")},
    }
