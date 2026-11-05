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

// ata-small: JS files in no tsconfig (an inferred project, type acquisition on) that import packages
// without types: ata-fake-lib (a dependency in package.json, in node_modules) and ata-fake-two
// (imported only by an overlay the scenario opens). ATA_STORE is what the stand-in npm (ata-npm/)
// installs: the types registry and the two @types packages.
FIXTURES["ata-small"] = {
  "package.json": json({ name: "ata-small", version: "1.0.0", dependencies: { "ata-fake-lib": "1.0.0" } }),
  "node_modules/ata-fake-lib/package.json": json({ name: "ata-fake-lib", version: "1.0.0", main: "index.js" }),
  "node_modules/ata-fake-lib/index.js": "exports.pad = (text, n) => text.padStart(n);\n",
  "src/app.js": ['import { pad } from "ata-fake-lib";', "", 'export const padded = pad("a", 3);', ""].join("\n"),
};
export const ATA_STORE = {
  "registry.json": json({ entries: { "ata-fake-lib": { latest: "1.0.0", "ts7.1": "1.0.0" }, "ata-fake-two": { latest: "2.0.0", "ts7.1": "2.0.0" } } }),
  "types/ata-fake-lib/package.json": json({ name: "@types/ata-fake-lib", version: "1.0.0", types: "index.d.ts" }),
  "types/ata-fake-lib/index.d.ts": "/** Pads `text` to `n` characters. */\nexport declare function pad(text: string, n: number): string;\n",
  "types/ata-fake-two/package.json": json({ name: "@types/ata-fake-two", version: "2.0.0", types: "index.d.ts" }),
  "types/ata-fake-two/index.d.ts": "export declare function twice(n: number): number;\n",
};

// Writes a { path: text } map into a fresh dir.
export function writeTree(files, root) {
  rmSync(root, { recursive: true, force: true });
  for (const [rel, text] of Object.entries(files)) {
    mkdirSync(dirname(join(root, rel)), { recursive: true });
    writeFileSync(join(root, rel), text);
  }
}

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
