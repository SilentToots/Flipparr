// Assert the running app against the Figma file.
//
//   node tests/visual/figma-check.mjs
//
// Unlike compare.mjs, which tolerates drift because a palette migration is
// meant to move things, this fails on any difference: the values in
// figma-spec.mjs are a specification, and there is no legitimate drift from
// one. It exists because "looks close in a screenshot" shipped twice.

import { chromium } from "playwright";
import { spec, icons, iconModules } from "./figma-spec.mjs";

const origin = process.env.FLIPPARR_UI_ORIGIN || "http://localhost:4173";
const width = 1440;
const height = 951;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width, height } });
await page.goto(`${origin}/library`, { waitUntil: "networkidle", timeout: 45000 });
await page.waitForSelector(".series-card", { timeout: 20000 });
await page.waitForTimeout(400);

const failures = await page.evaluate(({ spec, icons }) => {
  const out = [];
  for (const c of spec) {
    let el = document.querySelector(c.sel);
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
    const s = getComputedStyle(el);
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
  return out;
}, { spec, icons });

await browser.close();

// Icons that no data path renders are checked at the module level instead --
// that the named export from the named package is what the app imports.
const iconSource = await (await import("node:fs/promises")).readFile("src/design-icons.jsx", "utf8");
for (const m of iconModules) {
  if (!iconSource.includes(m.export) || !iconSource.includes(m.from)) {
    failures.push({ sel: `design-icons.jsx`, node: m.node, bad: [`${m.name}: expected ${m.export} from ${m.from}`] });
  }
}

const total = spec.length + icons.length + iconModules.length;
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
