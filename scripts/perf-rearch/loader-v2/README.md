# loader-v2 measurement tools

Tools behind `docs/perf-rearch/design/loader-v2.md` §0. Data lives in `~/.cache/perf-rearch/scratch/loader-v2/`.

- `fsbench.c`: replays the OS-level calls of one load and times them per kind, on 1 or N threads.
  - Build: `gcc -O2 -o fsbench fsbench.c -lpthread`.
  - Input is a TSV of `kind<TAB>path`. Kinds are `file_exists`, `directory_exists`, `realpath`, `read` and `entries`.
  - Make the input from a load dump's snapshot section:
    `zcat DUMP.gz | awk '/^== /{s++} s==2 && ($1=="file_exists"||$1=="directory_exists"||$1=="realpath"||$1=="entries"){print $1"\t"$2} s==2 && $1=="cached_file"{print "read\t"$2}'`
- `tt-wrap.sh` + `tt_sampler.py`: per-thread utime, stime and minor faults of a server, with a timeline of the busiest thread.
  - Run: `TT_BIN=<server> TT_OUT=<file> node ../loader/ttfd-logs.mjs $PWD/tt-wrap.sh -- <project> --file REL`.
- `sm-wrap.sh` + `smaps_sampler.py`: the last `smaps_rollup` (Rss, AnonHugePages) of a server before it exits.
- `classify.py`, `leaves.py`, `callers.py`: loader-thread views of a samply profile (`samply_top.py` format).
  - samply spreads kernel time over user frames, so OS cost is undercounted. Use `tt-wrap.sh` for the user/system split.
