// Diff two captures.
//
//   node tests/visual/compare.mjs baseline current
//
// Drift is expected during the palette migration, so this does not fail on
// "something changed". It fails on the things a colour change must never
// cause: an element that moved or vanished, text that fell below AA, a
// border that stopped being visible.

import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { PNG } from "pngjs";
import pixelmatch from "pixelmatch";

// A commit that removes or adds a component reflows on purpose, and would
// otherwise fail every time and teach everyone to ignore the run. Declaring it
// downgrades reflow, appear and vanish to warnings; contrast and borders stay
// a hard gate, because no structural change licenses unreadable text.
const args = process.argv.slice(2);
const allowStructural = args.includes("--allow-structural");
const [beforeName = "baseline", afterName = "current"] = args.filter((a) => !a.startsWith("--"));
const here = import.meta.dirname;
const beforeDir = path.join(here, "captures", beforeName);
const afterDir = path.join(here, "captures", afterName);
const diffDir = path.join(here, "captures", "diff");

const readJson = async (p) => JSON.parse(await readFile(p, "utf8"));
const before = await readJson(path.join(beforeDir, "sweep.json"));
const after = await readJson(path.join(afterDir, "sweep.json"));
const beforeSummary = await readJson(path.join(beforeDir, "summary.json"));
const afterSummary = await readJson(path.join(afterDir, "summary.json"));

await mkdir(diffDir, { recursive: true });

// ---- pixels ---------------------------------------------------------------
// A changed pixel is information, not a failure. A changed *shape* is a
// failure: if the images are different sizes the layout reflowed.
const pixelReport = [];
for (const state of after) {
  const file = `${state.name}.png`;
  let a;
  let b;
  try {
    a = PNG.sync.read(await readFile(path.join(beforeDir, file)));
    b = PNG.sync.read(await readFile(path.join(afterDir, file)));
  } catch {
    pixelReport.push({ state: state.name, status: "missing" });
    continue;
  }
  if (a.width !== b.width || a.height !== b.height) {
    pixelReport.push({
      state: state.name, status: "reflowed",
      before: `${a.width}x${a.height}`, after: `${b.width}x${b.height}`,
    });
    continue;
  }
  const diff = new PNG({ width: a.width, height: a.height });
  const changed = pixelmatch(a.data, b.data, diff.data, a.width, a.height, { threshold: 0.05 });
  await writeFile(path.join(diffDir, file), PNG.sync.write(diff));
  pixelReport.push({
    state: state.name, status: "ok", changed,
    percent: +((changed / (a.width * a.height)) * 100).toFixed(2),
  });
}

// ---- which selector, which property ---------------------------------------
const byName = (list) => Object.fromEntries(list.map((s) => [s.name, s]));
const beforeStates = byName(before);
const styleChanges = [];
for (const state of after) {
  const prior = beforeStates[state.name];
  if (!prior) continue;
  for (const [key, props] of Object.entries(state.styles)) {
    const was = prior.styles[key];
    if (!was) { styleChanges.push({ state: state.name, key, property: "(new element)" }); continue; }
    for (const [property, value] of Object.entries(props)) {
      if (was[property] !== value) {
        styleChanges.push({ state: state.name, key, property, from: was[property], to: value });
      }
    }
  }
  for (const key of Object.keys(prior.styles)) {
    if (!state.styles[key]) styleChanges.push({ state: state.name, key, property: "(element gone)" });
  }
}

const vanished = styleChanges.filter((c) => c.property === "(element gone)");
const appeared = styleChanges.filter((c) => c.property === "(new element)");
const reflowed = pixelReport.filter((p) => p.status === "reflowed");
const contrastDelta = afterSummary.contrastFailures - beforeSummary.contrastFailures;
const borderDelta = afterSummary.borderFailures - beforeSummary.borderFailures;

// ---- report ---------------------------------------------------------------
const pct = (n) => `${n}`.padStart(6);
console.log(`\n${beforeName} -> ${afterName}\n`);
console.log("  state                    pixels changed");
for (const p of pixelReport) {
  const detail = p.status === "ok" ? `${pct(p.changed)}  (${p.percent}%)`
    : p.status === "reflowed" ? `REFLOWED ${p.before} -> ${p.after}` : "MISSING";
  console.log(`  ${p.state.padEnd(24)} ${detail}`);
}

const grouped = new Map();
for (const c of styleChanges) {
  if (!c.from) continue;
  const k = `${c.property}: ${c.from} -> ${c.to}`;
  grouped.set(k, (grouped.get(k) || 0) + 1);
}
console.log(`\n  ${styleChanges.length} computed-style changes, ${grouped.size} distinct:`);
for (const [change, count] of [...grouped].sort((a, b) => b[1] - a[1]).slice(0, 40)) {
  console.log(`    ${String(count).padStart(4)}x  ${change}`);
}
if (grouped.size > 40) console.log(`    … and ${grouped.size - 40} more`);

console.log(`\n  distinct colours   ${beforeSummary.distinctColours} -> ${afterSummary.distinctColours}`);
console.log(`  contrast failures  ${beforeSummary.contrastFailures} -> ${afterSummary.contrastFailures} (${contrastDelta >= 0 ? "+" : ""}${contrastDelta})`);
console.log(`  border failures    ${beforeSummary.borderFailures} -> ${afterSummary.borderFailures} (${borderDelta >= 0 ? "+" : ""}${borderDelta})`);

await writeFile(path.join(diffDir, "report.json"),
  JSON.stringify({ pixelReport, styleChanges, contrastDelta, borderDelta }, null, 2));

const problems = [];
const structural = [];
if (reflowed.length) structural.push(`${reflowed.length} state(s) reflowed — a colour change must not move the layout`);
if (vanished.length) structural.push(`${vanished.length} element(s) disappeared`);
if (appeared.length) structural.push(`${appeared.length} element(s) appeared`);
if (allowStructural) {
  if (structural.length) console.log(`\n  structural changes, declared with --allow-structural:\n    ${structural.join("\n    ")}`);
} else {
  problems.push(...structural);
}
if (contrastDelta > 0) problems.push(`${contrastDelta} new text(s) below AA contrast`);
if (borderDelta > 0) problems.push(`${borderDelta} new invisible border(s)`);

if (problems.length) {
  console.error(`\nFAILED:\n  ${problems.join("\n  ")}`);
  console.error(`\nDiff images in tests/visual/diff/`);
  process.exit(1);
}
console.log(`\nPassed. Diff images in tests/visual/diff/ — review the drift.`);
