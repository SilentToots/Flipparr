import test from "node:test";
import assert from "node:assert/strict";
import { coverToTry } from "../src/cover-pick.js";

test("a cover that arrives after another failed is still tried", () => {
  const failed = new Set(["https://example.test/before.jpg"]);
  // The run's cover failed once; the provider then added the image under a new address.
  assert.equal(coverToTry(["https://example.test/before.jpg"], failed), null);
  assert.equal(coverToTry(["https://example.test/after.jpg"], failed), "https://example.test/after.jpg");
});

test("failed candidates are skipped in order, and empty ones ignored", () => {
  const failed = new Set(["a"]);
  assert.equal(coverToTry(["", "a", "b", "c"], failed), "b");
  assert.equal(coverToTry([], failed), null);
  assert.equal(coverToTry(undefined, failed), null);
});
