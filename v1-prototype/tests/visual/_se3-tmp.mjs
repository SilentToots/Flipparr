import { webkit, devices } from "playwright";
const browser = await webkit.launch();
for (const [name, ctx] of [["SE 320", devices["iPhone SE"]], ["13 390", devices["iPhone 13"]], ["Max 430", devices["iPhone 14 Pro Max"]]]) {
  const page = await (await browser.newContext({ ...ctx, hasTouch: false })).newPage();
  await page.goto("http://localhost:4180/settings", { waitUntil: "networkidle" });
  await page.waitForSelector(".settings-index button"); await page.waitForTimeout(300);
  console.log(name, await page.$$eval(".settings-index button", (bs) => bs.map((b) => {
    const l = b.querySelector(".settings-index-label"), v = b.querySelector(".settings-index-value");
    return `${l.textContent.slice(0, 20).padEnd(20)} ${l.scrollWidth > l.clientWidth + 1 ? "clipped" : "ok     "} value-left ${Math.round(v.getBoundingClientRect().left)} ${v.scrollWidth > v.clientWidth + 1 ? "value-clipped" : ""} chevron-right ${Math.round(b.lastElementChild.getBoundingClientRect().right)}`;
  })).then((r) => "\n  " + r.join("\n  ")));
  if (name === "13 390") await page.screenshot({ path: `${process.env.S}/idx.png`, clip: { x: 0, y: 150, width: 390, height: 440 } });
}
await browser.close();
