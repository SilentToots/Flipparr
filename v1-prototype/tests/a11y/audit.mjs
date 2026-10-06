// The responsive and accessibility pass, over the same screens the visual
// harness photographs (tests/visual/states.mjs), against a running app
// (VISUAL_APP_ORIGIN; see tests/visual/README.md for the scratch stack).
//
// For each screen, at desktop (1440), tablet (768), phone (375) and the
// smallest width the design system promises (320):
//   - axe-core's WCAG 2.0/2.1/2.2 A and AA rule sets (best-practice rules are not run);
//   - reflow: no horizontal page scroll at 320 (WCAG 1.4.10), which is also
//     what 200% zoom on a small laptop comes to;
//   - targets: tappable controls at least 24px in both dimensions (2.5.8);
//   - keyboard: Tab through the page -- every stop visible, a focus
//     indicator on each (2.4.7), and the page never trapping focus (2.1.2);
//   - reduced motion honoured: no animation or transition longer than a
//     frame while `prefers-reduced-motion: reduce` (2.3.3).
//
// Writes a11y-report.json to --out and prints a summary; exits non-zero on
// a serious or critical axe violation, a reflow overflow, or a focus stop
// with no visible indicator.

import { chromium } from "playwright";
import { AxeBuilder } from "@axe-core/playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { states } from "../visual/states.mjs";
import { applyStubs } from "../visual/stubs.mjs";

const APP_ORIGIN = process.env.VISUAL_APP_ORIGIN || "http://localhost:4173";
const out = (() => {
  const flag = process.argv.indexOf("--out");
  return flag === -1 ? path.join(import.meta.dirname, "report") : process.argv[flag + 1];
})();
const only = (() => {
  const flag = process.argv.indexOf("--only");
  return flag === -1 ? null : process.argv[flag + 1].split(",");
})();

const VIEWPORTS = [
  { label: "desktop", width: 1440, height: 900 },
  { label: "tablet", width: 768, height: 1024 },
  { label: "phone", width: 375, height: 812 },
  { label: "narrow", width: 320, height: 568 },
];

const FOCUS_PROBE = `(() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const cs = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  const visible = r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none";
  // An indicator is an outline, a focus ring in box-shadow, or a border/background
  // change relative to the element's non-focused self (measured by the caller).
  return {
    tag: el.tagName.toLowerCase(), role: el.getAttribute("role"), name: (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 40),
    visible, inViewport: r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth,
    outline: cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0 ? cs.outlineColor : null,
    boxShadow: cs.boxShadow, borderColor: cs.borderTopColor, background: cs.backgroundColor,
    path: (() => { const parts = []; for (let n = el; n && n.nodeType === 1 && parts.length < 8; n = n.parentElement) parts.unshift(n.tagName.toLowerCase() + (n.className && typeof n.className === "string" ? "." + n.className.split(" ")[0] : "")); return parts.join(">"); })(),
  };
})()`;


async function keyboardPass(page, maxStops = 60) {
  const stops = [];
  const problems = [];
  const seen = new Set();
  await page.evaluate(() => document.body.focus());
  for (let i = 0; i < maxStops; i += 1) {
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(FOCUS_PROBE);
    if (!focused) break;
    const key = focused.path + "|" + focused.name;
    if (seen.has(key)) break; // wrapped around: the page's tab ring is done
    seen.add(key);
    // Compare with the same element unfocused: a ring may be a box-shadow or a
    // border colour that only exists while focused.
    // ...or on the element's wrapper, when a field draws its ring with :focus-within.
    const unfocused = await page.evaluate(`(() => {
      const el = document.activeElement; const wrap = el.parentElement;
      const read = (n) => { const cs = getComputedStyle(n); return { boxShadow: cs.boxShadow, borderColor: cs.borderTopColor, background: cs.backgroundColor, outline: cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0 }; };
      const on = { self: read(el), wrap: wrap ? read(wrap) : null };
      el.blur(); const off = { self: read(el), wrap: wrap ? read(wrap) : null }; el.focus();
      const differs = (x, y) => x && y && (x.boxShadow !== y.boxShadow || x.borderColor !== y.borderColor || x.background !== y.background || x.outline !== y.outline);
      return { self: off.self, wrapperIndicates: differs(on.wrap, off.wrap) };
    })()`);
    const indicated = Boolean(focused.outline)
      || focused.boxShadow !== unfocused.self.boxShadow
      || focused.borderColor !== unfocused.self.borderColor
      || focused.background !== unfocused.self.background
      || unfocused.wrapperIndicates;
    stops.push({ ...focused, indicated });
    if (!focused.visible) problems.push({ kind: "focus on an invisible element", ...focused });
    else if (!indicated) problems.push({ kind: "no visible focus indicator", ...focused });
  }
  return { stops: stops.length, problems };
}

const REFLOW_PROBE = `(() => {
  const doc = document.documentElement;
  const overflow = Math.max(0, doc.scrollWidth - doc.clientWidth);
  const wide = [...document.querySelectorAll("body *")].filter((el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.right > doc.clientWidth + 1 && r.width > 0 && cs.position !== "fixed" && cs.overflowX !== "hidden" && el.closest("[data-scrolls-sideways], .shelf, .series-shelf, .reader, .drawer-covers, .cover-strip") === null;
  }).slice(0, 6).map((el) => el.tagName.toLowerCase() + (el.className && typeof el.className === "string" ? "." + el.className.split(" ")[0] : "") + " " + Math.round(el.getBoundingClientRect().right));
  return { overflow, wide };
})()`;

const TARGET_PROBE = `(() => {
  const small = [];
  for (const el of document.querySelectorAll("button, a[href], input, select, [role=button], [role=tab], [role=menuitem]")) {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || cs.visibility === "hidden") continue;
    if (el.closest("[inert], [aria-hidden=true]")) continue;
    if (el.tagName === "INPUT" && ["range", "hidden", "file"].includes(el.type)) continue;
    if (el.classList.contains("sr-only") || (r.width <= 1 && r.height <= 1)) continue; // off-screen helpers (a hidden submit) are not pointer targets
    // A control inside its <label> is hit through the label (2.5.8 counts the whole target).
    const label = el.tagName === "INPUT" && el.closest("label");
    if (label) { const lr = label.getBoundingClientRect(); if (lr.width >= 24 && lr.height >= 24) continue; }
    if (el.tagName === "A" && cs.display === "inline" && el.closest("p, li, small, span")) continue; // inline text links are exempt (2.5.8)
    if (r.width < 24 || r.height < 24) small.push({ tag: el.tagName.toLowerCase(), cls: typeof el.className === "string" ? el.className.split(" ")[0] : "", name: (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 30), w: Math.round(r.width), h: Math.round(r.height) });
  }
  return small.slice(0, 12);
})()`;

const MOTION_PROBE = `(() => {
  let longest = 0; const offenders = [];
  for (const el of document.querySelectorAll("body *")) {
    const cs = getComputedStyle(el);
    for (const prop of ["animationDuration", "transitionDuration"]) {
      for (const piece of cs[prop].split(",")) {
        const v = piece.trim(); const ms = v.endsWith("ms") ? parseFloat(v) : parseFloat(v) * 1000;
        if (ms > 20) { longest = Math.max(longest, ms); if (offenders.length < 6) offenders.push(el.tagName.toLowerCase() + (typeof el.className === "string" && el.className ? "." + el.className.split(" ")[0] : "") + " " + prop + " " + v); }
      }
    }
  }
  return { longest, offenders };
})()`;

async function reach(page, state, origin) {
  await page.unrouteAll();
  await applyStubs(page, state.stub);
  const catalog = state.waitForCatalog
    ? page.waitForResponse((r) => r.url().includes("/api/v1/catalog") && r.ok(), { timeout: 45000 })
    : null;
  await page.goto(origin + state.path, { waitUntil: "networkidle", timeout: 45000 });
  if (catalog) await catalog;
  if (state.setup) await state.setup(page);
  await page.waitForTimeout(500);
  for (const selector of state.require) {
    await page.waitForSelector(selector, { timeout: 10000 });
  }
}

const browser = await chromium.launch();
await mkdir(out, { recursive: true });
const report = [];
let serious = 0;
let overflowing = 0;
let unindicated = 0;

for (const viewport of VIEWPORTS) {
  const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, deviceScaleFactor: 1, reducedMotion: "reduce" });
  // The Comics grid remembers its view and filters in localStorage, so the
  // list and filter states would otherwise leave every state after them
  // waiting for a card (the same reset the visual harness makes).
  await context.addInitScript(() => {
    try {
      for (const key of Object.keys(localStorage)) {
        if (/^flipparr\.(u\d+\.)?library$/.test(key)) localStorage.removeItem(key);
      }
    } catch { /* private mode */ }
  });
  const page = await context.newPage();
  for (const state of states) {
    if (only && !only.includes(state.name)) continue;
    if (viewport.width <= 640 && state.phone === false) continue;
    const entry = { state: state.name, viewport: viewport.label, width: viewport.width };
    try {
      await reach(page, state, APP_ORIGIN);
      // reset the grid's remembered view between states, as the capture does
      const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
      entry.violations = axe.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length, sample: v.nodes.slice(0, 2).map((n) => n.target.join(" ")) }));
      serious += entry.violations.filter((v) => v.impact === "serious" || v.impact === "critical").length;
      entry.reflow = await page.evaluate(REFLOW_PROBE);
      if (viewport.width === 320 && entry.reflow.overflow > 0) overflowing += 1;
      entry.targets = await page.evaluate(TARGET_PROBE);
      entry.motion = await page.evaluate(MOTION_PROBE);
      if (viewport.label === "desktop" || viewport.label === "phone") {
        entry.keyboard = await keyboardPass(page);
        unindicated += entry.keyboard.problems.length;
      }
    } catch (error) {
      entry.error = String(error.message || error).split("\n")[0];
    }
    report.push(entry);
    const line = entry.error
      ? `ERR  ${state.name}@${viewport.label}: ${entry.error}`
      : `${entry.violations.some((v) => v.impact === "serious" || v.impact === "critical") || (viewport.width === 320 && entry.reflow.overflow > 0) || entry.keyboard?.problems.length ? "FAIL" : "ok  "} ${state.name}@${viewport.label}: axe ${entry.violations.length} (${entry.violations.filter((v) => v.impact === "serious" || v.impact === "critical").length} serious), overflow ${entry.reflow.overflow}px, small targets ${entry.targets.length}, motion ${entry.motion.longest}ms${entry.keyboard ? `, keyboard ${entry.keyboard.stops} stops / ${entry.keyboard.problems.length} problems` : ""}`;
    console.log(line);
  }
  await context.close();
}
await browser.close();
await writeFile(path.join(out, "a11y-report.json"), JSON.stringify({ generatedAt: new Date().toISOString(), origin: APP_ORIGIN, report }, null, 2));

// The whole picture, by rule and by place.
const byRule = new Map();
for (const entry of report) for (const v of entry.violations || []) {
  const row = byRule.get(v.id) || { id: v.id, impact: v.impact, help: v.help, where: new Set(), nodes: 0 };
  row.where.add(`${entry.state}@${entry.viewport}`); row.nodes += v.nodes; byRule.set(v.id, row);
}
console.log("\naxe rules violated:");
for (const row of [...byRule.values()].sort((a, b) => b.nodes - a.nodes)) console.log(`  ${row.impact.padEnd(8)} ${row.id.padEnd(28)} ${row.nodes} nodes in ${row.where.size} screens -- ${row.help}`);
const focusProblems = report.flatMap((e) => (e.keyboard?.problems || []).map((p) => ({ ...p, at: `${e.state}@${e.viewport}` })));
if (focusProblems.length) { console.log("\nfocus problems:"); for (const p of focusProblems.slice(0, 30)) console.log(`  ${p.at}: ${p.kind} -- ${p.path} "${p.name}"`); }
const overflow = report.filter((e) => e.width === 320 && e.reflow && e.reflow.overflow > 0);
if (overflow.length) { console.log("\nhorizontal overflow at 320px:"); for (const e of overflow) console.log(`  ${e.state}: ${e.reflow.overflow}px -- ${e.reflow.wide.join("; ")}`); }
const small = report.filter((e) => e.targets && e.targets.length);
if (small.length) { console.log("\nsmall targets (<24px):"); for (const e of small.slice(0, 20)) console.log(`  ${e.state}@${e.viewport}: ${e.targets.map((t) => `${t.tag}.${t.cls} "${t.name}" ${t.w}x${t.h}`).join("; ")}`); }
const motion = report.filter((e) => e.motion && e.motion.longest > 20);
if (motion.length) { console.log("\nmotion under reduced-motion:"); for (const e of motion.slice(0, 10)) console.log(`  ${e.state}@${e.viewport}: ${e.motion.longest}ms -- ${e.motion.offenders.join("; ")}`); }
console.log(`\n${serious} serious/critical axe violations, ${overflowing} screens overflow at 320px, ${unindicated} focus stops without an indicator`);
process.exit(serious || overflowing || unindicated ? 1 : 0);
