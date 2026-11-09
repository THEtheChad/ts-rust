# Timing window 2 (W2 lane; head = perf/first-principles 64014edb4, main = 91f3d021c; finished 2026-11-08 21:02 CST)

Driver: W2's `scripts/perf-rearch/window2.sh` (branch `t3/window-two-perf-measurements`); raw data `~/.cache/perf-rearch/window2/run/`. Load-gated, runs within 300 s of a VM boot discarded (P2). "(load)" marks cells with a run that ended above the limit.

**Head slower than Go: one cell**, `build-warm query` (no-op `-b`): 8 ms vs 7 ms (1.17×); lane P3. Everything else is at or under Go: CLI 0.37–0.67×, `-b` cold/warm 0.22–0.63×, incr-edit 0.48–0.74×, open12 0.88×, ttfd 0.93×, concurrent typing 0.28–0.69×, probes 0.13–0.26×.

| scenario | project | side | n | wall median (min..max) | CPU | max RSS | instructions | vs go | vs main | output |
|---|---|---|---|---|---|---|---|---|---|---|
| build-cold | effect | go | 3 | 1.50 s (1.48 s..1.50 s) | 10.47 s | 1254 MB | 55.19G |  |  |  |
| build-cold | effect | main | 3 | 832 ms (832 ms..834 ms) | 3.81 s | 997 MB | 29.22G | 0.56× |  | same |
| build-cold | effect | head | 3 | 838 ms (835 ms..860 ms) | 3.84 s | 1003 MB | 29.20G | 0.56× | 1.01× | same |
| build-cold | hono | go | 3 | 332 ms (327 ms..337 ms) | 1.68 s | 279 MB | 8.75G |  |  |  |
| build-cold | hono | main | 3 | 176 ms (176 ms..177 ms) | 610 ms | 228 MB | 3.81G | 0.53× |  | same |
| build-cold | hono | head | 3 | 180 ms (179 ms..187 ms) | 614 ms | 229 MB | 3.80G | 0.54× | 1.02× | same |
| build-cold | query | go | 4 | 97 ms (97 ms..101 ms) | 441 ms | 90 MB | 1.84G |  |  |  |
| build-cold | query | main | 6 | 50 ms (48 ms..52 ms) | 147 ms | 85 MB | 0.84G | 0.52× |  | same |
| build-cold | query | head | 6 | 50 ms (47 ms..52 ms) | 147 ms | 85 MB | 0.84G | 0.52× | 1.00× | same |
| build-cold | t3-server | go | 1 | 10.50 s (10.50 s..10.50 s) | 43.74 s | 4173 MB | 319.69G |  |  |  |
| build-cold | t3-server | main | 1 | 4.05 s (4.05 s..4.05 s) | 13.58 s | 3507 MB | 121.32G | 0.39× |  | same |
| build-cold | t3-server | head | 2 | 4.05 s (4.02 s..4.08 s) | 13.50 s | 3508 MB | 121.21G | 0.39× | 1.00× | same |
| build-cold | t3-web | go | 3 | 4.13 s (4.09 s..4.15 s) | 24.91 s | 2965 MB | 154.95G |  |  |  |
| build-cold | t3-web | main | 3 | 2.24 s (2.18 s..2.45 s) | 8.73 s | 2191 MB | 76.87G | 0.54× |  | same |
| build-cold | t3-web | head | 3 | 2.19 s (2.17 s..2.20 s) | 8.75 s | 2191 MB | 76.81G | 0.53× | 0.98× | same |
| build-cold | ts-pattern | go | 3 | 52 ms (52 ms..57 ms) | 181 ms | 50 MB | 0.72G |  |  |  |
| build-cold | ts-pattern | main | 2 | 30 ms (30 ms..30 ms) | 64 ms | 52 MB | 0.29G | 0.58× |  | same |
| build-cold | ts-pattern | head | 3 | 32 ms (32 ms..32 ms) | 66 ms | 53 MB | 0.29G | 0.61× | 1.06× | same |
| build-cold | umami | go | 3 | 1.37 s (1.37 s..1.39 s) | 7.00 s | 896 MB | 39.20G |  |  |  |
| build-cold | umami | main | 3 | 811 ms (804 ms..821 ms) | 2.65 s | 775 MB | 20.17G | 0.59× |  | same |
| build-cold | umami | head | 3 | 810 ms (808 ms..817 ms) | 2.66 s | 778 MB | 20.24G | 0.59× | 1.00× | same |
| build-cold | zod | go | 3 | 1.03 s (1.03 s..1.04 s) | 6.08 s | 863 MB | 35.80G |  |  |  |
| build-cold | zod | main | 3 | 477 ms (475 ms..479 ms) | 1.79 s | 677 MB | 14.48G | 0.46× |  | same |
| build-cold | zod | head | 3 | 483 ms (475 ms..486 ms) | 1.80 s | 678 MB | 14.48G | 0.47× | 1.01× | same |
| build-warm | effect | go | 2 | 174 ms (174 ms..174 ms) | 1.61 s | 265 MB | 4.79G |  |  |  |
| build-warm | effect | main | 3 | 75 ms (74 ms..78 ms) | 534 ms | 253 MB | 2.62G | 0.43× |  | same |
| build-warm | effect | head | 3 | 73 ms (73 ms..74 ms) | 516 ms | 254 MB | 2.62G | 0.42× | 0.98× | same |
| build-warm | hono | go | 3 | 76 ms (76 ms..91 ms) | 474 ms | 86 MB | 1.67G |  |  |  |
| build-warm | hono | main | 3 | 37 ms (35 ms..37 ms) | 157 ms | 89 MB | 0.68G | 0.48× |  | same |
| build-warm | hono | head | 3 | 36 ms (34 ms..41 ms) | 159 ms | 91 MB | 0.68G | 0.47× | 0.97× | same |
| build-warm | query | go | 11 | 7 ms (7 ms..8 ms) | 9 ms | 17 MB | 0.02G |  |  |  |
| build-warm | query | main | 12 | 8 ms (7 ms..9 ms) | 8 ms | 15 MB | 0.01G | **1.14× slower** |  | same |
| build-warm | query | head | 12 | 8 ms (8 ms..9 ms) | 8 ms | 15 MB | 0.01G | **1.17× slower** | 1.03× | same |
| build-warm | t3-server | go | 3 | 10.73 s (10.73 s..11.16 s) | 44.47 s | 4175 MB | 317.23G |  |  |  |
| build-warm | t3-server | main | 3 | 4.16 s (4.08 s..4.18 s) | 13.62 s | 3509 MB | 121.33G | 0.39× |  | same |
| build-warm | t3-server | head | 3 | 4.05 s (4.03 s..4.15 s) | 13.57 s | 3510 MB | 121.23G | 0.38× | 0.97× | same |
| build-warm | t3-web | go | 3 | 278 ms (272 ms..279 ms) | 577 ms | 47 MB | 5.22G |  |  |  |
| build-warm | t3-web | main | 3 | 62 ms (61 ms..63 ms) | 59 ms | 27 MB | 0.50G | 0.22× |  | same |
| build-warm | t3-web | head | 3 | 61 ms (61 ms..63 ms) | 59 ms | 28 MB | 0.50G | 0.22× | 0.99× | same |
| build-warm | ts-pattern | go | 3 | 50 ms (48 ms..52 ms) | 152 ms | 50 MB | 0.75G |  |  |  |
| build-warm | ts-pattern | main | 3 | 31 ms (30 ms..32 ms) | 61 ms | 52 MB | 0.29G | 0.62× |  | same |
| build-warm | ts-pattern | head | 3 | 32 ms (30 ms..33 ms) | 59 ms | 53 MB | 0.29G | 0.63× | 1.02× | same |
| build-warm | umami | go | 3 | 1.38 s (1.36 s..1.42 s) | 6.83 s | 876 MB | 38.34G |  |  |  |
| build-warm | umami | main | 3 | 807 ms (798 ms..817 ms) | 2.61 s | 776 MB | 20.15G | 0.59× |  | same |
| build-warm | umami | head | 3 | 814 ms (811 ms..818 ms) | 2.63 s | 778 MB | 20.24G | 0.59× | 1.01× | same |
| build-warm | zod | go | 3 | 1.03 s (1.03 s..1.03 s) | 5.48 s | 885 MB | 33.34G |  |  |  |
| build-warm | zod | main | 3 | 485 ms (482 ms..496 ms) | 1.81 s | 677 MB | 14.48G | 0.47× |  | same |
| build-warm | zod | head | 3 | 485 ms (484 ms..499 ms) | 1.81 s | 678 MB | 14.48G | 0.47× | 1.00× | same |
| concurrent-120 | umami | go | 5 | 316 ms (313 ms..338 ms) (load) | 0 ms | - | - |  |  |  |
| concurrent-120 | umami | main | 5 | 1.23 s (944 ms..1.51 s) | 0 ms | - | - | **3.89× slower** |  |  |
| concurrent-120 | umami | head | 5 | 90 ms (85 ms..93 ms) | 0 ms | - | - | 0.28× | 0.07× |  |
| concurrent-250 | umami | go | 4 | 144 ms (130 ms..153 ms) | 0 ms | - | - |  |  |  |
| concurrent-250 | umami | main | 5 | 198 ms (197 ms..206 ms) | 0 ms | - | - | **1.37× slower** |  |  |
| concurrent-250 | umami | head | 5 | 100 ms (95 ms..111 ms) | 0 ms | - | - | 0.69× | 0.51× |  |
| concurrent-500 | umami | go | 3 | 154 ms (146 ms..166 ms) | 0 ms | - | - |  |  |  |
| concurrent-500 | umami | main | 3 | 209 ms (204 ms..213 ms) | 0 ms | - | - | **1.36× slower** |  |  |
| concurrent-500 | umami | head | 3 | 100 ms (99 ms..110 ms) | 0 ms | - | - | 0.65× | 0.48× |  |
| emit | effect | go | 3 | 1.48 s (1.44 s..1.48 s) | 10.14 s | 1367 MB | 54.88G |  |  |  |
| emit | effect | main | 3 | 810 ms (794 ms..823 ms) | 3.87 s | 961 MB | 28.81G | 0.55× |  | same |
| emit | effect | head | 3 | 782 ms (774 ms..796 ms) | 3.70 s | 974 MB | 28.82G | 0.53× | 0.97× | same |
| emit | hono | go | 3 | 337 ms (319 ms..340 ms) | 1.71 s | 269 MB | 8.74G |  |  |  |
| emit | hono | main | 3 | 168 ms (167 ms..172 ms) | 587 ms | 225 MB | 3.71G | 0.50× |  | same |
| emit | hono | head | 3 | 164 ms (163 ms..168 ms) | 614 ms | 225 MB | 3.71G | 0.49× | 0.98× | same |
| emit | query | go | 3 | 101 ms (99 ms..105 ms) | 439 ms | 88 MB | 1.79G |  |  |  |
| emit | query | main | 3 | 49 ms (49 ms..50 ms) | 140 ms | 88 MB | 0.82G | 0.48× |  | same |
| emit | query | head | 3 | 48 ms (46 ms..49 ms) | 143 ms | 88 MB | 0.82G | 0.48× | 0.99× | same |
| emit | t3-server | go | 3 | 11.12 s (11.10 s..11.77 s) | 46.72 s | 4435 MB | 328.40G |  |  |  |
| emit | t3-server | main | 3 | 4.26 s (3.99 s..4.40 s) | 14.81 s | 3582 MB | 129.59G | 0.38× |  | same |
| emit | t3-server | head | 3 | 4.07 s (3.96 s..4.25 s) | 14.48 s | 3607 MB | 129.51G | 0.37× | 0.96× | same |
| emit | t3-web | go | 3 | 4.38 s (4.20 s..4.41 s) | 26.75 s | 3091 MB | 162.56G |  |  |  |
| emit | t3-web | main | 3 | 2.25 s (2.24 s..2.32 s) | 9.71 s | 2193 MB | 86.25G | 0.51× |  | same |
| emit | t3-web | head | 3 | 2.22 s (2.21 s..2.25 s) | 9.74 s | 2194 MB | 86.25G | 0.51× | 0.99× | same |
| emit | ts-pattern | go | 3 | 111 ms (111 ms..112 ms) | 296 ms | 75 MB | 1.61G |  |  |  |
| emit | ts-pattern | main | 3 | 63 ms (63 ms..65 ms) | 106 ms | 74 MB | 0.70G | 0.57× |  | same |
| emit | ts-pattern | head | 3 | 63 ms (62 ms..67 ms) | 113 ms | 75 MB | 0.70G | 0.57× | 1.00× | same |
| emit | umami | go | 3 | 1.46 s (1.42 s..1.47 s) | 7.52 s | 944 MB | 41.36G |  |  |  |
| emit | umami | main | 3 | 883 ms (878 ms..1.09 s) | 3.10 s | 832 MB | 22.26G | 0.61× |  | same |
| emit | umami | head | 3 | 856 ms (836 ms..876 ms) | 3.06 s | 829 MB | 22.31G | 0.59× | 0.97× | same |
| emit | vscode | go | 3 | 11.64 s (11.30 s..12.14 s) | 91.08 s | 7849 MB | 391.46G |  |  |  |
| emit | vscode | main | 3 | 7.43 s (7.41 s..7.99 s) | 36.39 s | 6401 MB | 289.36G | 0.64× |  | same |
| emit | vscode | head | 3 | 7.52 s (7.46 s..8.08 s) | 37.10 s | 6410 MB | 289.24G | 0.65× | 1.01× | same |
| emit | zod | go | 3 | 1.06 s (1.06 s..1.07 s) | 5.65 s | 964 MB | 34.48G |  |  |  |
| emit | zod | main | 3 | 508 ms (504 ms..628 ms) | 1.97 s | 699 MB | 16.16G | 0.48× |  | same |
| emit | zod | head | 3 | 502 ms (495 ms..530 ms) | 1.96 s | 700 MB | 16.16G | 0.47× | 0.99× | same |
| emit-cjs | hono | go | 3 | 284 ms (282 ms..290 ms) | 1.45 s | 240 MB | 7.97G |  |  |  |
| emit-cjs | hono | main | 3 | 146 ms (145 ms..148 ms) | 524 ms | 211 MB | 3.57G | 0.51× |  | same |
| emit-cjs | hono | head | 3 | 142 ms (138 ms..143 ms) | 539 ms | 212 MB | 3.57G | 0.50× | 0.97× | same |
| emit-cjs | query | go | 3 | 93 ms (88 ms..100 ms) | 428 ms | 88 MB | 1.90G |  |  |  |
| emit-cjs | query | main | 3 | 49 ms (49 ms..51 ms) | 154 ms | 92 MB | 0.95G | 0.53× |  | same |
| emit-cjs | query | head | 3 | 49 ms (47 ms..50 ms) | 162 ms | 91 MB | 0.95G | 0.53× | 0.99× | same |
| emit-cjs | t3-web | go | 3 | 4.06 s (4.06 s..4.08 s) | 25.57 s | 2908 MB | 149.25G |  |  |  |
| emit-cjs | t3-web | main | 3 | 2.32 s (2.30 s..2.33 s) | 9.69 s | 2245 MB | 86.22G | 0.57× |  | same |
| emit-cjs | t3-web | head | 3 | 2.31 s (2.31 s..2.32 s) | 9.66 s | 2250 MB | 86.14G | 0.57× | 1.00× | same |
| emit-cjs | ts-pattern | go | 3 | 102 ms (101 ms..104 ms) | 244 ms | 67 MB | 1.32G |  |  |  |
| emit-cjs | ts-pattern | main | 3 | 61 ms (61 ms..63 ms) | 97 ms | 69 MB | 0.62G | 0.60× |  | same |
| emit-cjs | ts-pattern | head | 3 | 61 ms (60 ms..61 ms) | 102 ms | 70 MB | 0.62G | 0.60× | 0.99× | same |
| emit-cjs | umami | go | 3 | 1.54 s (1.53 s..1.59 s) | 9.03 s | 1087 MB | 48.36G |  |  |  |
| emit-cjs | umami | main | 3 | 879 ms (876 ms..904 ms) | 3.30 s | 924 MB | 24.94G | 0.57× |  | same |
| emit-cjs | umami | head | 3 | 875 ms (856 ms..875 ms) | 3.43 s | 899 MB | 24.99G | 0.57× | 0.99× | same |
| emit-js | effect | go | 3 | 1.23 s (1.20 s..1.25 s) | 7.55 s | 950 MB | 38.58G |  |  |  |
| emit-js | effect | main | 3 | 694 ms (683 ms..694 ms) | 2.89 s | 799 MB | 21.67G | 0.57× |  | same |
| emit-js | effect | head | 3 | 693 ms (659 ms..723 ms) | 2.94 s | 805 MB | 21.67G | 0.56× | 1.00× | same |
| emit-js | hono | go | 3 | 315 ms (312 ms..320 ms) | 1.52 s | 244 MB | 8.28G |  |  |  |
| emit-js | hono | main | 3 | 155 ms (155 ms..157 ms) | 507 ms | 216 MB | 3.42G | 0.49× |  | same |
| emit-js | hono | head | 3 | 153 ms (140 ms..155 ms) | 524 ms | 218 MB | 3.42G | 0.48× | 0.99× | same |
| emit-js | query | go | 3 | 96 ms (95 ms..105 ms) | 448 ms | 89 MB | 1.93G |  |  |  |
| emit-js | query | main | 3 | 49 ms (47 ms..50 ms) | 143 ms | 90 MB | 0.89G | 0.51× |  | same |
| emit-js | query | head | 3 | 47 ms (47 ms..48 ms) | 152 ms | 91 MB | 0.89G | 0.49× | 0.97× | same |
| emit-js | t3-server | go | 2 | 10.67 s (10.63 s..10.72 s) | 43.93 s | 4555 MB | 321.69G |  |  |  |
| emit-js | t3-server | main | 3 | 4.15 s (4.11 s..4.23 s) | 14.52 s | 3581 MB | 129.59G | 0.39× |  | same |
| emit-js | t3-server | head | 3 | 3.99 s (3.99 s..4.08 s) | 14.56 s | 3605 MB | 129.47G | 0.37× | 0.96× | same |
| emit-js | t3-web | go | 3 | 3.89 s (3.85 s..3.99 s) | 23.57 s | 2695 MB | 147.48G |  |  |  |
| emit-js | t3-web | main | 3 | 2.11 s (2.09 s..2.12 s) | 8.91 s | 2091 MB | 79.89G | 0.54× |  | same |
| emit-js | t3-web | head | 3 | 2.08 s (2.07 s..2.10 s) | 8.99 s | 2094 MB | 79.82G | 0.53× | 0.98× | same |
| emit-js | ts-pattern | go | 3 | 109 ms (108 ms..111 ms) | 271 ms | 70 MB | 1.46G |  |  |  |
| emit-js | ts-pattern | main | 3 | 61 ms (61 ms..63 ms) | 96 ms | 73 MB | 0.66G | 0.56× |  | same |
| emit-js | ts-pattern | head | 3 | 61 ms (60 ms..62 ms) | 105 ms | 74 MB | 0.66G | 0.56× | 1.00× | same |
| emit-js | umami | go | 3 | 1.44 s (1.42 s..1.45 s) | 7.62 s | 910 MB | 40.35G |  |  |  |
| emit-js | umami | main | 3 | 850 ms (839 ms..862 ms) | 2.99 s | 827 MB | 21.72G | 0.59× |  | same |
| emit-js | umami | head | 3 | 812 ms (803 ms..822 ms) | 2.98 s | 817 MB | 21.76G | 0.56× | 0.95× | same |
| emit-js | vscode | go | 3 | 11.16 s (11.10 s..11.18 s) | 88.71 s | 8016 MB | 387.35G |  |  |  |
| emit-js | vscode | main | 3 | 7.51 s (7.35 s..7.67 s) | 36.76 s | 6402 MB | 289.36G | 0.67× |  | same |
| emit-js | vscode | head | 3 | 7.47 s (7.45 s..7.66 s) | 36.60 s | 6411 MB | 289.25G | 0.67× | 0.99× | same |
| emit-js | zod | go | 3 | 1.06 s (1.05 s..1.08 s) | 5.66 s | 972 MB | 34.31G |  |  |  |
| emit-js | zod | main | 3 | 503 ms (500 ms..505 ms) | 1.97 s | 699 MB | 16.16G | 0.47× |  | same |
| emit-js | zod | head | 3 | 503 ms (500 ms..514 ms) | 1.96 s | 702 MB | 16.16G | 0.47× | 1.00× | same |
| icount | effect | go | 2 | 5.60 s (4.99 s..6.21 s) | 4.86 s | 731 MB | 26.56G |  |  |  |
| icount | effect | main | 2 | 2.84 s (2.55 s..3.12 s) | 2.73 s | 599 MB | 14.73G | 0.55× |  | same |
| icount | effect | head | 2 | 3.08 s (2.79 s..3.38 s) | 2.97 s | 599 MB | 14.72G | 0.55× | 1.00× | same |
| icount | hono | go | 2 | 1.14 s (877 ms..1.41 s) | 867 ms | 172 MB | 5.31G |  |  |  |
| icount | hono | main | 2 | 367 ms (364 ms..371 ms) | 359 ms | 148 MB | 2.15G | 0.40× |  | same |
| icount | hono | head | 2 | 398 ms (390 ms..406 ms) | 387 ms | 149 MB | 2.14G | 0.40× | 1.00× | same |
| icount | query | go | 2 | 350 ms (236 ms..464 ms) | 241 ms | 78 MB | 1.34G |  |  |  |
| icount | query | main | 2 | 129 ms (129 ms..130 ms) | 128 ms | 76 MB | 0.69G | 0.51× |  | same |
| icount | query | head | 2 | 134 ms (131 ms..138 ms) | 135 ms | 75 MB | 0.69G | 0.51× | 1.00× | same |
| icount | t3-server | go | 2 | 26.80 s (19.68 s..33.92 s) | 25.19 s | 2766 MB | 187.79G |  |  |  |
| icount | t3-server | main | 2 | 8.35 s (8.33 s..8.36 s) | 8.02 s | 2177 MB | 71.40G | 0.38× |  | same |
| icount | t3-server | head | 2 | 8.84 s (8.78 s..8.90 s) | 8.52 s | 2177 MB | 71.34G | 0.38× | 1.00× | same |
| icount | t3-web | go | 2 | 15.51 s (10.47 s..20.56 s) | 12.28 s | 2002 MB | 93.61G |  |  |  |
| icount | t3-web | main | 2 | 5.20 s (5.18 s..5.23 s) | 5.03 s | 1358 MB | 47.07G | 0.50× |  | same |
| icount | t3-web | head | 2 | 5.15 s (5.13 s..5.16 s) | 4.97 s | 1359 MB | 47.02G | 0.50× | 1.00× | same |
| icount | ts-pattern | go | 2 | 240 ms (211 ms..269 ms) | 208 ms | 68 MB | 1.40G |  |  |  |
| icount | ts-pattern | main | 2 | 108 ms (97 ms..119 ms) | 105 ms | 66 MB | 0.59G | 0.42× |  | same |
| icount | ts-pattern | head | 2 | 99 ms (94 ms..104 ms) | 99 ms | 66 MB | 0.59G | 0.42× | 1.00× | same |
| icount | umami | go | 2 | 9.16 s (5.14 s..13.17 s) | 5.56 s | 818 MB | 32.96G |  |  |  |
| icount | umami | main | 2 | 2.48 s (2.38 s..2.58 s) | 2.41 s | 654 MB | 16.50G | 0.50× |  | same |
| icount | umami | head | 2 | 2.36 s (2.33 s..2.40 s) | 2.29 s | 655 MB | 16.50G | 0.50× | 1.00× | same |
| icount | vscode | go | 2 | 40.32 s (36.67 s..43.97 s) | 38.11 s | 5664 MB | 251.64G |  |  |  |
| icount | vscode | main | 2 | 34.77 s (22.97 s..46.56 s) | 33.15 s | 4823 MB | 164.16G | 0.65× |  | same |
| icount | vscode | head | 2 | 31.01 s (23.25 s..38.77 s) | 29.75 s | 4824 MB | 163.94G | 0.65× | 1.00× | same |
| icount | zod | go | 2 | 5.15 s (4.99 s..5.31 s) | 4.53 s | 744 MB | 28.41G |  |  |  |
| icount | zod | main | 2 | 2.03 s (2.02 s..2.04 s) | 1.96 s | 513 MB | 11.53G | 0.41× |  | same |
| icount | zod | head | 2 | 2.01 s (1.98 s..2.03 s) | 1.94 s | 513 MB | 11.53G | 0.41× | 1.00× | same |
| incr-edit | hono | go | 5 | 318 ms (296 ms..436 ms) (load) | 1.21 s | 204 MB | 6.42G |  |  |  |
| incr-edit | hono | main | 5 | 109 ms (107 ms..112 ms) | 755 ms | 211 MB | 2.33G | 0.34× |  | DIFFERS |
| incr-edit | hono | head | 5 | 161 ms (158 ms..210 ms) | 780 ms | 211 MB | 2.43G | 0.51× | 1.47× | same |
| incr-edit | query | go | 5 | 97 ms (95 ms..104 ms) | 439 ms | 89 MB | 1.90G |  |  |  |
| incr-edit | query | main | 5 | 47 ms (46 ms..49 ms) | 269 ms | 89 MB | 0.82G | 0.49× |  | same |
| incr-edit | query | head | 5 | 46 ms (44 ms..47 ms) | 267 ms | 89 MB | 0.82G | 0.48× | 0.98× | same |
| incr-edit | synth85 | go | 5 | 48 ms (45 ms..49 ms) | 124 ms | 47 MB | 0.56G |  |  |  |
| incr-edit | synth85 | main | 5 | 25 ms (24 ms..32 ms) | 87 ms | 49 MB | 0.19G | 0.53× |  | DIFFERS |
| incr-edit | synth85 | head | 5 | 36 ms (35 ms..36 ms) | 88 ms | 50 MB | 0.20G | 0.74× | 1.41× | same |
| incr-edit | umami | go | 5 | 1.35 s (1.31 s..1.62 s) (load) | 6.51 s | 902 MB | 35.26G |  |  |  |
| incr-edit | umami | main | 5 | 704 ms (691 ms..808 ms) | 4.94 s | 840 MB | 16.96G | 0.52× |  | same |
| incr-edit | umami | head | 5 | 803 ms (689 ms..879 ms) | 5.69 s | 840 MB | 17.00G | 0.60× | 1.14× | same |
| incr-warm | effect | go | 3 | 170 ms (168 ms..182 ms) | 1.58 s | 261 MB | 5.44G |  |  |  |
| incr-warm | effect | main | 3 | 63 ms (62 ms..66 ms) | 478 ms | 253 MB | 2.58G | 0.37× |  | same |
| incr-warm | effect | head | 3 | 66 ms (63 ms..70 ms) | 490 ms | 252 MB | 2.58G | 0.39× | 1.04× | same |
| incr-warm | hono | go | 3 | 78 ms (71 ms..88 ms) | 428 ms | 85 MB | 1.58G |  |  |  |
| incr-warm | hono | main | 3 | 34 ms (33 ms..35 ms) | 149 ms | 89 MB | 0.66G | 0.44× |  | same |
| incr-warm | hono | head | 3 | 33 ms (32 ms..35 ms) | 146 ms | 90 MB | 0.66G | 0.42× | 0.96× | same |
| incr-warm | query | go | 3 | 57 ms (57 ms..62 ms) | 291 ms | 68 MB | 1.11G |  |  |  |
| incr-warm | query | main | 3 | 26 ms (26 ms..26 ms) | 88 ms | 73 MB | 0.48G | 0.45× |  | same |
| incr-warm | query | head | 3 | 26 ms (25 ms..26 ms) | 89 ms | 72 MB | 0.48G | 0.46× | 1.02× | same |
| incr-warm | t3-server | go | 3 | 590 ms (578 ms..613 ms) | 6.72 s | 875 MB | 21.89G |  |  |  |
| incr-warm | t3-server | main | 3 | 260 ms (256 ms..265 ms) | 2.06 s | 899 MB | 11.36G | 0.44× |  | same |
| incr-warm | t3-server | head | 3 | 262 ms (261 ms..264 ms) | 2.07 s | 899 MB | 11.37G | 0.44× | 1.01× | same |
| incr-warm | t3-web | go | 3 | 587 ms (587 ms..695 ms) | 5.67 s | 743 MB | 19.87G |  |  |  |
| incr-warm | t3-web | main | 3 | 293 ms (288 ms..327 ms) | 1.97 s | 765 MB | 11.19G | 0.50× |  | same |
| incr-warm | t3-web | head | 3 | 293 ms (289 ms..318 ms) | 1.97 s | 767 MB | 11.25G | 0.50× | 1.00× | same |
| incr-warm | ts-pattern | go | 3 | 42 ms (39 ms..42 ms) | 125 ms | 43 MB | 0.54G |  |  |  |
| incr-warm | ts-pattern | main | 3 | 26 ms (25 ms..26 ms) | 47 ms | 50 MB | 0.20G | 0.62× |  | same |
| incr-warm | ts-pattern | head | 3 | 23 ms (23 ms..24 ms) | 42 ms | 49 MB | 0.19G | 0.56× | 0.90× | same |
| incr-warm | umami | go | 3 | 444 ms (434 ms..450 ms) | 3.72 s | 473 MB | 12.93G |  |  |  |
| incr-warm | umami | main | 3 | 222 ms (221 ms..228 ms) | 1.27 s | 504 MB | 6.59G | 0.50× |  | same |
| incr-warm | umami | head | 3 | 224 ms (222 ms..226 ms) | 1.27 s | 504 MB | 6.60G | 0.50× | 1.01× | same |
| incr-warm | vscode | go | 3 | 1.95 s (1.83 s..2.02 s) | 20.90 s | 2825 MB | 68.96G |  |  |  |
| incr-warm | vscode | main | 3 | 817 ms (754 ms..871 ms) | 6.38 s | 2983 MB | 36.17G | 0.42× |  | same |
| incr-warm | vscode | head | 3 | 791 ms (774 ms..834 ms) | 6.50 s | 2983 MB | 36.17G | 0.41× | 0.97× | same |
| incr-warm | zod | go | 3 | 127 ms (124 ms..134 ms) | 926 ms | 150 MB | 3.11G |  |  |  |
| incr-warm | zod | main | 3 | 53 ms (53 ms..54 ms) | 294 ms | 148 MB | 1.45G | 0.42× |  | same |
| incr-warm | zod | head | 3 | 53 ms (53 ms..54 ms) | 301 ms | 149 MB | 1.45G | 0.42× | 1.01× | same |
| noemit | effect | go | 3 | 1.16 s (1.16 s..1.16 s) | 6.43 s | 858 MB | 34.56G |  |  |  |
| noemit | effect | main | 3 | 640 ms (630 ms..642 ms) | 2.43 s | 748 MB | 18.49G | 0.55× |  | same |
| noemit | effect | head | 3 | 637 ms (630 ms..647 ms) | 2.43 s | 749 MB | 18.47G | 0.55× | 1.00× | same |
| noemit | hono | go | 3 | 287 ms (281 ms..292 ms) | 1.25 s | 225 MB | 7.48G |  |  |  |
| noemit | hono | main | 3 | 127 ms (125 ms..127 ms) | 408 ms | 207 MB | 2.94G | 0.44× |  | same |
| noemit | hono | head | 3 | 125 ms (124 ms..130 ms) | 407 ms | 209 MB | 2.94G | 0.44× | 0.99× | same |
| noemit | query | go | 3 | 99 ms (97 ms..109 ms) | 415 ms | 87 MB | 1.80G |  |  |  |
| noemit | query | main | 3 | 44 ms (43 ms..45 ms) | 128 ms | 87 MB | 0.78G | 0.45× |  | same |
| noemit | query | head | 3 | 45 ms (44 ms..45 ms) | 132 ms | 87 MB | 0.78G | 0.45× | 1.01× | same |
| noemit | t3-server | go | 3 | 11.07 s (11.03 s..11.57 s) | 41.45 s | 4314 MB | 304.90G |  |  |  |
| noemit | t3-server | main | 2 | 4.03 s (3.86 s..4.20 s) | 13.39 s | 3492 MB | 119.54G | 0.36× |  | same |
| noemit | t3-server | head | 4 | 4.14 s (4.01 s..4.54 s) | 13.46 s | 3490 MB | 119.44G | 0.37× | 1.03× | same |
| noemit | t3-server | head-thp0 | 1 | 3.98 s (3.98 s..3.98 s) | 13.12 s | 3532 MB | 119.43G | 0.36× |  | same |
| noemit | t3-web | go | 2 | 4.12 s (4.10 s..4.13 s) | 23.50 s | 2898 MB | 147.33G |  |  |  |
| noemit | t3-web | main | 3 | 2.05 s (2.05 s..2.06 s) | 8.41 s | 2090 MB | 74.58G | 0.50× |  | same |
| noemit | t3-web | head | 3 | 2.05 s (2.03 s..2.08 s) | 8.38 s | 2090 MB | 74.56G | 0.50× | 1.00× | same |
| noemit | ts-pattern | go | 3 | 108 ms (105 ms..110 ms) | 249 ms | 72 MB | 1.47G |  |  |  |
| noemit | ts-pattern | main | 3 | 60 ms (57 ms..60 ms) | 92 ms | 70 MB | 0.64G | 0.55× |  | same |
| noemit | ts-pattern | head | 3 | 59 ms (58 ms..59 ms) | 89 ms | 72 MB | 0.64G | 0.54× | 0.98× | same |
| noemit | umami | go | 3 | 1.38 s (1.35 s..1.39 s) | 6.81 s | 859 MB | 37.76G |  |  |  |
| noemit | umami | main | 3 | 736 ms (733 ms..744 ms) | 2.49 s | 767 MB | 19.57G | 0.53× |  | same |
| noemit | umami | head | 3 | 736 ms (735 ms..749 ms) | 2.49 s | 769 MB | 19.61G | 0.53× | 1.00× | same |
| noemit | vscode | go | 6 | 8.94 s (8.28 s..9.42 s) | 53.75 s | 5924 MB | 247.94G |  |  |  |
| noemit | vscode | main | 3 | 5.41 s (5.40 s..5.42 s) | 23.74 s | 5404 MB | 174.67G | 0.61× |  | same |
| noemit | vscode | head | 6 | 5.71 s (5.38 s..6.36 s) | 24.56 s | 5401 MB | 174.46G | 0.64× | 1.05× | same |
| noemit | vscode | head-thp0 | 3 | 5.82 s (5.58 s..6.23 s) | 25.72 s | 5469 MB | 174.44G | 0.65× |  | same |
| noemit | zod | go | 4 | 1.04 s (1.03 s..1.05 s) | 5.57 s | 917 MB | 32.24G |  |  |  |
| noemit | zod | main | 2 | 474 ms (470 ms..478 ms) | 1.76 s | 673 MB | 14.31G | 0.46× |  | same |
| noemit | zod | head | 4 | 487 ms (469 ms..679 ms) | 1.82 s | 673 MB | 14.31G | 0.47× | 1.03× | same |
| noemit | zod | head-thp0 | 2 | 477 ms (476 ms..478 ms) | 1.75 s | 694 MB | 14.30G | 0.46× |  | same |
| open12 | honojs__hono | go | 5 | 121 ms (114 ms..186 ms) | 1.21 s | 226 MB | - |  |  |  |
| open12 | honojs__hono | main | 5 | 126 ms (119 ms..204 ms) | 530 ms | 241 MB | - | **1.04× slower** |  |  |
| open12 | honojs__hono | head | 5 | 106 ms (100 ms..111 ms) | 520 ms | 236 MB | - | 0.88× | 0.84× |  |
| probe-ts | umami | go | 5 | 14 ms (12 ms..19 ms) | 522 ms | - | - |  |  |  |
| probe-ts | umami | main | 5 | 6 ms (3 ms..6 ms) | 12 ms | - | - | 0.39× |  |  |
| probe-ts | umami | head | 5 | 4 ms (3 ms..9 ms) | 8 ms | - | - | 0.26× | 0.65× |  |
| probe-tsx | umami | go | 5 | 62 ms (52 ms..66 ms) | 1.45 s | - | - |  |  |  |
| probe-tsx | umami | main | 5 | 103 ms (90 ms..111 ms) | 315 ms | - | - | **1.67× slower** |  |  |
| probe-tsx | umami | head | 5 | 8 ms (7 ms..10 ms) | 25 ms | - | - | 0.13× | 0.08× |  |
| single | effect | go | 3 | 2.58 s (2.53 s..2.59 s) | 3.41 s | 695 MB | 23.66G |  |  |  |
| single | effect | main | 3 | 1.76 s (1.75 s..1.80 s) | 1.72 s | 599 MB | 14.73G | 0.68× |  | same |
| single | effect | head | 3 | 1.75 s (1.75 s..1.84 s) | 1.71 s | 599 MB | 14.72G | 0.68× | 0.99× | same |
| single | hono | go | 3 | 407 ms (403 ms..412 ms) | 550 ms | 169 MB | 4.24G |  |  |  |
| single | hono | main | 3 | 248 ms (245 ms..250 ms) | 247 ms | 148 MB | 2.15G | 0.61× |  | same |
| single | hono | head | 3 | 247 ms (245 ms..249 ms) | 249 ms | 148 MB | 2.14G | 0.61× | 1.00× | same |
| single | query | go | 3 | 135 ms (132 ms..143 ms) | 206 ms | 79 MB | 1.33G |  |  |  |
| single | query | main | 3 | 96 ms (95 ms..97 ms) | 98 ms | 75 MB | 0.69G | 0.71× |  | same |
| single | query | head | 3 | 97 ms (95 ms..98 ms) | 98 ms | 76 MB | 0.69G | 0.72× | 1.01× | same |
| single | t3-server | go | 2 | 15.08 s (14.92 s..15.24 s) | 19.34 s | 2578 MB | 172.86G |  |  |  |
| single | t3-server | main | 2 | 7.55 s (7.44 s..7.65 s) | 7.27 s | 2178 MB | 71.40G | 0.50× |  | same |
| single | t3-server | head | 1 | 7.59 s (7.59 s..7.59 s) | 7.35 s | 2177 MB | 71.34G | 0.50× | 1.01× | same |
| single | t3-web | go | 3 | 7.59 s (7.59 s..7.75 s) | 10.11 s | 1830 MB | 80.55G |  |  |  |
| single | t3-web | main | 3 | 4.88 s (4.80 s..4.97 s) | 4.76 s | 1359 MB | 47.07G | 0.64× |  | same |
| single | t3-web | head | 3 | 4.93 s (4.82 s..5.10 s) | 4.80 s | 1359 MB | 47.02G | 0.65× | 1.01× | same |
| single | ts-pattern | go | 3 | 114 ms (113 ms..117 ms) | 169 ms | 67 MB | 1.25G |  |  |  |
| single | ts-pattern | main | 3 | 74 ms (72 ms..75 ms) | 78 ms | 66 MB | 0.59G | 0.65× |  | same |
| single | ts-pattern | head | 3 | 73 ms (71 ms..74 ms) | 75 ms | 66 MB | 0.59G | 0.64× | 0.99× | same |
| single | umami | go | 3 | 2.50 s (2.46 s..2.50 s) | 3.58 s | 782 MB | 27.81G |  |  |  |
| single | umami | main | 3 | 1.65 s (1.65 s..1.65 s) | 1.62 s | 655 MB | 16.50G | 0.66× |  | same |
| single | umami | head | 3 | 1.65 s (1.65 s..1.66 s) | 1.62 s | 655 MB | 16.50G | 0.66× | 1.00× | same |
| single | vscode | go | 3 | 25.27 s (25.14 s..26.95 s) | 34.88 s | 5184 MB | 218.21G |  |  |  |
| single | vscode | main | 2 | 19.57 s (19.22 s..19.93 s) | 18.80 s | 4824 MB | 164.15G | 0.77× |  | same |
| single | vscode | head | 3 | 19.78 s (19.48 s..20.14 s) | 19.00 s | 4824 MB | 163.93G | 0.78× | 1.01× | same |
| single | zod | go | 3 | 2.23 s (2.21 s..2.24 s) | 3.39 s | 683 MB | 24.18G |  |  |  |
| single | zod | main | 3 | 1.27 s (1.26 s..1.28 s) | 1.25 s | 513 MB | 11.53G | 0.57× |  | same |
| single | zod | head | 3 | 1.26 s (1.26 s..1.28 s) | 1.24 s | 513 MB | 11.53G | 0.57× | 0.99× | same |
| ttfd | umami | go | 5 | 299 ms (282 ms..366 ms) | 3.25 s | 447 MB | - |  |  |  |
| ttfd | umami | main | 5 | 413 ms (395 ms..523 ms) | 1.43 s | 531 MB | - | **1.38× slower** |  |  |
| ttfd | umami | head | 5 | 278 ms (266 ms..303 ms) | 1.52 s | 528 MB | - | 0.93× | 0.67× |  |
