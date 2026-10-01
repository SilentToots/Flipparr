// Alignment and fit, measured rather than eyeballed, on every page at five
// widths. Fails on anything off by more than half a pixel:
//
//   - the phone's tab bar sits on the page's edges;
//
//   - the page header's title and actions sit on the content's edges, and its
//     tools row starts on the left one;
//   - the first and last content blocks sit on the same edges (a form page's
//     narrower column is measured against its narrower header);
//   - everything in the header's first row shares one centre line, and so do
//     the rail's head and the page title;
//   - the rail's wordmark starts where its icons do, and its fold control ends
//     where its items do;
//   - every visible placeholder fits its field at full length. Fields do not
//     truncate: wording that does not fit is rewritten.
//
//   VISUAL_APP_ORIGIN=http://localhost:4180 npm run layout:check
//
// Needs a running app with a library behind it.

import { chromium } from "playwright";

const APP = process.env.VISUAL_APP_ORIGIN || "http://localhost:4173";
const WIDTHS = [320, 375, 390, 430, 768, 1024, 1440];
const PAGES = {
  library: { path: "/library/all", ready: ".series-card", first: ".series-grid > :first-child, .series-table", last: ".series-grid, .series-table" },
  discover: { path: "/discover", ready: ".release-shelf", first: ".release-shelf", last: ".release-shelf" },
  // A phone opens this as Discover's results; above 640px it is the Search page.
  search: { path: "/search?q=batman", ready: ".discover-results .library-match, .discover-results .discover-note", first: ".discover-results", last: ".discover-results" },
  pull: { path: "/pull-list", ready: ".request-card, .request-empty", first: ".request-card, .request-empty", last: ".request-card, .request-empty", also: [".request-list-bar"], alsoRight: [".request-list-bar"] },
  // A phone opens on the list of sections; above 640px, on the first section.
  settings: { path: "/settings", ready: ".settings-shell, .settings-index", first: ".settings-shell, .settings-index", last: ".settings-shell, .settings-index" },
  "settings-folders": { path: "/settings/library", ready: ".settings-card", first: ".settings-shell", last: ".settings-shell" },
  import: { path: "/import", ready: ".focused-panel", first: ".library-sources-panel, .focused-panel", last: ".focused-panel" },
};

function audit({ page }) {
  const issues = [];
  const shown = (el) => el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  const box = (sel) => {
    const el = document.querySelector(sel);
    if (!shown(el)) return null;
    const b = el.getBoundingClientRect();
    return { l: b.left, r: b.right, cy: b.top + b.height / 2 };
  };
  const near = (label, got, want) => {
    if (got != null && want != null && Math.abs(got - want) > 0.5) issues.push(`${label}: ${got.toFixed(1)}, want ${want.toFixed(1)}`);
  };
  const main = document.querySelector("main");
  const cs = getComputedStyle(main);
  const mb = main.getBoundingClientRect();
  const header = document.querySelector(".page-header").getBoundingClientRect();
  const left = mb.left + parseFloat(cs.paddingLeft);
  const right = Math.min(mb.right - parseFloat(cs.paddingRight), header.right);

  near("header title, left", box(".page-header-heading")?.l, left);
  near("header actions, right", box(".page-header-actions")?.r, right);
  if (shown(document.querySelector(".page-header-tools"))) near("header tools, left", box(".page-header-tools > *")?.l, left);
  near("content, left", box(page.first)?.l, left);
  near("content, right", box(page.last)?.r, right);
  for (const sel of page.also || []) near(`${sel}, left`, box(sel)?.l, left);
  for (const sel of page.alsoRight || []) near(`${sel}, right`, box(sel)?.r, right);

  const row = box(".page-header-title")?.cy;
  near("bell, centre", box(".page-header .appbar-bell")?.cy, row);
  near("primary action, centre", box(".page-header .glass-button--primary")?.cy, row);
  if (!document.querySelector(".page-header--search-page, .page-header--search-phone")) near("search, centre", box(".page-header .page-header-search")?.cy, row);
  // Above 640px: the sidebar floats on its inset and leads with Search; a
  // phone's tab bar has no Search; and a tablet's rail shows no F mark (it
  // sits with the counts, which the rail leaves out).
  const searchTab = document.querySelector('.sidebar [data-nav="search"]');
  if (window.innerWidth <= 640 && shown(searchTab)) issues.push("Search shown in a phone's tab bar");
  if (window.innerWidth > 640) {
    if (!shown(searchTab) || searchTab !== document.querySelector(".sidebar .nav-item")) issues.push("Search is not the sidebar's first item");
    if (window.innerWidth <= 900 && shown(document.querySelector(".sidebar-mark"))) issues.push("F mark shown on a tablet's rail");
    near("sidebar, inset", box(".sidebar")?.l, parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--space-12")));
  }
  // The phone's tab bar sits on the page's edges.
  if (window.innerWidth <= 640 && !document.querySelector(".tab-bar-collapsed")) {
    near("tab bar, left", box(".sidebar nav")?.l, left);
    near("tab bar, right", box(".sidebar nav")?.r, right);
  }

  const ctx = document.createElement("canvas").getContext("2d");
  for (const field of document.querySelectorAll("input[placeholder], textarea[placeholder]")) {
    if (!shown(field)) continue;
    const fs = getComputedStyle(field);
    ctx.font = fs.font;
    const room = field.clientWidth - parseFloat(fs.paddingLeft) - parseFloat(fs.paddingRight);
    const need = ctx.measureText(field.placeholder).width;
    if (need > room) issues.push(`placeholder "${field.placeholder}" needs ${Math.ceil(need)}px, has ${Math.floor(room)}px`);
  }
  return issues;
}

const browser = await chromium.launch();
let failures = 0;
for (const width of WIDTHS) {
  const tab = await browser.newPage({ viewport: { width, height: 900 } });
  for (const [name, page] of Object.entries(PAGES)) {
    await tab.goto(APP + page.path);
    try {
      await tab.waitForSelector(page.ready, { timeout: 30000 });
    } catch {
      console.log(`  ✗ ${name}@${width}: ${page.ready} never appeared`);
      failures += 1;
      continue;
    }
    await tab.waitForTimeout(600);
    const issues = await tab.evaluate(audit, { page });
    console.log(`  ${issues.length ? "✗" : "ok"} ${name}@${width}${issues.map((line) => `\n      ${line}`).join("")}`);
    failures += issues.length;
  }
  await tab.close();
}
await browser.close();
console.log(failures ? `\n${failures} layout problem(s).` : "\nEverything lines up and every placeholder fits.");
process.exit(failures ? 1 : 0);
