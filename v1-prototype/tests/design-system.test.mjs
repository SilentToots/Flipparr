// The design system's rules, checked against the stylesheets as text.
//
// DESIGN_SYSTEM.md has always said screens should use tokens rather than raw
// values, and an audit on 2026-09-16 found it had not held: eleven type steps
// five of them a pixel apart, spacing tokens used 4 times in ~1,200 chances,
// 25 radii. Nothing failed when any of that landed. This does.
//
// Every exception below carries its reason: a written ledger, not a silent
// pass.

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

// The line box that is a length on purpose.
const LEADING_EXCEPTIONS = {
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

const LADDER = ["2xs", "xs", "sm", "md", "lg", "xl", "2xl"];
const tokenPx = (name) => {
  const m = read("design-tokens.css").match(new RegExp(`^\\s*--text-${name}:\\s*([\\d.]+)(rem|px);`, "m"));
  return m && (m[2] === "rem" ? +m[1] * 16 : +m[1]);
};

test("the type ladder is seven steps, 10px up, at least 2px apart", () => {
  const px = LADDER.map(tokenPx);
  assert.deepEqual(px, [10, 12, 14, 16, 18, 20, 24]);
  const defined = [...read("design-tokens.css").matchAll(/^\s*--text-([a-z0-9]+):/gm)].map((m) => m[1]);
  assert.deepEqual(defined.filter((n) => n !== "input"), LADDER, "a step was added or renamed");
});

// 10px is the floor, and it is for chips, not reading. These are the places
// it is allowed: text inside a badge or chip, the phone's two-up card
// metadata, where two cards share 375px, and the tab bar's count.
const SMALLEST_TEXT = new Set([
  ".library-match-art b",
  ".run-status",
  ".publication-status, .monitoring-status",
  ".status-badge, .status-chip, .provider-state, .segmented-tab b",
  ".series-card :is(.publication-status, .monitoring-status)",
  ".new-run-copy p",
  ".series-card-byline",
  ".ownership.compact .ownership-label",
  ".sidebar .nav-item > b",
  // A tablet's rail: labels under their icons, as YouTube's mini guide.
  ".sidebar nav .nav-item",
  ".sidebar nav .nav-item > b",
]);

test("every font-size is a step on the ladder", () => {
  const ok = (d) =>
    /^var\(--text-(xs|sm|md|lg|xl|2xl)\)$/.test(d.value)
    || (d.value === "var(--text-2xs)" && SMALLEST_TEXT.has(d.selector.replace(/\s+/g, " ")))
    || (d.value === "var(--text-input)" && /\b(input|select|textarea)\b/.test(d.selector))
    || /^clamp\(var\(--text-[a-z0-9]+\), [^,]+, var\(--text-[a-z0-9]+\)\)$/.test(d.value)
    || d.value === "inherit"
    // The setup step marker is a dot; its number is for screen readers.
    || (d.value === "0" && d.selector === ".setup-step-marker");
  const bad = styles().filter((d) => d.property === "font-size" && !ok(d)).map((d) => where("styles.css", d));
  assert.deepEqual(bad, []);
});

const SPACE_SCALE = [2, 4, 6, 8, 10, 12, 14, 16, 20, 24, 28, 32, 40, 48, 64, 80, 96];
const SPACING = /^((row-|column-)?gap|padding(-[a-z]+){0,2}|margin(-[a-z]+){0,2})$/;
// The design's badge: 12px of icon and text centred in an 18px chip, which
// puts 3px above and below. The one spacing value off the 2px grid.
const BADGE_INSET = new Set([".publication-status, .monitoring-status", ".run-status", ".library-match-art b",
  ".status-badge, .status-chip, .provider-state, .segmented-tab b"]);

test("every spacing token is named for its value, and the scale is the published one", () => {
  const defined = [...read("design-tokens.css").matchAll(/^\s*--space-(\d+):\s*([^;]+);/gm)];
  assert.deepEqual(defined.map((m) => +m[1]), SPACE_SCALE);
  for (const [, n, value] of defined) assert.equal(value, `${n}px`, `--space-${n}`);
});

test("every padding, margin and gap is on the spacing scale", () => {
  const bad = [];
  for (const d of styles().filter((x) => SPACING.test(x.property))) {
    const selector = d.selector.replace(/\s+/g, " ");
    for (const [, px] of d.value.matchAll(/(?<![\w.#-])-?(\d*\.?\d+)px\b/g)) {
      const n = +px;
      if (n === 0 || n === 1) continue;                            // hairlines
      if (n === 3 && BADGE_INSET.has(selector)) continue;
      bad.push(`${where("styles.css", d)}  (${px}px: use a --space-* step)`);
    }
    if (/\b\d*\.?\d+(em|rem|vw|vh)\b/.test(d.value.replace(/var\([^)]*\)|env\([^)]*\)/g, ""))) {
      bad.push(`${where("styles.css", d)}  (relative length)`);
    }
  }
  assert.deepEqual(bad, []);
});

test("every border-radius is a radius token, a circle, or none", () => {
  const ok = (part) => /^var\(--radius-(4|6|8|12|16|24|pill)\)$/.test(part) || ["0", "50%", "inherit"].includes(part);
  const bad = styles()
    .filter((d) => d.property === "border-radius" && !d.value.split(/\s+/).every(ok))
    .map((d) => where("styles.css", d));
  assert.deepEqual(bad, []);
});

// Buttons have one corner: 8px, the design's for a control. The exceptions
// are shapes, not sizes -- pills and circles -- and buttons nested inside
// another control, which take 4px inside their 8px frame the way the
// design's view toggle does.
const NESTED_BUTTONS = new Set([
  ".scope-toggle button", ".view-toggle button", ".sort-menu button",
  ".file-actions-menu > div button", ".library-sheet-segments button", ".library-sheet-options button",
  ".notifications-open", ".notifications-dismiss",
]);

test("buttons take the 8px corner unless they are a pill, a circle, or nested", () => {
  const isButton = (sel) => sel.split(",").some((s) => /(^|\s|>)button$|-button$|^\.(primary|secondary|ghost|danger|ui|filter)-button\b/.test(s.trim()));
  const bad = styles()
    .filter((d) => d.property === "border-radius" && isButton(d.selector.replace(/\s+/g, " ")))
    .filter((d) => !/^var\(--radius-(8|pill)\)$|^50%$|^inherit$|^0$/.test(d.value))
    .filter((d) => !(NESTED_BUTTONS.has(d.selector.replace(/\s+/g, " ")) && d.value === "var(--radius-4)"))
    .map((d) => where("styles.css", d));
  assert.deepEqual(bad, []);
});

// Colours live in design-tokens.css. Two literal forms are allowed here,
// because neither is a colour choice: black as a mask stencil, where only
// its opacity is read, and the tab glass, whose white is scaled by the
// --nav-glass-* tokens inside calc().
const COLOUR = /#[0-9a-f]{3,8}\b|\b(rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(|\b(white|black|red|green|blue|gr[ae]y|silver|purple|violet|orange|yellow)\b/i;

test("no colour is written in the stylesheet itself", () => {
  const bad = [];
  for (const d of styles()) {
    if (d.property.startsWith("--")) continue;
    let value = d.value;
    for (let i = 0; i < 4; i += 1) value = value.replace(/var\([^()]*\)/g, "");
    if (/mask-image$/.test(d.property)) value = value.replace(/#000\b/g, "");
    if (d.selector === ".glass-indicator") value = value.replace(/rgb\(255 255 255 \/ [^;]*?\)\s*\)?/g, "");
    if (COLOUR.test(value)) bad.push(where("styles.css", d));
  }
  assert.deepEqual(bad, []);
});

test("the chrome grey is never text, except on icon-only buttons", () => {
  const ICON_ONLY = new Set([".sidebar-scan", ".view-toggle button"]);
  const bad = styles()
    .filter((d) => d.property === "color" && d.value.includes("--chrome-label"))
    .filter((d) => !ICON_ONLY.has(d.selector.replace(/\s+/g, " ")))
    .map((d) => where("styles.css", d));
  assert.deepEqual(bad, []);
});

// Three widths, where the layout changes: a phone up to 640px, a tablet with
// the icon rail up to 900px, and desktop, with one wide-content exception.
// Each crossing is written as max-width on one side and min-width one pixel
// later on the other. Container queries are components sizing themselves and
// are not breakpoints.
const BREAKPOINTS = new Set(["max-width: 640px", "min-width: 641px", "max-width: 900px", "min-width: 901px", "max-width: 1100px"]);

for (const file of ["design-tokens.css", "styles.css"]) {
  test(`${file}: every media query width is one of the breakpoints`, () => {
    const bad = [];
    read(file).split("\n").forEach((text, i) => {
      for (const [, query] of text.matchAll(/@media\s*\(([^)]*width[^)]*)\)/g)) {
        if (!BREAKPOINTS.has(query.trim())) bad.push(`${file}:${i + 1}  @media (${query})`);
      }
    });
    assert.deepEqual(bad, []);
  });
}

// Glass is the navigation layer's material. Content -- cards, rows, covers --
// stays solid, and glass never sits on glass, so a blur may only appear on
// the controls and bars named here, each with its reason.
const GLASS = new Map([
  [".sidebar", "the floating sidebar above 640px"],
  [".glass-button, .glass-field, .glass-capsule, .glass-menu", "the page header's controls and search field, grouped controls in a capsule, and menus that grow out of them"],
  [".glass-indicator", "the sliding thumb in the tab bar, segmented tabs and drawer tabs"],
  [".sidebar nav", "the phone's floating tab bar"],
  [".page-header::before", "the header's scroll edge, which blurs content passing under it"],
  [".comic-drawer-bar::before", "a drawer's pinned top bar, frosted once the title scrolls under it"],
]);

test("backdrop blur is only on the navigation layer", () => {
  const bad = declarations(read("styles.css"))
    .filter((d) => /backdrop-filter$/.test(d.property) && d.value !== "none")
    .filter((d) => !GLASS.has(d.selector.replace(/\s+/g, " ")))
    .map((d) => where("styles.css", d));
  assert.deepEqual(bad, []);
});
