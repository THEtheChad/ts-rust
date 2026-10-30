// Picks files and positions in a project, the same way for every server.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const SKIP_DIRS = new Set(["node_modules", ".git", "dist", "build", ".next", "coverage", "out"]);

export function listSourceFiles(root) {
  const files = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir).sort()) {
      if (SKIP_DIRS.has(name)) continue;
      const path = join(dir, name);
      const st = statSync(path);
      if (st.isDirectory()) walk(path);
      else if (/\.(ts|tsx|mts|cts)$/.test(name) && !name.endsWith(".d.ts")) files.push(path);
    }
  };
  walk(root);
  return files;
}

// Chooses `count` files spread evenly over the sorted list, skipping tiny and huge files.
export function pickFiles(root, count, explicit) {
  if (explicit?.length) return explicit.map((f) => join(root, f));
  const files = listSourceFiles(root).filter((f) => {
    const size = statSync(f).size;
    return size > 400 && size < 60000;
  });
  if (files.length <= count) return files;
  const step = files.length / count;
  return Array.from({ length: count }, (_, i) => files[Math.floor(i * step + step / 2)]);
}

export const languageId = (path) => (/x$/.test(path) ? "typescriptreact" : "typescript");

// Strips comments and string contents (keeping offsets) so identifiers are found in code only.
function maskNonCode(text) {
  return text.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*|`(?:\\[\s\S]|[^`\\])*`|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'/g, (m) =>
    m.replace(/[^\n]/g, " "),
  );
}

export function offsetToPosition(text, offset) {
  let line = 0;
  let lineStart = 0;
  for (let i = 0; i < offset; i++) {
    if (text.charCodeAt(i) === 10) {
      line++;
      lineStart = i + 1;
    }
  }
  return { line, character: offset - lineStart };
}

export function endPosition(text) {
  return offsetToPosition(text, text.length);
}

// Identifier positions (middle of the identifier) and member-access positions (just after a dot),
// `count` of each, evenly spread through the file.
export function pickPositions(text, count) {
  const code = maskNonCode(text);
  const idents = [];
  const members = [];
  const re = /[A-Za-z_$][\w$]*/g;
  for (let m; (m = re.exec(code)); ) {
    if (KEYWORDS.has(m[0])) continue;
    idents.push(m.index + (m[0].length >> 1));
    if (m.index > 0 && code[m.index - 1] === ".") members.push(m.index);
  }
  const spread = (list) => {
    if (list.length <= count) return list;
    const step = list.length / count;
    return Array.from({ length: count }, (_, i) => list[Math.floor(i * step + step / 2)]);
  };
  return {
    idents: spread(idents).map((o) => offsetToPosition(text, o)),
    members: spread(members).map((o) => offsetToPosition(text, o)),
  };
}

const KEYWORDS = new Set(
  "abstract any as async await boolean break case catch class const constructor continue debugger declare default delete do else enum export extends false finally for from function get if implements import in infer instanceof interface is keyof let module namespace never new null number object of package private protected public readonly require return satisfies set static string super switch symbol this throw true try type typeof undefined unique unknown var void while with yield".split(
    " ",
  ),
);

export const rel = (root, path) => relative(root, path);
export const readText = (path) => readFileSync(path, "utf8");
