// Status marks keep one meaning each, on the web and in Flipparr View
// (docs/STATUS_MARKS_PLAN.md; DESIGN_SYSTEM.md "A cover's corners"). A check
// on a cover meant read on the web and downloaded in the app until
// 2026-10-11; these hold the parts that can drift back.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { declarations } from "./css-declarations.mjs";

const SRC = path.join(import.meta.dirname, "..", "src");
const read = (name) => readFileSync(path.join(SRC, name), "utf8");
const phosphorImports = (source) => (source.match(/import \{([^}]*)\} from "@phosphor-icons\/react"/) || [, ""])[1]
  .split(",").map((name) => name.trim()).filter(Boolean);

test("a cover's download mark is never a check: the check means read", () => {
  const icons = phosphorImports(read("components/CoverStatus.jsx"));
  assert.ok(!icons.includes("Check") && !icons.includes("CheckCircle"), `CoverStatus imports ${icons.join(", ")}`);
});

test("each corner of a cover has one job", () => {
  const css = declarations(read("styles.css")).filter((d) => !d.media.length);
  const sides = (selector) => new Set(css.filter((d) => d.selector === selector && ["top", "right", "bottom", "left"].includes(d.property)).map((d) => d.property));
  assert.deepEqual([...sides(".read-state.tag")].sort(), ["right", "top"], "reading: top right");
  assert.deepEqual([...sides(".read-state.sash")].sort(), ["right", "top"], "reading: top right");
  assert.deepEqual([...sides(".cover-status.corner")].sort(), ["bottom", "right"], "the app's download mark: bottom right");
  assert.deepEqual([...sides(".collection-card-mark")].sort(), ["bottom", "left"], "a collection's mark: bottom left");
});

test("reading's tones say reading, and nothing else", () => {
  const files = ["App.jsx", "reader-app/ReaderApp.jsx", "drawers/frame.jsx", "library/cards.jsx", "reader/ReaderView.jsx"];
  for (const file of files) {
    assert.ok(!/reading-(read|progress)/.test(read(file)), `${file} borrows a reading tone`);
  }
  assert.ok(/reading-read/.test(read("drawers/inventory.jsx")), "an issue row's reading words keep them");
});
