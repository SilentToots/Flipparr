// Photograph every screen and record what colour every element resolved to.
//
//   node tests/visual/capture.mjs --out baseline
//   node tests/visual/capture.mjs --out current
//
// Screenshots catch anything that moves. The colour sweep says which selector
// and which value moved, which a picture cannot. Both are written per run.

import { chromium } from "playwright";
import { mkdir, writeFile, rm } from "node:fs/promises";
import path from "node:path";
import { states, backendStates } from "./states.mjs";
import { applyStubs } from "./stubs.mjs";

const APP = process.env.VISUAL_APP_ORIGIN || "http://localhost:4173";
const BACKEND = process.env.VISUAL_BACKEND_ORIGIN || "http://127.0.0.1:8795";
// Most of the type and spacing rules that differ between phone and desktop
// sit behind max-width: 640px, so a desktop-only capture cannot see them.
// Phone states are written as <name>@phone.
const VIEWPORTS = [
  { suffix: "", width: 1440, height: 900 },
  { suffix: "@phone", width: 375, height: 812 },
];

const out = (() => {
  const flag = process.argv.indexOf("--out");
  if (flag === -1 || !process.argv[flag + 1]) {
    throw new Error("usage: capture.mjs --out <baseline|current>");
  }
  return path.join(import.meta.dirname, "captures", process.argv[flag + 1]);
})();

// Everything about an element that a palette change can alter. Keyed by a
// structural path so the same element is comparable across runs without
// depending on text, which changes with the library.
const SWEEP = `(() => {
  const pathOf = (el) => {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && parts.length < 12; n = n.parentElement) {
      const tag = n.tagName.toLowerCase();
      const siblings = n.parentElement ? [...n.parentElement.children].filter((c) => c.tagName === n.tagName) : [n];
      parts.unshift(siblings.length > 1 ? tag + "[" + (siblings.indexOf(n) + 1) + "]" : tag);
    }
    return parts.join(">");
  };
  const PROPS = ["color","backgroundColor","borderTopColor","borderRightColor",
    "borderBottomColor","borderLeftColor","outlineColor","boxShadow","fill","stroke",
    "fontSize","fontWeight","lineHeight",
    "paddingTop","paddingRight","paddingBottom","paddingLeft",
    "marginTop","marginRight","marginBottom","marginLeft","rowGap","columnGap","borderTopLeftRadius"];
  const parse = (c) => {
    const m = String(c).match(/rgba?\\((\\d+)[,\\s]+(\\d+)[,\\s]+(\\d+)(?:[,\\s/]+([\\d.]+))?/);
    return m ? { r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4] } : null;
  };
  const lum = ({ r, g, b }) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (a, b) => {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05);
  };
  const backdrop = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0.95) return c;
    }
    return { r: 0, g: 0, b: 0, a: 1 };
  };

  const styles = {};
  const colours = new Set();
  const contrast = [];
  const borders = [];

  for (const el of document.querySelectorAll("*")) {
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") continue;
    // A descendant of a display:none element reports its own display, so the
    // check above misses whole hidden subtrees -- the mobile attention panel
    // put 54 phantom contrast failures in the count that way. getClientRects
    // is empty for anything that is not laid out, and non-empty for a fixed
    // element, which offsetParent would have wrongly excluded.
    if (!el.getClientRects().length) continue;
    const key = pathOf(el);
    const record = {};
    for (const p of PROPS) {
      const v = cs[p];
      if (!v || v === "none" || v === "rgba(0, 0, 0, 0)" || v === "0px" || v === "normal") continue;
      record[p] = v;
      if (!/color|shadow|fill|stroke/i.test(p)) continue;
      const c = parse(v);
      if (c && c.a > 0) colours.add(v);
    }
    if (Object.keys(record).length) styles[key] = record;

    // Own text only — an element whose text lives in a child would otherwise
    // be measured against the wrong colour.
    const ownText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    const fg = parse(cs.color);
    if (ownText && fg && fg.a > 0.5) {
      const size = parseFloat(cs.fontSize) || 16;
      const bold = (parseInt(cs.fontWeight, 10) || 400) >= 700;
      const large = size >= 24 || (size >= 18.66 && bold);
      const r = ratio(fg, backdrop(el));
      if (r < (large ? 3 : 4.5)) {
        contrast.push({ key, color: cs.color, size, bold, ratio: +r.toFixed(2), need: large ? 3 : 4.5 });
      }
    }

    for (const side of ["Top", "Right", "Bottom", "Left"]) {
      if (parseFloat(cs["border" + side + "Width"]) <= 0) continue;
      const bc = parse(cs["border" + side + "Color"]);
      if (!bc || bc.a < 0.5) continue;
      const r = ratio(bc, backdrop(el));
      // 1.3 flags --line itself (1.30 on --bg), which is the app's deliberate
      // hairline. 1.15 is the point a border stops being visible at all.
      if (r < 1.15) borders.push({ key, side, color: cs["border" + side + "Color"], ratio: +r.toFixed(2) });
    }
  }
  return { styles, colours: [...colours].sort(), contrast, borders, elements: document.querySelectorAll("*").length };
})()`;

async function capture(page, state, origin, dir, suffix) {
  const url = origin + state.path;
  await page.unrouteAll();
  await applyStubs(page, state.stub);
  // A screen whose content comes from the catalog renders its empty state
  // until the catalog arrives, which reads as real content to the guard.
  const catalog = state.waitForCatalog
    ? page.waitForResponse((r) => r.url().includes("/api/v1/catalog") && r.ok(), { timeout: 45000 })
    : null;
  await page.goto(url, { waitUntil: "networkidle", timeout: 45000 });
  if (catalog) await catalog;
  if (state.setup) await state.setup(page);
  await page.waitForTimeout(500);

  // Wait for the guard rather than asserting it: a setup step that clicks and
  // re-renders can land just after the settle, and that is a slow render, not
  // a dead backend. A dead backend renders empty forever and still fails here,
  // ten seconds later.
  const missing = [];
  for (const selector of (suffix && state.phoneRequire) || state.require) {
    try {
      await page.waitForSelector(selector, { timeout: 10000 });
    } catch {
      missing.push(selector);
    }
  }
  if (missing.length) {
    throw new Error(
      `${state.name}: required selectors absent (${missing.join(", ")}). ` +
      "The screen rendered but has no content — check the backend tunnel is up."
    );
  }
  await page.waitForTimeout(200);

  const name = state.name + suffix;
  await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: true });
  const data = await page.evaluate(SWEEP);
  return { name, url, ...data };
}

const browser = await chromium.launch();

await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });

const results = [];
const failures = [];
for (const { suffix, width, height } of VIEWPORTS) {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
  // Animations would make every screenshot differ from itself.
  await page.emulateMedia({ reducedMotion: "reduce" });
  const work = [
    ...states.map((s) => [s, APP]),
    ...backendStates.map((s) => [s, BACKEND]),
  ].filter(([state]) => !suffix || state.phone !== false);
  for (const [state, origin] of work) {
    const name = state.name + suffix;
    try {
      results.push(await capture(page, state, origin, out, suffix));
      process.stdout.write(`  ok   ${name}\n`);
    } catch (error) {
      failures.push(`${name}: ${error.message}`);
      process.stdout.write(`  FAIL ${name}\n`);
    }
  }
  await page.close();
}
await browser.close();

await writeFile(path.join(out, "sweep.json"), JSON.stringify(results, null, 2));

const colours = new Set(results.flatMap((r) => r.colours));
const contrast = results.flatMap((r) => r.contrast.map((c) => ({ state: r.name, ...c })));
const borders = results.flatMap((r) => r.borders.map((b) => ({ state: r.name, ...b })));
await writeFile(
  path.join(out, "summary.json"),
  JSON.stringify({
    states: results.length,
    distinctColours: colours.size,
    colours: [...colours].sort(),
    contrastFailures: contrast.length,
    contrast,
    borderFailures: borders.length,
    borders,
  }, null, 2)
);

console.log(`\n${results.length} states captured to ${path.basename(out)}/`);
console.log(`distinct colours: ${colours.size}`);
console.log(`contrast below AA: ${contrast.length}`);
console.log(`borders under 1.3:1: ${borders.length}`);
if (failures.length) {
  console.error(`\n${failures.length} state(s) failed:\n  ${failures.join("\n  ")}`);
  process.exit(1);
}
