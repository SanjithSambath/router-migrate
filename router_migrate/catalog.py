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

# Dated or versioned suffixes providers append to a base model id.
_DATE_SUFFIXES = [
    re.compile(r"-(\d{4}-\d{2}-\d{2})$"),        # gpt-4o-2024-08-06
    re.compile(r"-(\d{8})$"),                     # claude-3-5-sonnet-20241022, claude-sonnet-4-5-20250929
    re.compile(r"-(\d{4})$"),                     # gpt-4-0613, gpt-3.5-turbo-0125
    re.compile(r"-(preview|latest)$"),            # gpt-4-turbo-preview, claude-3-5-sonnet-latest
    re.compile(r"-v\d+:\d+$"),                    # bedrock-style -v1:0
]
_PREFIXES = [re.compile(r"^(openai|anthropic|vertex|bedrock|xai|fireworks|baseten)[:/]")]

# Well-known older names that map onto a catalog id with the same model family. Only exact equivalents:
# these are the same model under another name, never a different model.
EQUIVALENTS = {
    "gpt-4o-mini-2024-07-18": "gpt-4o-mini",
    "chatgpt-4o-latest": None,  # not the same model as gpt-4o; keep on provider
    "claude-3-5-haiku-latest": None,
}


class Catalog:
    def __init__(self, models, source):
        self.models = models
        self.source = source
        self.by_id = {m["id"]: m for m in models}
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
        if name in EQUIVALENTS:
            tgt = EQUIVALENTS[name]
            return (tgt, "dated id of the same model") if tgt else (None, "no same-model equivalent in catalog")
        base = name
        for p in _PREFIXES:
            base = p.sub("", base)
        for s in _DATE_SUFFIXES:
            stripped = s.sub("", base)
            if stripped != base and stripped in self.by_id and not self.by_id[stripped].get("requires_byok"):
                return stripped, "dated/prefixed id -> undated catalog id (same model)"
        if base != name and base in self.by_id:
            return base, "provider prefix removed"
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
