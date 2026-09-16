// The design system's rules, checked against the stylesheets as text.
//
// DESIGN_SYSTEM.md has always said screens should use tokens rather than raw
// values, and an audit on 2026-09-16 found it had not held: eleven type steps
// five of them a pixel apart, spacing tokens used 4 times in ~1,200 chances,
// 25 radii. Nothing failed when any of that landed. This does.
//
// Every exception below carries its reason, the same way node-map.json treats
// a Figma deviation: a written ledger, not a silent pass.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

const SRC = path.join(import.meta.dirname, "..", "src");
const read = (name) => readFileSync(path.join(SRC, name), "utf8");

// Every declaration in a stylesheet, with the at-rules it sits inside. Not a
// full CSS parser -- it only has to understand this project's two files --
// but it keeps line numbers so a failure says where to look.
export function declarations(css) {
  const out = [];
  const stack = [];
  let line = 1;
  let buffer = "";
  let bufferLine = 1;
  for (let i = 0; i < css.length; i += 1) {
    const ch = css[i];
    if (ch === "/" && css[i + 1] === "*") {
      const end = css.indexOf("*/", i + 2);
      const stop = end === -1 ? css.length : end + 2;
      for (let j = i; j < stop; j += 1) if (css[j] === "\n") line += 1;
      i = stop - 1;
      continue;
    }
    if (ch === "\n") line += 1;
    if (ch === "{") {
      stack.push(buffer.trim());
      buffer = "";
      bufferLine = line;
      continue;
    }
    if (ch === ";" || ch === "}") {
      const text = buffer.trim();
      const colon = text.indexOf(":");
      if (colon > 0 && stack.length) {
        out.push({
          property: text.slice(0, colon).trim(),
          value: text.slice(colon + 1).trim(),
          line: bufferLine,
          selector: stack[stack.length - 1],
          media: stack.filter((s) => s.startsWith("@media")),
        });
      }
      buffer = "";
      bufferLine = line;
      if (ch === "}") stack.pop();
      continue;
    }
    if (!buffer.trim()) bufferLine = line;
    buffer += ch;
  }
  return out;
}

const SIZE_TOKEN = /^--(text|space|radius)-|^--control-font-|^--status-font-size$/;
const where = (file, d) => `${file}:${d.line}  ${d.selector} { ${d.property}: ${d.value} }`;

for (const file of ["design-tokens.css", "styles.css"]) {
  test(`${file}: no size token is redefined inside a media query`, () => {
    // A token redefined per breakpoint stops being one value. The case that
    // prompted this: the mobile block set --control-font-sm to a raw 15px,
    // which quietly cut it loose from the ladder it was meant to alias, so a
    // change to the ladder no longer reached phone controls.
    const bad = declarations(read(file))
      .filter((d) => d.media.length && SIZE_TOKEN.test(d.property))
      .map((d) => where(file, d));
    assert.deepEqual(bad, []);
  });
}

test("the parser sees declarations inside nested at-rules", () => {
  const found = declarations("a{color:red}\n@media (x){b{--text-sm: 1px;}}");
  assert.equal(found.length, 2);
  assert.deepEqual(found[1].media, ["@media (x)"]);
  assert.equal(found[1].line, 2);
});
