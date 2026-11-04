// Small synthetic projects for the watch scenarios (scenario.mjs). Each is written fresh into a
// scratch dir before every server run, so file-system events start from the same tree.
import { mkdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

const json = (v) => JSON.stringify(v, null, 2) + "\n";

// watch-small: relative imports (one missing at first), a bare package in node_modules, a
// package linked into node_modules by a directory symlink (as pnpm and workspaces do), and a
// package.json "imports" subpath. Every resolution kind the loader caches has a file-system event
// in the fsevents scenario that changes its answer. A { symlink } value makes a symlink.
export const FIXTURES = {
  "watch-small": {
    "package.json": json({ name: "watch-small", version: "1.0.0", type: "module", imports: { "#internal/*": "./src/internal/*.js" } }),
    "tsconfig.json": json({
      compilerOptions: { strict: true, module: "nodenext", moduleResolution: "nodenext", target: "es2022", noEmit: true, skipLibCheck: false },
      include: ["src/**/*"],
    }),
    "src/a.ts": [
      'import { b } from "./b.js";',
      'import { c } from "./c.js";',
      'import { fake } from "fake-pkg";',
      'import { helper } from "#internal/helper";',
      'import { linked } from "linked-pkg";',
      "export const total: number = b + c + fake + helper + linked;",
      "export const parts = { b, c, fake, helper, linked };",
      "",
    ].join("\n"),
    "src/b.ts": "export const b = 1;\n",
    "src/internal/helper.ts": "export const helper = 2;\n",
    "src/internal/helper2.ts": 'export const helper = "two";\n',
    "src/uses.ts": "export const u = hel;\n",
    "node_modules/fake-pkg/package.json": json({ name: "fake-pkg", version: "1.0.0", types: "./index.d.ts" }),
    "node_modules/fake-pkg/index.d.ts": "export declare const fake: number;\n",
    "node_modules/fake-pkg/other.d.ts": "export declare const fake: string;\n",
    "packages/linked-pkg/package.json": json({ name: "linked-pkg", version: "1.0.0", types: "./index.d.ts" }),
    "packages/linked-pkg/index.d.ts": "export declare const linked: number;\n",
    "node_modules/linked-pkg": { symlink: "../packages/linked-pkg" },
  },
};

// Replaces `root` with a fresh copy of the fixture.
export function writeFixture(name, root) {
  const files = FIXTURES[name];
  if (!files) throw new Error(`unknown fixture ${name}`);
  rmSync(root, { recursive: true, force: true });
  for (const [rel, text] of Object.entries(files)) {
    mkdirSync(dirname(join(root, rel)), { recursive: true });
    if (typeof text === "string") writeFileSync(join(root, rel), text);
    else symlinkSync(text.symlink, join(root, rel), "dir");
  }
}
