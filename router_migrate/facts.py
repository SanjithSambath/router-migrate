"""Fact refresh: re-measure the fact sheet's measured lines against live Router.

`router-migrate facts --refresh` runs a fixed, small probe set (well under $0.05) with the migration key and rewrites
only the lines it re-measured. A line is owned by a probe through an anchor comment, `<!-- fact:<id> -->`:
  - on a list or paragraph line, the anchor sits at the end and the whole line after its indent and list marker is
    replaced by the measured sentence (plus the anchor);
  - in a table row, the anchor sits inside a cell and only that cell is replaced.
Measured text is tagged `[R<YYYY-MM-DD>]` and the sheet's source table gains a row for that tag. Lines without an
anchor are never touched; a probe that fails leaves its lines as they were and is reported.

The key is only ever sent as the Authorization header; it is never printed, logged, returned or written.
"""
import argparse
import datetime as _dt
import difflib
import json
import os
import re
import sys
import time

import httpx

_HERE = os.path.dirname(os.path.abspath(__file__))
ROUTER = "https://api.router.com"
FLEX_PATH = os.path.join(_HERE, "data", "flex_models.json")
ANCHOR = re.compile(r"<!-- fact:([a-z0-9-]+) -->")
KEY_HELP = ("needs the migration key: set ROUTER_MIGRATE_KEY or put it in ~/.config/router-migrate/key "
            "(or the file named by ROUTER_MIGRATE_KEY_FILE)")
OUT_CAP = 16  # max_output_tokens on every generation probe (OpenAI's minimum)
_sleep = time.sleep  # tests replace it


def default_sheet():
    return os.path.join(_HERE, "data", os.environ.get("RM_FACTS") or "ROUTER.md")


def _client(api_key):
    return httpx.Client(base_url=ROUTER, timeout=60,
                        headers={"Authorization": "Bearer " + api_key, "User-Agent": "router-migrate-facts/1"})


class ProbeError(Exception):
    pass


def _post(c, body):
    """POST /v1/responses; returns (status, json body or None, headers). Raises only on transport failure or 5xx."""
    r = c.post("/v1/responses", json=body)
    if r.status_code >= 500:
        raise ProbeError("HTTP %d from /v1/responses" % r.status_code)
    try:
        j = r.json()
    except ValueError:
        j = None
    return r.status_code, j, r.headers


def _cost(j):
    try:
        return float(((j or {}).get("usage") or {}).get("cost") or 0)
    except (TypeError, ValueError):
        return 0.0


def _err(status, j):
    e = (j or {}).get("error") if isinstance(j, dict) else None
    msg = (e.get("message") if isinstance(e, dict) else None) or "no JSON error body"
    code = e.get("code") if isinstance(e, dict) else None
    return "HTTP %d%s: %s" % (status, " `%s`" % code if code else "", msg[:160])


def _usd(x):
    return "$%.6f" % x if x < 0.001 else "$%.5f" % x


# ------------------------------------------------------------------------------------------------ probes
# Each probe takes an httpx client and returns (result dict, {anchor: text with "{tag}"}, cost_usd), or raises.

_OPEN_WEIGHT = [("deepseek", "DeepSeek"), ("kimi", "Kimi"), ("glm", "GLM"), ("minimax", "MiniMax"), ("qwen", "Qwen"),
                ("nemotron", "Nemotron"), ("gpt-oss", "gpt-oss")]


def probe_catalog(c):
    r = c.get("/v1/models")
    if r.status_code != 200:
        raise ProbeError("GET /v1/models returned HTTP %d" % r.status_code)
    rows = r.json().get("data") or []
    if not rows:
        raise ProbeError("GET /v1/models returned no models")
    own = {}
    for m in rows:
        rt = m.get("router") or {}
        if rt.get("requires_byok"):
            kind = "byok"
        else:
            kind = m.get("owned_by") or "?"
        own.setdefault(kind, []).append(m["id"])
    # Router's own ids: switchyard (owned_by router) and jev-* (owned_by typesafe) are strategies, not open-weight models
    router_ids = own.pop("router", []) + own.pop("typesafe", [])
    open_weight = [i for i in router_ids if i != "switchyard" and not i.startswith("jev-")]
    fams = []
    for i in open_weight:
        name = next((n for p, n in _OPEN_WEIGHT if i.startswith(p)), i.split("-")[0])
        if name not in fams:
            fams.append(name)
    order = [n for _, n in _OPEN_WEIGHT]
    fams.sort(key=lambda n: (order.index(n) if n in order else len(order), n))
    counts = {"total": len(rows), "openai": len(own.pop("openai", [])), "anthropic": len(own.pop("anthropic", [])),
              "xai": len(own.pop("xai", [])), "open_weight": len(open_weight), "byok": len(own.pop("byok", []))}
    other = {k: len(v) for k, v in sorted(own.items())}
    ids = {m["id"] for m in rows}
    flex = json.load(open(FLEX_PATH))["flex_models"]
    listed = [i for i in flex if i in ids]
    gone = [i for i in flex if i not in ids]
    tiers = {m["id"]: [t.get("id") for t in (m.get("router") or {}).get("service_tiers") or []]
             for m in rows if (m.get("router") or {}).get("service_tiers")}
    result = dict(counts, other=other, flex_listed=len(listed), flex_unlisted=gone, catalog_service_tiers=tiers)

    other_txt = "".join(", %s (%d)" % kv for kv in other.items())
    counts_line = ("One catalog of models from many vendors behind both API surfaces (%d ids): OpenAI (%d ids), "
                   "Anthropic (%d), xAI (%d)%s, open-weight models under provider-agnostic ids (%s: %d), plus Bedrock "
                   "and Vertex (Gemini) ids that need your own provider key (%d ids, `requires_byok`) {tag}."
                   % (counts["total"], counts["openai"], counts["anthropic"], counts["xai"], other_txt,
                      ", ".join(fams), counts["open_weight"], counts["byok"]))
    flex_line = ("Where: `/v1/responses` with a single `model` that has a Flex tier: %d OpenAI ids (gpt-6.x, gpt-5.x, "
                 "o3, o4-mini; `data/flex_models.json`, measured by serving), %s in `GET /v1/models` {tag}; the "
                 "catalog itself doesn't mark Flex eligibility."
                 % (len(flex), "all listed" if not gone else "%d listed (not listed: %s)" % (len(listed), ", ".join(gone))))
    return result, {"catalog-counts": counts_line, "flex-eligible": flex_line}, 0.0


def probe_flex_tier(c):
    model = "gpt-5.4-mini"
    status, j, _ = _post(c, {"model": model, "input": "Reply with the word OK.", "max_output_tokens": OUT_CAP,
                             "reasoning": {"effort": "none"}})
    if status != 200 or not isinstance(j, dict):
        raise ProbeError(_err(status, j))
    tier = j.get("service_tier")
    u = j.get("usage") or {}
    cost = _cost(j)
    result = {"model": model, "service_tier": tier, "cost_usd": cost,
              "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")}
    if tier == "flex":
        line = ("Plain `model` requests to Flex-eligible models are served at Flex without opting in: a plain "
                "gpt-5.4-mini request came back `service_tier: \"flex\"` (usage.cost %s) {tag}; earlier sweep: "
                "gpt-5-nano 5 of 5, 13 models on Responses [v2 #3]." % _usd(cost))
    else:
        line = ("A plain gpt-5.4-mini request came back `service_tier: %s` (usage.cost %s), so Flex was not the "
                "default for this key {tag}; on the earlier test account plain requests were served at Flex "
                "(gpt-5-nano 5 of 5; 13 models on Responses) [v2 #3]." % (json.dumps(tier), _usd(cost)))
    return result, {"flex-tier": line}, cost


def _cache_prompt():
    rules = ["Rule %d: when asked about item %d, answer with the code word K%03d and say nothing else." % (i, i, i)
             for i in range(1, 61)]
    return "You answer lookups from this table.\n" + "\n".join(rules) + "\n\nReply with the word OK."


def probe_cache_responses(c, pause=2.0):
    model = "gpt-4o-mini"
    body = {"model": model, "input": _cache_prompt(), "max_output_tokens": OUT_CAP,
            "prompt_cache_key": "router-migrate-facts-refresh"}
    calls, cost = [], 0.0
    for n in range(2):
        if n:
            _sleep(pause)
        status, j, _ = _post(c, body)
        if status != 200 or not isinstance(j, dict):
            raise ProbeError("call %d: %s" % (n + 1, _err(status, j)))
        u = j.get("usage") or {}
        cached = ((u.get("input_tokens_details") or {}).get("cached_tokens")) or 0
        calls.append({"input_tokens": u.get("input_tokens"), "cached_tokens": cached, "cost_usd": _cost(j)})
        cost += _cost(j)
    a, b = calls
    result = {"model": model, "calls": calls}
    if b["cached_tokens"]:
        line = ("gpt-4o-mini, %s-token prompt with a `prompt_cache_key`: the second call cached %s tokens and cost %s "
                "instead of %s {tag}." % (format(a["input_tokens"] or 0, ","), format(b["cached_tokens"], ","),
                                          _usd(b["cost_usd"]), _usd(a["cost_usd"])))
    else:
        line = ("gpt-4o-mini, %s-token prompt with a `prompt_cache_key`: the second call, about %d s later, cached no "
                "tokens (cost %s both times) {tag}; earlier a 9,020-token prompt cached 8,832 tokens on the second "
                "call [v2 group 6]." % (format(a["input_tokens"] or 0, ","), int(pause), _usd(b["cost_usd"])))
    return result, {"cache-responses": line}, cost


def probe_fallback(c):
    bad = "not-a-real-model-xyz"
    status, j, h = _post(c, {"models": [bad, "gpt-4o-mini"], "input": "Reply with the word OK.",
                             "max_output_tokens": OUT_CAP})
    if status == 200 and isinstance(j, dict):
        served = j.get("model")
        named = bad in json.dumps(j) or any(bad in v for v in h.values())
        result = {"status": 200, "served_model": served, "provider": h.get("x-router-provider"),
                  "names_failed_candidate": named, "cost_usd": _cost(j)}
        line = ("Behaviour: falls through a missing model: `models: [\"%s\", \"gpt-4o-mini\"]` was served by %s, and the "
                "response %s the failed candidate {tag}; a model the key may not use (403) and an invalid parameter "
                "(temperature on gpt-5-nano) also fall through silently [v2 #8, M0]."
                % (bad, "`%s`" % served if served else "an unnamed model", "names" if named else "doesn't name"))
        return result, {"fallback": line}, _cost(j)
    result = {"status": status, "error": _err(status, j)}
    line = ("Behaviour: `models: [\"%s\", \"gpt-4o-mini\"]` did not fall through: %s {tag}; earlier a missing model, a "
            "model the key may not use (403) and an invalid parameter fell through silently [v2 #8, M0]."
            % (bad, result["error"]))
    return result, {"fallback": line}, 0.0


def probe_switchyard(c):
    status, j, h = _post(c, {"model": "switchyard", "input": "Hi", "max_output_tokens": OUT_CAP})
    if status == 200 and isinstance(j, dict):
        served, tier = j.get("model"), j.get("service_tier")
        result = {"status": 200, "served_model": served, "service_tier": tier,
                  "provider": h.get("x-router-provider"), "cost_usd": _cost(j)}
        known = ("works from app code once a strategy is saved: served %s at `%s` {tag}; strategy `efficient_model` / "
                 "`capable_model` [M0]; docs call it experimental and aimed at coding agents [docs]"
                 % ("`%s`" % served if served else "an unnamed model", tier))
        return result, {"switchyard": known}, _cost(j)
    result = {"status": status, "error": _err(status, j)}
    known = ("`model: \"switchyard\"` returned %s for this key {tag}; it needs a strategy saved on app.router.com "
             "(`efficient_model` / `capable_model`) [M0]; docs call it experimental and aimed at coding agents [docs]"
             % result["error"].replace("|", "/"))
    return result, {"switchyard": known}, 0.0


PROBES = [
    ("catalog", probe_catalog, ("catalog-counts", "flex-eligible")),
    ("flex-tier", probe_flex_tier, ("flex-tier",)),
    ("cache-responses", probe_cache_responses, ("cache-responses",)),
    ("fallback", probe_fallback, ("fallback",)),
    ("switchyard", probe_switchyard, ("switchyard",)),
]


# ------------------------------------------------------------------------------------------------ rewriting
_PREFIX = re.compile(r"^(\s*(?:[-*+]\s+|\d+[.)]\s+)?)")


def anchors(text):
    """{anchor id: [0-based line indexes]} for every `<!-- fact:id -->` in the sheet."""
    out = {}
    for i, line in enumerate(text.split("\n")):
        for m in ANCHOR.finditer(line):
            out.setdefault(m.group(1), []).append(i)
    return out


def rewrite_line(line, anchor_id, text):
    """The anchored line with its measured text replaced: the cell holding the anchor in a table row, else everything
    after the indent and list marker."""
    mark = "<!-- fact:%s -->" % anchor_id
    if line.lstrip().startswith("|"):
        cells = line.split("|")
        for k, cell in enumerate(cells):
            if mark in cell:
                cells[k] = " %s %s " % (text.replace("|", "/"), mark)
                return "|".join(cells)
        return line
    return _PREFIX.match(line).group(1) + text + " " + mark


def _add_source_row(lines, tag, date):
    row = "| %s | router-migrate facts --refresh, live probes on %s |" % (tag, date)
    if any(l.startswith("| %s |" % tag) for l in lines):
        return False
    for i, l in enumerate(lines):
        if re.match(r"^\|\s*Tag\s*\|\s*Source\s*\|", l):
            j = i + 1
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            lines.insert(j, row)
            return True
    return False


def _scrub(obj, key):
    s = json.dumps(obj)
    if key and key in s:
        s = s.replace(key, "***")
    return json.loads(s)


def refresh(sheet_path, api_key, out_path=None, probes=None, now=None, dry_run=False):
    """Run the probes against live Router and rewrite the anchored lines they re-measured.

    probes: probe ids to run (default: all of PROBES). now: a date or datetime for the tag (default: today).
    Writes to out_path (default: sheet_path) unless dry_run. Returns
    {"probes": [{id, ok, result, cost_usd}], "changed": [{id, line}], "total_cost_usd", "tag", "diff", "written"}.
    """
    if not api_key:
        raise ValueError(KEY_HELP)
    day = now or _dt.date.today()
    date = day.strftime("%Y-%m-%d")
    tag = "[R%s]" % date
    old = open(sheet_path).read()
    lines = old.split("\n")
    where = anchors(old)
    wanted = [p for p in PROBES if probes is None or p[0] in probes]
    report, changed, total = [], [], 0.0
    with _client(api_key) as c:
        for pid, fn, owns in wanted:
            if not any(a in where for a in owns):
                report.append({"id": pid, "ok": False, "skipped": True, "cost_usd": 0.0,
                               "result": "skipped: no %s anchor in the sheet" % "/".join(owns)})
                continue
            try:
                result, texts, cost = fn(c)
            except Exception as e:  # a failing probe leaves its lines alone
                msg = str(e) if isinstance(e, ProbeError) else "%s: %s" % (type(e).__name__, e)
                report.append({"id": pid, "ok": False, "result": "failed: " + msg, "cost_usd": 0.0})
                continue
            total += cost
            report.append({"id": pid, "ok": True, "result": result, "cost_usd": round(cost, 8)})
            for aid, text in texts.items():
                for i in where.get(aid, []):
                    new = rewrite_line(lines[i], aid, text.replace("{tag}", tag))
                    if new != lines[i]:
                        lines[i] = new
                        changed.append({"id": aid, "line": i + 1})
    if changed:
        _add_source_row(lines, tag, date)
    new_text = "\n".join(lines)
    target = out_path or sheet_path
    diff = "".join(difflib.unified_diff(old.splitlines(True), new_text.splitlines(True),
                                        sheet_path, target if not dry_run else sheet_path + " (refreshed)"))
    written = None
    if not dry_run and (new_text != old or out_path):
        with open(target, "w") as f:
            f.write(new_text)
        written = target
    out = {"probes": report, "changed": sorted(changed, key=lambda c: c["line"]), "total_cost_usd": round(total, 8), "tag": tag, "diff": diff,
           "written": written}
    return _scrub(out, api_key)


# ------------------------------------------------------------------------------------------------ CLI
def cli(args):
    """`router-migrate facts --refresh [--sheet PATH] [--dry-run] [--json]`. Returns the exit code."""
    ap = argparse.ArgumentParser(prog="router-migrate facts",
                                 description="Re-measure the fact sheet's anchored lines against live Router.")
    ap.add_argument("--refresh", action="store_true", help="run the live probes (needs the migration key)")
    ap.add_argument("--sheet", default=None, help="fact sheet to refresh (default: %s)" % default_sheet())
    ap.add_argument("--out", default=None, help="write the refreshed sheet here instead of over --sheet")
    ap.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args(list(args))
    if not a.refresh:
        ap.print_usage(sys.stderr)
        print("router-migrate facts: nothing to do (pass --refresh)", file=sys.stderr)
        return 2
    from . import report_v6
    key = report_v6.migration_key()
    if not key:
        print("router-migrate facts --refresh " + KEY_HELP + ". Nothing was changed.", file=sys.stderr)
        return 2
    sheet = a.sheet or default_sheet()
    res = refresh(sheet, key, out_path=a.out, dry_run=a.dry_run)
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        for p in res["probes"]:
            r = p["result"]
            print("%-16s %s  %s" % (p["id"], "ok  " if p["ok"] else "FAIL", r if isinstance(r, str) else json.dumps(r)))
        print("changed: %s" % (", ".join("%s (line %d)" % (c["id"], c["line"]) for c in res["changed"]) or "nothing"))
        print("cost: $%.5f" % res["total_cost_usd"])
        if res["diff"]:
            print(res["diff"])
        print("written: %s" % (res["written"] or ("nothing (dry run)" if a.dry_run else "nothing")))
    return 0 if all(p["ok"] or p.get("skipped") for p in res["probes"]) else 1


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
