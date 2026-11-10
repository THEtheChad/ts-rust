// JSON without lossy parsing (lane H8). A server's answer is compared as canonical JSON (keys in the
// server's order, strings re-escaped, no insignificant whitespace), so every value must survive a parse
// and a stringify unchanged. JSON.parse alone does not keep numbers: `1e999` becomes Infinity, which
// JSON.stringify writes as null, so a server answering 1e999 where Go answers null matched; `-0`, `1.0`,
// `1E2` and integers above 2^53 also lose their text. A number whose text is not the one JSON.stringify
// writes for its value is kept as that exact text (JSON.rawJSON, which JSON.stringify writes verbatim), so
// it is a difference wherever the other side wrote another text.
//
// Cost (lane H8 round 2): a reviver on every value made a parse 7 times slower (umami's logs: 340 MB a
// run). parseStrict scans the text once (scan: an escape-aware tokenizer of strings, brackets, the commas
// before keys, and the number tokens that can be non-canonical), checks every object's decoded keys for a
// duplicate and every such number for its canonical text, and parses with plain JSON.parse when every
// number is canonical, with the reviver only when one is not. Whoever keeps such a value marks it
// (`rawNumbers` on a log entry), and readers parse with parseKeep only what is marked.

const keepNumbers = (key, value, context) =>
  typeof value === "number" && context?.source !== undefined && JSON.stringify(value) !== context.source ? JSON.rawJSON(context.source) : value;

// JSON.parse, keeping every number's text where its value would not give it back.
export const parseKeep = (text) => JSON.parse(text, keepNumbers);

// The tokens of a JSON text that decide its keys and its numbers: strings (escape-aware), brackets, a
// comma before a string (a key follows a comma only as a string), and the number tokens that can be
// non-canonical (a fraction or an exponent, -0, 16 or more digits). A plain integer of up to 15 digits
// is always canonical and is not a token here, so the long arrays of semantic tokens cost nothing.
const SCAN = /"(?:[^"\\]|\\.)*"|[{}[\]]|,(?=[ \t\r\n]*")|-?\d+\.\d+(?:[eE][+-]?\d+)?|-?\d+[eE][+-]?\d+|-0(?![.\deE])|-?\d{16,}/g;

// { dup: the first key an object of `text` has twice, or null; exact: every number canonical }. Meaningful
// for a text JSON.parse accepts (parseStrict parses it).
export function scan(text) {
  const stack = []; // per open container: the keys seen so far (an object) or null (an array)
  let prev = "";
  let exact = true;
  for (const [tok] of text.matchAll(SCAN)) {
    const c = tok[0];
    if (c === "{") stack.push(new Set());
    else if (c === "[") stack.push(null);
    else if (c === "}" || c === "]") stack.pop();
    else if (c === '"') {
      const keys = stack[stack.length - 1];
      if (keys && (prev === "{" || prev === ",")) {
        const k = JSON.parse(tok);
        if (keys.has(k)) return { dup: k, exact };
        keys.add(k);
      }
    } else if (c !== ",") {
      if (exact && JSON.stringify(Number(tok)) !== tok) exact = false;
      prev = "n";
      continue;
    }
    prev = c;
  }
  return { dup: null, exact };
}

// The first key an object of `text` has twice, or null.
export const duplicateKey = (text) => scan(text).dup;

// A frame's body as { value, exact }, strictly: one JSON text (RFC 8259: no BOM, no NaN or Infinity, no
// comment), no duplicate key, every number's text kept (exact: no number needed keeping). Throws with the
// reason.
export function parseStrict(text) {
  const { dup, exact } = scan(text);
  let value;
  try {
    value = exact ? JSON.parse(text) : parseKeep(text);
  } catch (e) {
    throw new Error(`not JSON: ${e.message.slice(0, 120)}`);
  }
  if (dup !== null) throw new Error(`a duplicate key ${JSON.stringify(dup).slice(0, 80)}`);
  return { value, exact };
}

// A log line written by lspdiff: its entry, with kept numbers where the entry is marked `rawNumbers`.
export const parseEntry = (line) => (line.includes('"rawNumbers":true') ? parseKeep(line) : JSON.parse(line));
