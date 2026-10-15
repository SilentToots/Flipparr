// Every image a shared screen draws goes through the host (`useImageSrc`):
// Flipparr View's web view cannot send the token with a plain image
// address, so a raw `src={url}` shows a broken image there and nowhere else
// -- the panel editor's page did, found on the owner's iPhone (2026-10-11).
// App.jsx is the web's alone and may use addresses as they are.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

const SRC = path.join(import.meta.dirname, "..", "src");
const WEB_ONLY = new Set(["App.jsx"]);

function jsxFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return jsxFiles(full);
    return name.endsWith(".jsx") ? [full] : [];
  });
}

test("shared screens load every image through the host", () => {
  const bad = [];
  for (const file of jsxFiles(SRC)) {
    const name = path.relative(SRC, file);
    if (WEB_ONLY.has(name)) continue;
    readFileSync(file, "utf8").split("\n").forEach((line, index) => {
      for (const match of line.matchAll(/<img\b(?:=>|[^>])*?\bsrc=\{([^}]*)\}/g)) {
        if (!/^(src|imageSrc)\(/.test(match[1].trim())) bad.push(`${name}:${index + 1}  src={${match[1]}}`);
      }
      // A preload is an image too: `new Image().src = url` bypassed the host
      // (the panel editor's next page, the review of 2026-10-14).
      for (const match of line.matchAll(/\.src\s*=\s*([^;]+);/g)) {
        if (/new Image\(\)/.test(line) || /\bahead\b|\bpreload\b/.test(line)) {
          if (!/^(src|imageSrc)\(/.test(match[1].trim())) bad.push(`${name}:${index + 1}  .src = ${match[1].trim()}`);
        }
      }
    });
  }
  assert.deepEqual(bad, []);
});
