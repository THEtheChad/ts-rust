#!/usr/bin/env python3
"""`quality.sh gating <rev>`: diagnostics, verification and test-hook code that is still in the
shipped build, found in rustc's cfg-expanded source (qexpand.py), not by reading cfg attributes.

The convention (docs/perf-rearch/brief.md, "Verification code is gated whole") and the rules
(docs/perf-rearch/quality.md section 8):

- env     a GOPORT_*/TSC_RS_*/TSGO_* name of class diagnostic, verification or test-hook (quality.py
          `hatch_class`) is read in the default build.
- module  a module of the default build has a reserved name: `diag`, `test_hooks`, or a name ending
          in `_diag`, `_verify`, `_dump`, `_stats` or `_times`. Diagnostic code lives in such a
          module, and the module is declared under the feature. A module that is a stateless no-op
          stub passes (`stub_check`: no static, thread-local, atomic, lock, cell, clock or env read,
          and no fn body over 16 tokens outside derived impls): the `off` timers of phase_times.
- topic   an item of the default build is named after a gated env name: its name holds the env
          name's topic (the name less `GOPORT_`, two words or more: `checker_call_log` for
          `GOPORT_CHECKER_CALL_LOG`), and that env name is of class diagnostic, verification or
          test-hook by its own words (not only because it is feature-only).
- dead    an item of the default build that no code of the default build names, while the
          diagnostics/test-hooks build does: support code of gated code that ships (Q3's
          `full_tables`, `assert_same_tables` before its fix).
- name    a fn of the default build named `assert_same*`.

With --wasm the wasm build (wasm32-wasip1, ts_wasm) is checked too (env, module, topic, name).

A finding passes when scripts/perf-rearch/gating-allow.txt lists it with a reason:
`<rule> <subject>  # <reason>`. The subject is the env name (env), the module path (module), or
`<file>::<item>` (topic, dead, name; the file relative to crates/ts_goport/src/); a module path
starts with its crate (`ts_goport::program::reuse_verify`). Exit 1 when a
finding is not allowed, 0 otherwise; allow entries that match nothing are listed as stale.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import qexpand  # noqa: E402

ALLOW = HERE / "gating-allow.txt"
GATED_CLASSES = ("diagnostic", "verification", "test-hook")
RESERVED_MOD = re.compile(r"^(diag|test_hooks)$|_(diag|verify|dump|stats|times)$")
NAME_RULE = re.compile(r"^assert_same")
SRC_PREFIX = "crates/ts_goport/src/"

# words of expanded source, skipping strings, chars and comments (expanded source has doc comments)
WORDS_RE = re.compile(r"""r(\#*)"[\s\S]*?"\1|b?"[^"\\]*(?:\\[\s\S][^"\\]*)*"|b?'(?:[^'\\\n]|\\[^\n]+?)'|//[^\n]*|/\*[\s\S]*?\*/|([A-Za-z_][A-Za-z0-9_]*)""")
ITEM_KW = {"fn", "struct", "enum", "static", "const", "trait", "type", "union"}


def scan(exp: dict) -> tuple[Counter, dict[str, list[dict]]]:
    """Word counts of one build, and its item definitions by name ({kind, file, modpath})."""
    words: Counter = Counter()
    defs: dict[str, list[dict]] = defaultdict(list)
    for p in exp["pieces"]:
        prev = prev2 = None
        for m in WORDS_RE.finditer(p["text"]):
            w = m.group(2)
            if w is None:
                continue
            words[w] += 1
            if prev in ITEM_KW or (prev == "mut" and prev2 == "static"):
                kind = prev if prev != "mut" else "static"
                if w != "_" and not (kind == "fn" and w == "main"):
                    defs[w].append({"kind": kind, "file": p["file"], "modpath": p["modpath"]})
            prev2, prev = prev, w
    return words, defs


# A shipped stub holds no state and does nothing: no static, thread-local, atomic, lock, cell,
# clock or env read, and no fn body (derived impls aside) over STUB_MAX_TOKENS tokens.
STATE_WORDS = {"static", "thread_local", "LocalKey", "Mutex", "RwLock", "OnceLock", "OnceCell", "LazyLock",
               "Instant", "SystemTime", "RefCell", "Cell", "UnsafeCell", "var", "var_os", "vars"}
STUB_MAX_TOKENS = 16


def module_text(exp: dict, m: dict) -> str:
    """The expanded text of one module of a build: its piece, or for an inline module its block
    inside its file's piece."""
    cands = [p for p in exp["pieces"] if p["crate"] == m["crate"] and p["file"] == m["file"]
             and (m["modpath"] == p["modpath"] or m["modpath"].startswith(p["modpath"] + "::"))]
    if not cands:
        return ""
    p = max(cands, key=lambda p: len(p["modpath"]))
    if not m["inline"] or p["modpath"] == m["modpath"]:
        return p["text"]
    want = m["modpath"][len(p["modpath"]) + 2 :].split("::")
    toks = qexpand.tokens(p["text"])
    stack: list[tuple[str, int]] = []
    depth = 0
    for i, (k, t, pos) in enumerate(toks):
        if t == "{":
            depth += 1
            if i >= 2 and toks[i - 2][1] == "mod" and toks[i - 1][0] == "id":
                stack.append((toks[i - 1][1], depth))
                if [n for n, _ in stack] == want:
                    start = pos + 1
            continue
        if t == "}":
            if stack and stack[-1][1] == depth:
                if [n for n, _ in stack] == want:
                    return p["text"][start:pos]
                stack.pop()
            depth -= 1
    return ""


def stub_check(text: str) -> str | None:
    """None when the module text is a stateless no-op stub; else what it holds."""
    toks = [(k, t) for k, t, _ in qexpand.tokens(text)]
    state = sorted({t for k, t in toks if k == "id" and (t in STATE_WORDS or t.startswith("Atomic"))})
    if state:
        return "state: " + ", ".join(state[:6])
    n = len(toks)
    close: dict[int, int] = {}
    st: list[int] = []
    for i, (_, t) in enumerate(toks):
        if t == "{":
            st.append(i)
        elif t == "}" and st:
            close[st.pop()] = i
    # the impls rustc's derives made: `#[automatically_derived] impl ... { ... }`
    derived: list[tuple[int, int]] = []
    for i, (_, t) in enumerate(toks):
        if t == "automatically_derived":
            j = i
            while j < n and toks[j][1] != "impl":
                j += 1
            while j < n and toks[j][1] != "{":
                j += 1
            if j < n:
                derived.append((i, close.get(j, n)))
    for i, (k, t) in enumerate(toks):
        if t != "fn" or i + 1 >= n or toks[i + 1][0] != "id" or any(a <= i <= b for a, b in derived):
            continue
        depth, j = 0, i + 2
        while j < n:
            u = toks[j][1]
            if u in ("(", "["):
                depth += 1
            elif u in (")", "]"):
                depth -= 1
            elif depth == 0 and u == ";":
                break
            elif depth == 0 and u == "{":
                size = close.get(j, n) - j - 1
                if size > STUB_MAX_TOKENS:
                    return f"fn {toks[i + 1][1]} has a body of {size} tokens"
                break
            j += 1
    return None


def short(f: str) -> str:
    return f.removeprefix(SRC_PREFIX)


def source_line(src_an: dict, file: str, kind: str, name: str) -> str:
    """The line of `<kind> <name>` in the source file (`~` when the file has more than one)."""
    a = src_an.get(file)
    if not a:
        return "?"
    t, ln = a["texts"], a["lines"]
    hits = [ln[i + 1] for i in range(len(t) - 1) if t[i + 1] == name and (t[i] == kind or (kind == "static" and t[i] == "mut"))]
    if not hits:
        return "?"
    return f"{'' if len(hits) == 1 else '~'}{hits[0]}"


def load_allow(path: Path = ALLOW) -> dict[tuple[str, str], str]:
    out = {}
    if not path.exists():
        return out
    for n, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        body, _, reason = line.partition("#")
        parts = body.split()
        if len(parts) != 2 or not reason.strip():
            sys.exit(f"{path}:{n}: want `<rule> <subject>  # <reason>`, got: {raw}")
        out[(parts[0], parts[1])] = reason.strip()
    return out


def build_findings(build: str, exp: dict, hatches: list[dict], src_an: dict) -> list[dict]:
    """env, module, topic and name findings of one build."""
    out = []
    for h in hatches:
        if h["read_in_prod"] and h["class"] in GATED_CLASSES:
            out.append({"rule": "env", "subject": h["name"], "build": build, "where": [short(x) for x in h["sites"][:3]],
                        "detail": f"{h['class']} env name read in the {build} build"})
    for m in exp["modules"]:
        name = m["modpath"].rsplit("::", 1)[-1]
        if m["modpath"] != "crate" and RESERVED_MOD.search(name):
            held = stub_check(module_text(exp, m))
            if held is None:
                continue  # a stateless no-op stub (`off`): what a gated module may leave behind
            out.append({"rule": "module", "subject": m["crate"] + m["modpath"].removeprefix("crate"),
                        "build": build, "where": [short(m["file"])],
                        "detail": f"reserved module name `{name}`{' (inline)' if m['inline'] else ''}, not a stub ({held})"})
    topics = {}
    for h in hatches:
        # a topic only from a name that its own words class (`_LOG`, `_STATS`, `VERIFY`, `_TEST_`):
        # a feature-only name classed test-hook by default (GOPORT_RESOLVE_AHEAD) names a product
        # mechanism, not diagnostics code
        if h["class"] in GATED_CLASSES and h["name"].startswith("GOPORT_") and h.get("class_by_name"):
            t = h["name"].removeprefix("GOPORT_").lower()
            if "_" in t:
                topics[t] = h["name"]
    _, defs = scan(exp)
    for name, ds in sorted(defs.items()):
        low = name.lower()
        for t, env in topics.items():
            if t in low:
                for d in ds:
                    out.append({"rule": "topic", "subject": f"{short(d['file'])}::{name}", "build": build,
                                "where": [f"{short(d['file'])}:{source_line(src_an, d['file'], d['kind'], name)}"],
                                "detail": f"{d['kind']} named after the gated {env}"})
                break
        if NAME_RULE.search(name):
            for d in ds:
                if d["kind"] == "fn":
                    out.append({"rule": "name", "subject": f"{short(d['file'])}::{name}", "build": build,
                                "where": [f"{short(d['file'])}:{source_line(src_an, d['file'], 'fn', name)}"],
                                "detail": "verifier name (`assert_same*`)"})
    return out


def dead_findings(default: dict, feats: dict, src_an: dict) -> list[dict]:
    wd, dd = scan(default)
    wf, df = scan(feats)
    out = []
    for name, ds in sorted(dd.items()):
        if wd[name] - len(ds) > 0:
            continue
        refs_f = wf[name] - len(df.get(name, []))
        if refs_f <= 0:
            continue
        for d in ds:
            out.append({"rule": "dead", "subject": f"{short(d['file'])}::{name}", "build": "default",
                        "where": [f"{short(d['file'])}:{source_line(src_an, d['file'], d['kind'], name)}"],
                        "detail": f"{d['kind']} named by no default-build code, by {refs_f} site(s) of the features build"})
    return out


def gating(sha: str, src_an: dict, hatches: list[dict], wasm: bool, inventory_of) -> dict:
    t0 = time.time()
    default = qexpand.expand(sha, "default")
    feats = qexpand.expand(sha, "features")
    findings = build_findings("default", default, hatches, src_an) + dead_findings(default, feats, src_an)
    if wasm:
        w = qexpand.expand(sha, "wasm")
        hw, _ = inventory_of(w, src_an)
        # the wasm build's hatches, classed as in the host inventory (feature-only there means the
        # name is gated on the host; in the wasm build a read is a shipped read)
        cls = {h["name"]: h["class"] for h in hatches}
        wh = [{"name": n, "read_in_prod": h["reads"] > 0, "class": cls.get(n, "?"), "sites": h["sites"]} for n, h in hw.items()]
        findings += build_findings("wasm", w, wh, src_an)
    # one finding per (rule, subject); the builds that have it
    merged: dict[tuple[str, str], dict] = {}
    for f in findings:
        k = (f["rule"], f["subject"])
        if k in merged:
            if f["build"] not in merged[k]["builds"]:
                merged[k]["builds"].append(f["build"])
            continue
        merged[k] = {**{x: f[x] for x in ("rule", "subject", "where", "detail")}, "builds": [f["build"]]}
    allow = load_allow()
    res = {"sha": sha, "findings": [], "allowed": [], "stale": [], "toolchain": default["toolchain"],
           "builds": ["default"] + (["wasm"] if wasm else [])}
    for k, f in sorted(merged.items()):
        if k in allow:
            res["allowed"].append({**f, "reason": allow[k]})
        else:
            res["findings"].append(f)
    res["stale"] = [{"rule": r, "subject": s, "reason": why} for (r, s), why in sorted(allow.items()) if (r, s) not in merged]
    res["seconds"] = round(time.time() - t0, 1)
    return res


def md(res: dict, rev: str) -> list[str]:
    o = [f"## Gating check {rev} ({res['sha'][:9]}): builds {', '.join(res['builds'])} on {res['toolchain']}", ""]
    o.append(f"- {len(res['findings'])} finding(s) not allowed, {len(res['allowed'])} allowed "
             f"(scripts/perf-rearch/gating-allow.txt), {len(res['stale'])} stale allow entr{'y' if len(res['stale']) == 1 else 'ies'}")
    if res["findings"]:
        o += ["", "| rule | subject | where | why | builds |", "|---|---|---|---|---|"]
        for f in res["findings"]:
            o.append(f"| {f['rule']} | `{f['subject']}` | {', '.join(f['where'])} | {f['detail']} | {', '.join(f['builds'])} |")
    if res["allowed"]:
        o += ["", "Allowed:", ""]
        for f in res["allowed"]:
            o.append(f"- {f['rule']} `{f['subject']}`: {f['reason']}")
    if res["stale"]:
        o += ["", "Stale allow entries (match nothing; remove them):", ""]
        for f in res["stale"]:
            o.append(f"- {f['rule']} `{f['subject']}`")
    o += ["", f"_quality.sh gating: {res['seconds']} s; {'GATING OK' if not res['findings'] else 'GATING FAIL'}_"]
    return o


if __name__ == "__main__":
    print(json.dumps(load_allow(), indent=1))
