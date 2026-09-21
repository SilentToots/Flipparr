// The app's icons, rendered from the one mark.
//
// One source -- the monochrome F bubble in public/brand -- and every raster a
// device asks for comes out of it here, so they cannot drift from each other
// or from the mark. Black and white only: the mark is white on a black tile,
// which reads on a light tab strip, a dark one, and a home screen of any
// wallpaper. Run with `npm run brand:icons`; the outputs are committed, so a
// build never needs a browser.
//
// What each one is for:
//   icon-180.png           iOS home screen (apple-touch-icon). Opaque; iOS
//                          rounds the corners itself, so the tile is square.
//   icon-192/512.png       Android and desktop installs, purpose "any": a
//                          rounded tile on transparent, for launchers that
//                          do not mask.
//   icon-maskable-512.png  purpose "maskable": full-bleed black with the mark
//                          inside the safe zone, for launchers that cut their
//                          own shape (circle, squircle, leaf).
//   favicon-32/16.png      the tab and bookmark fallback for browsers that
//                          do not take an SVG favicon (Safari), as a small
//                          rounded tile so it reads on either theme.
//   favicon.ico            the same 32px tile in the container old browsers
//                          and some crawlers still ask for at /favicon.ico.

import { chromium } from "playwright";
import { readFile, writeFile } from "node:fs/promises";

const here = new URL(".", import.meta.url);
const brand = new URL("../public/brand/", here);
const mark = await readFile(new URL("flipparr-mark-monochrome.svg", brand), "utf8");

// The tile: a black square, rounded by `radius` (a fraction of the size, 0
// for none), with the white mark scaled to `inset` of the tile's width.
function tile(size, { radius, inset }) {
  const r = Math.round(size * radius);
  const w = Math.round(size * inset);
  return `<!doctype html><html><head><style>
    html, body { margin: 0; background: transparent; }
    .tile { width: ${size}px; height: ${size}px; background: #000; border-radius: ${r}px;
            display: grid; place-items: center; color: #fff; }
    svg { width: ${w}px; height: ${w}px; display: block; }
  </style></head><body><div class="tile">${mark}</div></body></html>`;
}

const ICONS = [
  ["icon-180.png", 180, { radius: 0, inset: 0.72 }],
  ["icon-192.png", 192, { radius: 0.22, inset: 0.72 }],
  ["icon-512.png", 512, { radius: 0.22, inset: 0.72 }],
  // The maskable safe zone is the central 80% circle; 0.58 of the width
  // keeps every corner of the mark inside it.
  ["icon-maskable-512.png", 512, { radius: 0, inset: 0.58 }],
  ["favicon-32.png", 32, { radius: 0.22, inset: 0.8 }],
  ["favicon-16.png", 16, { radius: 0.22, inset: 0.84 }],
];

const browser = await chromium.launch();
const page = await browser.newPage({ deviceScaleFactor: 1 });
for (const [name, size, shape] of ICONS) {
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(tile(size, shape));
  const png = await page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
  await writeFile(new URL(name, brand), png);
  process.stdout.write(`  ${name}\n`);
}
await browser.close();

// favicon.ico: one 32px PNG in an ICO container. The header is six bytes, the
// single directory entry sixteen, and the image data follows at offset 22.
const png32 = await readFile(new URL("favicon-32.png", brand));
const header = Buffer.alloc(6);
header.writeUInt16LE(0, 0); // reserved
header.writeUInt16LE(1, 2); // type: icon
header.writeUInt16LE(1, 4); // one image
const entry = Buffer.alloc(16);
entry.writeUInt8(32, 0); // width
entry.writeUInt8(32, 1); // height
entry.writeUInt8(0, 2); // palette
entry.writeUInt8(0, 3); // reserved
entry.writeUInt16LE(1, 4); // planes
entry.writeUInt16LE(32, 6); // bits per pixel
entry.writeUInt32LE(png32.length, 8);
entry.writeUInt32LE(22, 12); // offset
await writeFile(new URL("../public/favicon.ico", here), Buffer.concat([header, entry, png32]));
process.stdout.write("  favicon.ico\n");
