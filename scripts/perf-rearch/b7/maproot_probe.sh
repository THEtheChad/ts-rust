#!/bin/bash
# Lane B7 round 3: is a relative mapRoot resolved against the current directory anywhere in emit?
# usage: maproot_probe.sh <go bin> <tsc-rs bin> <scratch dir>
# Four runs per side in <scratch>/{go,rs}/w: -p sub/tsconfig.json from w (mapRoot "maps" in the config),
# -p sub/tsconfig.2.json ("../m2" with sourceRoot ""), -p sub/tsconfig.json --mapRoot rel3 from w, and
# -p tsconfig.json --mapRoot rel4 from w/sub. Then diff -r of the two sides (outputs, maps, logs).
set -u
GO=$1 RS=$2 D=$3
for side in go rs; do
  rm -rf "${D:?}/${side:?}"
  mkdir -p "$D/$side/w/sub/src/lib"
  cd "$D/$side/w" || exit 1
  printf 'export const a = 1;\n' > sub/src/a.ts
  printf 'export const b = 2;\n' > sub/src/lib/b.ts
  echo '{"compilerOptions":{"rootDir":"src","sourceMap":true,"declaration":true,"declarationMap":true,"outDir":"out","mapRoot":"maps","types":[]},"include":["src"]}' > sub/tsconfig.json
  echo '{"compilerOptions":{"rootDir":"src","sourceMap":true,"outDir":"out2","mapRoot":"../m2","sourceRoot":"","types":[]},"include":["src"]}' > sub/tsconfig.2.json
  B=$GO; [ $side = rs ] && B=$RS
  "$B" -p sub/tsconfig.json > ../out1.txt 2>&1; echo "rc $?" >> ../out1.txt
  "$B" -p sub/tsconfig.2.json > ../out2.txt 2>&1; echo "rc $?" >> ../out2.txt
  "$B" -p sub/tsconfig.json --mapRoot rel3 --outDir out3 > ../out3.txt 2>&1; echo "rc $?" >> ../out3.txt
  (cd sub && "$B" -p tsconfig.json --mapRoot rel4 --outDir out4 > ../../out4.txt 2>&1; echo "rc $?" >> ../../out4.txt)
done
cd "$D" && diff -r go rs && echo "MAPROOT SAME"
