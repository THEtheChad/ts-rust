// JSON without lossy parsing (lane H8). A server's answer is compared as canonical JSON (keys in the
// server's order, strings re-escaped, no insignificant whitespace), so every value must survive a parse
// and a stringify unchanged. JSON.parse alone does not keep numbers: `1e999` becomes Infinity, which
// JSON.stringify writes as null, so a server answering 1e999 where Go answers null matched; `-0`, `1.0`,
// `1E2` and integers above 2^53 also lose their text. parseKeep keeps every number whose text is not the
// one JSON.stringify writes for its value as that exact text (JSON.rawJSON, which JSON.stringify writes
// verbatim), so such a number is a difference wherever the other side wrote another text. parseStrict
// also refuses what has no single value: a duplicate key (JSON.parse keeps the last), and a body that is
// not one JSON text.

const keepNumbers = (key, value, context) =>
  typeof value === "number" && context?.source !== undefined && JSON.stringify(value) !== context.source ? JSON.rawJSON(context.source) : value;

// JSON.parse, keeping every number's text where its value would not give it back.
export const parseKeep = (text) => JSON.parse(text, keepNumbers);

// The string, bracket and key-comma tokens of a JSON text that JSON.parse accepted: enough to see each
// object's keys (a key is a string right after `{`, or after a `,` in an object). A comma is a token only
// before a string, so the long number arrays of semantic tokens cost nothing here.
const TOKENS = /"(?:[^"\\]|\\.)*"|[{}[\]]|,(?=[ \t\r\n]*")/g;

// The first key that an object of `text` has twice, or null.
export function duplicateKey(text) {
  const stack = []; // per open container: the keys seen so far (an object) or null (an array)
  let prev = "";
  for (const [tok] of text.matchAll(TOKENS)) {
    const c = tok[0];
    if (c === "{") stack.push(new Set());
    else if (c === "[") stack.push(null);
    else if (c === "}" || c === "]") stack.pop();
    else if (c === '"') {
      const keys = stack[stack.length - 1];
      if (keys && (prev === "{" || prev === ",")) {
        const k = JSON.parse(tok);
        if (keys.has(k)) return k;
        keys.add(k);
      }
    }
    prev = c;
  }
  return null;
}

// A frame's body as a value, strictly: one JSON text (RFC 8259: no BOM, no NaN or Infinity, no comment),
// no duplicate key, every number's text kept (parseKeep). Throws with the reason.
export function parseStrict(text) {
  let v;
  try {
    v = parseKeep(text);
  } catch (e) {
    throw new Error(`not JSON: ${e.message.slice(0, 120)}`);
  }
  const dup = duplicateKey(text);
  if (dup !== null) throw new Error(`a duplicate key ${JSON.stringify(dup).slice(0, 80)}`);
  return v;
}
