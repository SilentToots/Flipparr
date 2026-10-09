import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

// The web app and Flipparr View are built from the same screens
// (docs/OFFLINE_READING_PLAN.md, "Phase 3b"). What they share lives outside
// App.jsx and never reaches back into it: App.jsx is the web app's own shell,
// and Flipparr View does not load it.
const SRC = path.join(import.meta.dirname, "..", "src");
const SHARED = ["api.js", "host.js", "viewer.js", "dialogs.jsx", "editions.js", "components", "drawers", "frame", "library", "reader", "reader-app"];

function files(entry) {
  const full = path.join(SRC, entry);
  if (!statSync(full).isDirectory()) return [full];
  return readdirSync(full).flatMap((name) => files(path.join(entry, name)));
}

test("shared modules import nothing from App.jsx", () => {
  const offenders = SHARED.flatMap(files)
    .filter((file) => /\.(js|jsx)$/.test(file))
    .filter((file) => /from\s+["'][^"']*\/App(\.jsx)?["']/.test(readFileSync(file, "utf8")))
    .map((file) => path.relative(SRC, file));
  assert.deepEqual(offenders, []);
});
