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
import { declarations } from "./css-declarations.mjs";

const SRC = path.join(import.meta.dirname, "..", "src");
const read = (name) => readFileSync(path.join(SRC, name), "utf8");

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

const styles = () => declarations(read("styles.css"));

test("every font-weight is a weight token", () => {
  const bad = styles()
    .filter((d) => d.property === "font-weight" && !/^var\(--weight-(regular|medium|semibold|bold|black)\)$/.test(d.value))
    .map((d) => where("styles.css", d));
  assert.deepEqual(bad, []);
});

// The two line boxes that are a length on purpose.
const LEADING_EXCEPTIONS = {
  // The tab label's line box is what holds the phone tab bar at the design's
  // height; three Figma frames (69:763, 69:793, 69:800) assert that height.
  ".sidebar .nav-item > span": "10px",
  // A tap-target hack: the summary is made 44px tall by its line box. It
  // should become min-height plus centring, and then this entry can go.
  ".issue-catalog-card summary": "44px",
};

test("every line-height is a leading token or a written exception", () => {
  const bad = styles()
    .filter((d) => d.property === "line-height")
    .filter((d) => !/^var\(--leading-(none|tight|snug|normal|relaxed)\)$/.test(d.value))
    .filter((d) => LEADING_EXCEPTIONS[d.selector] !== d.value)
    .map((d) => where("styles.css", d));
  assert.deepEqual(bad, []);
});
