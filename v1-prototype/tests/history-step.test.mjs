import { test } from "node:test";
import assert from "node:assert/strict";
import { historyStep } from "../src/history-step.js";

test("opening the reader or a run's drawer remembers where it came from", () => {
  assert.deepEqual(historyStep("/library", "/library?read=63", null), { kind: "push", state: { from: "/library" } });
  assert.deepEqual(historyStep("/library/all", "/library/all?series=61", null), { kind: "push", state: { from: "/library/all" } });
});

test("closing back to where it came from is Back, so its entry is gone", () => {
  assert.deepEqual(historyStep("/library?read=63", "/library", { from: "/library" }), { kind: "back" });
});

test("closing it anywhere else writes over its entry, never stacks on it", () => {
  // A comic read from a run's drawer: the drawer closes as the reader opens...
  assert.deepEqual(historyStep("/library/all?series=61", "/library/all?read=63", { from: "/library/all" }), { kind: "replace", state: null });
  // ...and the reader, closed, returns to the grid rather than the drawer.
  assert.deepEqual(historyStep("/library/all?read=63", "/library/all", null), { kind: "replace", state: null });
});

test("a change of tab or page stacks as it always has", () => {
  assert.deepEqual(historyStep("/library", "/settings", null), { kind: "push", state: null });
  assert.deepEqual(historyStep("/settings", "/library", { from: "/somewhere" }), { kind: "push", state: null });
  assert.deepEqual(historyStep("/library", "/library", null), { kind: "none" });
});

test("over a dialog's own entry the view is written over it", () => {
  assert.deepEqual(historyStep("/library?read=63", "/library/all", { dialog: 3, from: "/library" }), { kind: "replace", state: null });
});
