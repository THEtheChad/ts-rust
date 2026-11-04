import json, re, sys
for f in sys.argv[1:]:
    d = json.load(open(f))
    txt = "\n".join(l for _, l in d["logs"])
    def ms(rx):
        m = re.search(rx, txt)
        if not m: return None
        v, u = float(m.group(1)), m.group(2)
        return v / 1000 if u in ("µs", "us") else v * 1000 if u == "s" else v
    def ts(rx):
        m = re.search(r"\[(\d\d):(\d\d):(\d\d\.\d+)\] " + rx, txt)
        return None if not m else (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) * 1000
    cfg = None
    a, b = ts("Loading config file"), ts("Finished loading config file")
    if a is not None and b is not None: cfg = b - a
    print(f"{f}: init {d['init_ms']:.1f} didOpen {ms(r'textDocument/didOpen. in ([\d.]+)(ms|µs|s)'):.1f} config {cfg if cfg is None else round(cfg,1)} "
          f"program {ms(r'Program update for \S+ completed in ([\d.]+)(ms|µs|s)'):.1f} diag {d['diag_ms']:.1f} ttfd {d['ttfd_ms']:.1f} cpu {d['cpu_ms']}")
