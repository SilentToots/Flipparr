// Assert the running app against the Figma file.
//
//   node tests/visual/figma-check.mjs
//
// Unlike compare.mjs, which tolerates drift because a palette migration is
// meant to move things, this fails on any difference: the values in
// figma-spec.mjs are a specification, and there is no legitimate drift from
// one. It exists because "looks close in a screenshot" shipped twice.

import { chromium } from "playwright";
import { spec, icons, frames } from "./figma-spec.generated.mjs";

const origin = process.env.FLIPPARR_UI_ORIGIN || "http://localhost:4173";
const width = 1440;
const height = 951;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width, height } });
await page.goto(`${origin}/library`, { waitUntil: "networkidle", timeout: 45000 });
await page.waitForSelector(".series-card", { timeout: 20000 });
await page.waitForTimeout(400);

const failures = await page.evaluate(({ spec, icons, frames }) => {
  const out = [];
  for (const c of spec) {
    let el = document.querySelector(c.pseudo ? c.sel.replace(c.pseudo, "") : c.sel);
    // A badge that only appears for a followed series is not in this library's
    // data. Build one from the same classes so the rule is still checked.
    let probe = null;
    if (!el && c.synth) {
      const parent = document.querySelector(c.synth.parent);
      if (parent) {
        probe = document.createElement(c.synth.tag);
        probe.className = c.synth.className;
        probe.textContent = "probe";
        parent.appendChild(probe);
        el = probe;
      }
    }
    if (!el) { out.push({ sel: c.sel, node: c.node, bad: ["element not found"] }); continue; }
    const s = getComputedStyle(el, c.pseudo || null);
    const bad = [];
    for (const [k, want] of Object.entries(c.props || {})) {
      // Shorthands do not round-trip through getComputedStyle, so compare the
      // longhands the design actually pins.
      let got = s[k];
      if (k === "padding") got = want.includes(" ") ? `${s.paddingTop} ${s.paddingRight}` : s.paddingTop;
      if (k === "borderRadius" && !want.includes(" ")) got = s.borderTopLeftRadius;
      if (got !== want) bad.push(`${k}: want ${want}, got ${got}`);
    }
    if (c.box) {
      const r = el.getBoundingClientRect();
      // A grid or flex track can land a fraction off an integer design value.
      if (c.box.w && Math.abs(r.width - c.box.w) > 1) bad.push(`width: want ${c.box.w}, got ${r.width.toFixed(1)}`);
      if (c.box.h && Math.abs(r.height - c.box.h) > 1) bad.push(`height: want ${c.box.h}, got ${r.height.toFixed(1)}`);
    }
    if (probe) probe.remove();
    if (bad.length) out.push({ sel: c.sel, node: c.node, bad });
  }
  for (const i of icons) {
    const el = document.querySelector(i.where);
    if (!el) { out.push({ sel: i.where, node: i.node, bad: [`icon missing (${i.name})`] }); continue; }
    const r = el.getBoundingClientRect();
    if (Math.abs(r.width - i.size) > 1) {
      out.push({ sel: i.where, node: i.node, bad: [`${i.name}: want ${i.size}px, got ${r.width.toFixed(1)}px`] });
    }
  }
  for (const f of frames) {
    const el = document.querySelector(f.sel);
    if (!el) { out.push({ sel: f.sel, node: f.node, bad: ["frame not found"] }); continue; }
    const r = el.getBoundingClientRect();
    let p = { x: 0, y: 0 };
    if (f.within) {
      const pe = document.querySelector(f.within);
      if (!pe) { out.push({ sel: f.sel, node: f.node, bad: [`parent ${f.within} not found`] }); continue; }
      p = pe.getBoundingClientRect();
    }
    const bad = [];
    const near = (got, want) => Math.abs(got - want) > 1;
    if (f.w != null && near(r.width, f.w)) bad.push(`width: want ${f.w}, got ${r.width.toFixed(1)}`);
    if (f.h != null && near(r.height, f.h)) bad.push(`height: want ${f.h}, got ${r.height.toFixed(1)}`);
    if (p) {
      if (f.x != null && near(r.x - p.x, f.x)) bad.push(`x within ${f.within || "viewport"}: want ${f.x}, got ${(r.x - p.x).toFixed(1)}`);
      if (f.y != null && near(r.y - p.y, f.y)) bad.push(`y within ${f.within || "viewport"}: want ${f.y}, got ${(r.y - p.y).toFixed(1)}`);
    }
    if (bad.length) out.push({ sel: f.sel, node: f.node, bad });
  }
  return out;
}, { spec, icons, frames });

await browser.close();

const total = spec.length + icons.length + frames.length;
if (!failures.length) {
  console.log(`\nFigma spec: ${total} checks, all match.\n`);
  process.exit(0);
}
console.error(`\nFigma spec: ${failures.length} of ${total} checks do not match the file.\n`);
for (const f of failures) {
  console.error(`  ${f.sel}  (node ${f.node})`);
  for (const line of f.bad) console.error(`      ${line}`);
}
console.error("");
process.exit(1);
