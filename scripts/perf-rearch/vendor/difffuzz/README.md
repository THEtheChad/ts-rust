# difffuzz

A differential fuzzer: it mutates TypeScript test cases, runs Go `tsc` at the pin (the oracle) and
`tsc-rs` on each one, and reports every difference in exit code, stdout, stderr or written files.
A `tsc-rs` crash (exit 70, a signal, a stack overflow) is always a finding. Python 3 standard
library only.

```sh
# 45 minutes, 6 workers, then reduce each new signature (12 minutes at most each)
scripts/difffuzz/difffuzz.py fuzz --out /tmp/dfz --workers 6 --minutes 45 --reduce-minutes 12 --seed 1 \
  --rs target/release/tsgo
scripts/difffuzz/difffuzz.py replay --seed 1 --case 4711 --out /tmp/dfz-4711   # make case 4711 again
scripts/difffuzz/difffuzz.py check /tmp/dfz/findings/<sig>/case.json           # run a case, print the diff
scripts/difffuzz/difffuzz.py reduce /tmp/dfz/raw/<sig>/<id>/case.json --out /tmp/small
```

`--weights jsdoc_host=12,deep=1` changes the weights of the text mutators (`TEXT_MUTATORS` in
`mutators.py`); `replay` needs the same weights. Defaults: `--go ~/.local/bin/tsgo-oracle`, `--rs target/release/tsgo`, `--upstream` the `tsc/`
directory of `~/.explore/repos/microsoft__TypeScript@3e9236361fa9`, `--timeout 60` seconds.

## How it works

- **Seeds** (`seeds.py`): every `compiler` and `conformance` case under
  `tsc/testdata/tests/cases` of the pinned checkout, split the way Go's testrunner splits them
  (`// @filename:` files, `// @option:` globals). Options become `tsconfig.json` options (names and
  types from `internal/tsoptions/declarations_generated.go`); the first value of a variation is
  used. Cases with `@link`, `@symlink`, `@currentDirectory`, their own `tsconfig.json` or `..`
  paths are skipped; output options (outDir, outFile, ...) and harness-only options are dropped.
- **Mutators** (`mutators.py`): moved and duplicated comments and `@ts-ignore` /
  `@ts-expect-error` / `@ts-nocheck` directives, JSDoc blocks with nested type expressions and
  comments inside `{}`, JSDoc tags on JS hosts (functions, methods, arrows, classes,
  `module.exports`) with stray TS syntax such as `<>` (Go's reparser makes TS nodes of them in
  `.js` files; the file usually gets renamed to `.js`), deep nesting (8 to 3000 levels, log-uniform) of parens, calls, object and
  array literals, templates, conditional types, binary chains, JSX and more, Unicode identifiers
  and escapes, CRLF/CR/mixed line endings and U+2028, BOMs (UTF-8, UTF-16, stray), truncation at
  a byte offset, token edits, splices from other seeds, and option changes (strict, target, module,
  jsx, declaration, exactOptionalPropertyTypes, checkJs with a file renamed to `.js`, ...). A case
  gets 1 to 4 of them; 3% run unmutated. 5% replace the generated `tsconfig.json` with mutated JSON
  text (comments, trailing commas, BOM, wrong option types, truncation), and 30% add command-line
  flags (`--pretty true`, `--listFiles`, `--explainFiles`, `--traceResolution`, `--showConfig`,
  ...). 40% of the cases without `noEmit` get `noEmit`; the rest emit to `out/` and the written
  files are compared too.
- **Oracle** (`oracle.py`): both compilers run in the same temporary directory (under `--tmp`)
  with `-p tsconfig.json --pretty false` and `GOPORT_*` removed from the environment. A timeout
  on one side gets a second try with twice the time. A difference is confirmed with five more Go
  runs, eight with `--traceResolution` (its order races between Go's resolver threads). Go's
  output changing drops the case (`go-nondet`; a Go timeout there counts as load). Then
  one more `tsc-rs` run follows (`rs-flaky` when it then matches Go, `rs-nondet` when it differs again).
  Go panics and Go-only hangs get `oracle:` signatures and are not reduced.
- **Signature**: `crash:<panic file:line>`, `crash:stack-overflow`, `crash:signal-N`, `hang`,
  `diag:<first differing diagnostic>` (`-TS2322` missing in tsc-rs, `+TS1005` extra in tsc-rs,
  `TS1005>TS1109` other code, `pos~`/`msg~`/`file~` same code with another position, message or
  file name), `stderr:...`, `emit:<ext>:missing|extra|content`, `exit:<go>><rs>`.
- **Reduction** (`reduce.py`): delta debugging with the predicate "same signature": files, then
  options, then each file by lines, by halving repeated runs (deep nesting), by tokens and (under
  300 characters) by characters, until a round removes nothing.

Output in `--out`: `log.txt` (progress), `stats.json`, `slow.txt`, `notes.txt` (dropped
nondeterministic cases), `raw/<sig>/<case>/` (up to `--keep` examples per signature) and
`findings/<sig>/` with `repro/` (the files and `tsconfig.json`), `go.out`, `rs.out`, `diff`,
`info.json` and `case.json`. Case ids are `<seed>:<n>`; case n of a seed is the same on every run
with the same seed corpus, mutator code and `--weights`.
