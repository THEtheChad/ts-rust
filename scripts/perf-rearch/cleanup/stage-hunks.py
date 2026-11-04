#!/usr/bin/env python3
"""Stage only the hunks of FILE whose text matches REGEX (a non-interactive `git add -p`).
usage: stage-hunks.py FILE REGEX   (run from the repo root)"""
import re, subprocess, sys
if len(sys.argv) != 3:
    sys.exit(__doc__)
path, rx = sys.argv[1], re.compile(sys.argv[2])
diff = subprocess.run(["git", "diff", "-U0", "--", path], capture_output=True, text=True, check=True).stdout
head, *hunks = re.split(r"(?m)^(?=@@ )", diff)
keep = [h for h in hunks if rx.search(h)]
if keep:
    subprocess.run(["git", "apply", "--cached", "--unidiff-zero", "-"], input=head + "".join(keep), text=True, check=True)
print(f"{path}: staged {len(keep)} of {len(hunks)} hunks")
