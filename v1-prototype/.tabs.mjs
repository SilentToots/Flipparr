import { chromium } from "playwright";
const SP = "/private/tmp/claude-501/-Users-blee-Documents-ChatGPT-Comic-Arr-Project/5cde0185-7969-49e5-bc9e-5122ca6da6e6/scratchpad";
const b = await chromium.launch();
const measure = (sel) => (p) => p.evaluate((sel) => [...document.querySelectorAll(sel)].filter((e) => e.getClientRects().length).slice(0, 3).map((e) => {
  const s = getComputedStyle(e), r = e.getBoundingClientRect();
  return `${e.textContent.trim().slice(0, 14).padEnd(14)} ${s.fontSize}/${s.fontWeight} h${r.height.toFixed(0)} ${e.classList.contains("active") ? "active " + s.color : s.color}`;
}), sel);
for (const [vp, w, h] of [["desktop", 1440, 900], ["phone", 390, 844]]) {
  const p = await b.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 2 });
  await p.goto("http://localhost:4180/pull-list", { waitUntil: "networkidle" }); await p.waitForSelector(".request-tabs");
  console.log(vp, "pull-list", await measure(".request-tabs button")(p));
  await p.locator(".request-tabs").screenshot({ path: `${SP}/tabs-pull-${vp}.png` });
  if (vp === "desktop") {
    await p.goto("http://localhost:4180/settings", { waitUntil: "networkidle" }); await p.waitForSelector(".settings-tabs");
    console.log(vp, "settings ", await measure(".settings-tabs button")(p));
    await p.locator(".settings-tabs").screenshot({ path: `${SP}/tabs-settings-${vp}.png` });
  }
  await p.goto("http://localhost:4180/library", { waitUntil: "networkidle" }); await p.locator(".series-card").first().click();
  await p.waitForSelector(".comic-drawer-tabs"); await p.waitForTimeout(700);
  console.log(vp, "drawer   ", await measure(".comic-drawer-tabs button")(p));
  await p.locator(".comic-drawer-tabs").screenshot({ path: `${SP}/tabs-drawer-${vp}.png` });
  await p.close();
}
await b.close();
