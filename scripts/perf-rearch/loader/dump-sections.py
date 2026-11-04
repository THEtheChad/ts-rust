#!/usr/bin/env python3
"""Record-level comparison of S1a runs: load dumps (GOPORT_LOAD_DUMP) section by section, or
message streams (load-dump-session.mjs <out>.stream) as one unit.

usage: python3 -I dump-sections.py dumps|stream --off OFF... --on ON... [--serial SERIAL]
       python3 -I dump-sections.py selftest   (Astra's repro and the other verdicts)

A record is one line: a dump line inside a `== kind ==` section, or one message of a stream.
A stream is two units, compared apart: the server's messages (s2c) in their order, and the
client's (c2s) in theirs. Their interleaving is not compared: the client sends its next
message when it gets a response, while the server may still be writing the log line of that
response, so the interleaving is a race in the client, not the server's behavior. Server
messages after the client's `exit` notification are EXCLUDED and listed (the server stops
then). Per unit (a dump section by its index, or a stream direction) and per S1a-on run:
  FAIL  missing   a record that every S1a-off run has (at least that count) and the on run lacks
  FAIL  extra     a record that the on run has more times than any S1a-off run
  FAIL  order     every off run lists the unit's records in one order and the on run, with
                  the same records, in another (first differing position)
  INCONCLUSIVE    a record whose count differs between the S1a-off runs: it varies without
                  S1a, so it proves nothing either way. Each one is listed; a unit whose off
                  runs differ in order only is listed as order-inconclusive. More off runs
                  make fewer records inconclusive.
  EXCLUDED        a record of a documented kind (EXCLUDED_KINDS below), each listed with the
                  reason.
SERIAL (no resolve ahead) is compared the same way against the S1a-off runs and reported
apart (it never fails the comparison: keyed second loads differ from it before S1a).
Ends with RECORDS OK when no FAIL, else RECORDS FAIL. Each INCONCLUSIVE/EXCLUDED record is
printed (cut to 300 characters) so a reader can check every exemption.
"""
import sys
from collections import Counter

# Record kinds (first word of a dump line) that differ between S1a on and off by design,
# with the reason. Their behavior is compared through other records.
EXCLUDED_KINDS = {
    "info_cache_entry": "the raw InfoCache entry store: S1a adopts a taken answer's package.json "
                        "lookups as pending reads (InfoCache::add_pending), which range() and "
                        "contains_key() do not see; enumeration is compared through the `package_json` "
                        "records (package_json_cache_entries) and lookups through `info_cache_get`",
    "info_cache_pending": "the raw InfoCache pending store (see info_cache_entry)",
    "after_exit": "a server message after the client's exit notification (the server stops then)",
}


def units(path, mode):
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    if mode == "stream":
        s2c, c2s, after_exit = [], [], []
        exited = False
        for line in lines:
            if line.startswith("c2s "):
                c2s.append(line)
                if '"method":"exit"' in line:
                    exited = True
            elif exited:
                after_exit.append("after_exit " + line)
            else:
                s2c.append(line)
        return [("stream s2c", s2c + after_exit), ("stream c2s", c2s)]
    out, cur, kind = [], [], None
    for line in lines:
        if line.startswith("== ") and line.endswith(" =="):
            if kind is not None:
                out.append((kind, cur))
            kind, cur = line, []
        elif kind is not None:
            cur.append(line)
    if kind is not None:
        out.append((kind, cur))
    return out


def kind_of(record):
    return record.split(" ", 1)[0].strip()


def cut(text):
    return text if len(text) <= 300 else text[:300] + "…"


def compare(name, offs, other, label, report, first):
    """Returns the FAIL count of `other` against the off runs of one unit. `first`: this is
    the first run compared for the unit (it lists the inconclusive and excluded records)."""
    fails = 0
    counts = [Counter(off) for off in offs]
    x = Counter(other)
    stable, union = counts[0], counts[0]
    for c in counts[1:]:
        stable, union = stable & c, union | c
    if first:
        for record in sorted((union - stable).keys()):
            if kind_of(record) not in EXCLUDED_KINDS:
                report(f"INCONCLUSIVE {name}: varies between the S1a-off runs (counts "
                       f"{[c[record] for c in counts]}): {cut(record)}")
    for record, count in sorted((stable - x).items()):
        if kind_of(record) in EXCLUDED_KINDS:
            if first:
                report(f"EXCLUDED {name}: {kind_of(record)} ({EXCLUDED_KINDS[kind_of(record)]}): "
                       f"only off: {cut(record)}")
            continue
        report(f"FAIL {name} {label} missing x{count}: {cut(record)}")
        fails += 1
    for record, count in sorted((x - union).items()):
        if kind_of(record) in EXCLUDED_KINDS:
            if first:
                report(f"EXCLUDED {name}: {kind_of(record)} ({EXCLUDED_KINDS[kind_of(record)]}): "
                       f"only {label}: {cut(record)}")
            continue
        report(f"FAIL {name} {label} extra x{count}: {cut(record)}")
        fails += 1
    keep = lambda records: [r for r in records if kind_of(r) not in EXCLUDED_KINDS]
    kept_offs = [keep(off) for off in offs]
    kx = keep(other)
    if all(k == kept_offs[0] for k in kept_offs):
        ka = kept_offs[0]
        if Counter(kx) == Counter(ka) and kx != ka:
            i = next(i for i, (p, q) in enumerate(zip(ka, kx)) if p != q)
            report(f"FAIL {name} {label} order at record {i}: off {cut(ka[i])} | {label} {cut(kx[i])}")
            fails += 1
    elif first and all(Counter(k) == Counter(kept_offs[0]) for k in kept_offs):
        report(f"INCONCLUSIVE {name}: the S1a-off runs differ in order; the order of this unit "
               f"is not compared")
    return fails


def selftest():
    """The verdicts on small dumps: noise in the off runs plus a wrong resolution in both on
    runs fails (Astra's repro of the old section-level rule); off-run noise alone is
    inconclusive and passes; a reordered on run fails; an excluded kind is listed."""
    import os, subprocess, tempfile
    cases = {
        "repro": (["module a=a.ts", "noise 1"], ["module a=a.ts", "noise 2"],
                  ["module a=WRONG.ts", "noise 1"], ["module a=WRONG.ts", "noise 2"], "RECORDS FAIL"),
        "noise": (["module a=a.ts", "noise 1"], ["module a=a.ts", "noise 2"],
                  ["module a=a.ts", "noise 2"], ["module a=a.ts", "noise 1"], "RECORDS OK"),
        "order": (["load a", "load b"], ["load a", "load b"], ["load b", "load a"], ["load a", "load b"],
                  "RECORDS FAIL"),
        "excluded": (["info_cache_entry k", "get k"], ["info_cache_entry k", "get k"],
                     ["info_cache_pending k", "get k"], ["info_cache_pending k", "get k"], "RECORDS OK"),
        # Streams: a refresh moved across a response fails; the client's send racing a
        # server log line does not; a message after exit is excluded.
        "stream-refresh": (["c2s {req 1}", "s2c {refresh}", "s2c {response 1}"],
                           ["c2s {req 1}", "s2c {refresh}", "s2c {response 1}"],
                           ["c2s {req 1}", "s2c {response 1}", "s2c {refresh}"],
                           ["c2s {req 1}", "s2c {refresh}", "s2c {response 1}"], "RECORDS FAIL"),
        "stream-race": (["c2s {req 1}", "s2c {response 1}", "s2c {log}", "c2s {req 2}"],
                        ["c2s {req 1}", "s2c {response 1}", "s2c {log}", "c2s {req 2}"],
                        ["c2s {req 1}", "s2c {response 1}", "c2s {req 2}", "s2c {log}"],
                        ["c2s {req 1}", "s2c {response 1}", "s2c {log}", "c2s {req 2}"], "RECORDS OK"),
        "stream-exit": (['c2s {"method":"exit"}'], ['c2s {"method":"exit"}'],
                        ['c2s {"method":"exit"}', "s2c {late log}"], ['c2s {"method":"exit"}'], "RECORDS OK"),
    }
    with tempfile.TemporaryDirectory() as d:
        for name, (*runs, want) in cases.items():
            paths = []
            for i, records in enumerate(runs):
                path = os.path.join(d, f"{name}{i}")
                head = "" if name.startswith("stream") else "== load ==\n"
                open(path, "w").write(head + "\n".join(records) + "\n")
                paths.append(path)
            mode = "stream" if name.startswith("stream") else "dumps"
            out = subprocess.run([sys.executable, "-I", __file__, mode, "--off", *paths[:2],
                                  "--on", *paths[2:]],
                                 capture_output=True, text=True).stdout
            got = out.strip().splitlines()[-1].split(" (")[0]
            print(f"{'ok  ' if got == want else 'BAD '} {name}: {got} (want {want})")
            if got != want:
                sys.exit(1)
    print("SELFTEST OK")


def main():
    if sys.argv[1:] == ["selftest"]:
        return selftest()
    args = sys.argv[1:]
    mode = args.pop(0) if args else ""
    groups, key = {"--off": [], "--on": [], "--serial": []}, None
    for arg in args:
        if arg in groups:
            key = arg
        elif key:
            groups[key].append(arg)
    offs, ons, serial = groups["--off"], groups["--on"], groups["--serial"]
    if mode not in ("dumps", "stream") or len(offs) < 2 or not ons or len(serial) > 1:
        sys.exit(__doc__)
    off_runs = [units(f, mode) for f in offs]
    on_runs = [units(f, mode) for f in ons]
    serial_runs = [units(f, mode) for f in serial]
    count = max(len(r) for r in off_runs + on_runs)
    fails = 0
    out = []
    for i in range(count):
        def at(run):
            return run[i] if i < len(run) else ("<absent>", [])
        rows = [at(r) for r in off_runs + on_runs]
        kinds = {k for k, _ in rows}
        name = f"#{i} {rows[0][0]}" if mode == "dumps" else rows[0][0]
        if len(kinds) > 1:
            out.append(f"FAIL {name}: the runs have different units here: {sorted(kinds)}")
            fails += 1
            continue
        off_recs = [records for _, records in rows[:len(offs)]]
        lines = []
        f = 0
        for j, run in enumerate(on_runs):
            f += compare(name, off_recs, at(run)[1], f"on{j + 1}", lines.append, j == 0)
        fails += f
        for run in serial_runs:
            serial_lines = []
            compare(name, off_recs, at(run)[1], "serial", serial_lines.append, False)
            lines += [f"SERIAL {line}" for line in serial_lines if line.startswith("FAIL")]
        out.append(f"{'ok  ' if not f else 'FAIL'} {name} ({len(off_recs[0])} records)")
        out += [f"  {line}" for line in lines]
    print("\n".join(out))
    print("RECORDS OK" if fails == 0 else f"RECORDS FAIL ({fails})")


main()
