// Coming back to Flipparr fetches the library again, at most once a minute
// (src/refresh-on-return.js; the owner, 2026-10-11).
import { test } from "node:test";
import assert from "node:assert/strict";
import { REFRESH_ON_RETURN_MS, dueForRefresh, refreshesOnReturn } from "../src/refresh-on-return.js";

test("back in the foreground, the library is fetched again only when a minute has passed", () => {
  const at = Date.parse("2026-10-11T08:00:00Z");
  assert.equal(dueForRefresh(0, at), true, "never fetched");
  assert.equal(dueForRefresh(at, at + 5000), false, "flicking between apps");
  assert.equal(dueForRefresh(at, at + REFRESH_ON_RETURN_MS), true);
  assert.equal(dueForRefresh(at, at + 8 * 60 * 60 * 1000), true, "overnight");
});

test("only a page shown again, and due, refreshes; being hidden never does", () => {
  const at = Date.parse("2026-10-11T08:00:00Z");
  assert.equal(refreshesOnReturn("visible", at, at + REFRESH_ON_RETURN_MS), true);
  assert.equal(refreshesOnReturn("hidden", at, at + REFRESH_ON_RETURN_MS), false, "going to the background is not a return");
  assert.equal(refreshesOnReturn("visible", at, at + 1000), false, "back within the minute");
  assert.equal(refreshesOnReturn("visible", 0, at), true, "never fetched");
});
