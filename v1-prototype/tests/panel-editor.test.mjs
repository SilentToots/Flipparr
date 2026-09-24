import test from "node:test";
import assert from "node:assert/strict";
import {
  PANEL_MIN_SIDE, normalise, handlePoint, hitTest, dragRect, drawnRect, isDrawn, newPanel, nudge,
  removeAt, tapOrder, applyOrder, toPayload, fromReading,
} from "../src/panel-editor.js";

const grid = [
  { x: 0.05, y: 0.05, w: 0.4, h: 0.4 }, { x: 0.55, y: 0.05, w: 0.4, h: 0.4 },
  { x: 0.05, y: 0.55, w: 0.9, h: 0.4 },
];
const reach = { x: 0.05, y: 0.04 };

test("a rectangle stays on the page and no thinner than a sliver", () => {
  assert.deepEqual(normalise({ x: -0.2, y: 0.9, w: 0.5, h: 0.5 }), { x: 0, y: 0.5, w: 0.5, h: 0.5 });
  assert.equal(normalise({ x: 0.5, y: 0.5, w: 0.001, h: 0.001 }).w, PANEL_MIN_SIDE);
  assert.deepEqual(normalise({ x: 0.123456, y: 0, w: 1, h: 1 }), { x: 0, y: 0, w: 1, h: 1 }, "a full-page panel has nowhere to be nudged");
});

test("a pointer finds a body, or a handle of the selected panel only", () => {
  assert.deepEqual(hitTest(grid, { x: 0.2, y: 0.2 }, reach), { index: 0, part: "body" });
  assert.equal(hitTest(grid, { x: 0.5, y: 0.5 }, reach), null, "the gutter is nobody's");
  // Just inside panel 1's corner, with nothing selected: its body, not a handle.
  assert.deepEqual(hitTest(grid, { x: 0.44, y: 0.06 }, reach), { index: 0, part: "body" });
  assert.deepEqual(hitTest(grid, { x: 0.44, y: 0.06 }, reach, 0), { index: 0, part: "ne" });
  assert.deepEqual(hitTest(grid, { x: 0.47, y: 0.03 }, reach, 0), { index: 0, part: "ne" }, "and a little outside it, within reach");
  assert.deepEqual(hitTest(grid, { x: 0.25, y: 0.45 }, reach, 0), { index: 0, part: "s" });
  const se = handlePoint(grid[2], "se");
  assert.ok(Math.abs(se.x - 0.95) < 1e-9 && Math.abs(se.y - 0.95) < 1e-9);
});

test("a small panel over a large one is picked up first, and the selected one before either", () => {
  const stacked = [{ x: 0, y: 0, w: 1, h: 1 }, { x: 0.4, y: 0.4, w: 0.2, h: 0.2 }];
  assert.equal(hitTest(stacked, { x: 0.5, y: 0.5 }, reach).index, 1);
  assert.equal(hitTest(stacked, { x: 0.5, y: 0.5 }, reach, 0).index, 0, "what you have hold of stays yours");
});

test("dragging moves a body whole and pulls one edge or two from a handle", () => {
  const start = grid[0];
  assert.deepEqual(dragRect(start, "body", 0.1, 0.1), { x: 0.15, y: 0.15, w: 0.4, h: 0.4 });
  assert.deepEqual(dragRect(start, "body", -1, -1), { x: 0, y: 0, w: 0.4, h: 0.4 }, "stopped at the page's edge");
  assert.deepEqual(dragRect(start, "e", 0.1, 0.5), { x: 0.05, y: 0.05, w: 0.5, h: 0.4 }, "a side ignores the other axis");
  assert.deepEqual(dragRect(start, "nw", -0.05, 0.05), { x: 0, y: 0.1, w: 0.45, h: 0.35 });
  const inverted = dragRect(start, "w", 0.9, 0);
  assert.equal(inverted.w, PANEL_MIN_SIDE, "an edge dragged past its opposite stops, never crosses");
  assert.equal(inverted.x + inverted.w, 0.45);
});

test("a drawn rectangle is the same whichever way it was dragged, and a slip is not one", () => {
  const a = drawnRect({ x: 0.1, y: 0.1 }, { x: 0.4, y: 0.3 });
  const b = drawnRect({ x: 0.4, y: 0.3 }, { x: 0.1, y: 0.1 });
  assert.deepEqual(a, b);
  assert.ok(isDrawn(a));
  assert.ok(!isDrawn(drawnRect({ x: 0.1, y: 0.1 }, { x: 0.11, y: 0.11 })));
});

test("a new panel sits in the middle, and beside one already there", () => {
  const first = newPanel([]);
  assert.deepEqual(first, { x: 0.35, y: 0.35, w: 0.3, h: 0.3 });
  const second = newPanel([first]);
  assert.notDeepEqual(second, first);
  assert.ok(second.x > first.x);
});

test("the keyboard nudges a panel, and resizes it with Shift", () => {
  const start = grid[0];
  assert.deepEqual(nudge(start, "ArrowRight", 0.01), { ...start, x: 0.06 });
  assert.deepEqual(nudge(start, "ArrowDown", 0.01, true), { ...start, h: 0.41 });
  assert.equal(nudge(start, "Enter", 0.01), start);
});

test("removing a panel keeps a sensible selection", () => {
  assert.deepEqual(removeAt(grid, 2).selected, 1);
  assert.deepEqual(removeAt(grid, 0).selected, 0);
  assert.deepEqual(removeAt([grid[0]], 0), { panels: [], selected: -1 });
});

test("ordering by tapping numbers each panel once and rearranges them when done", () => {
  let step = tapOrder([], 2, 3);
  assert.deepEqual(step, { sequence: [2], done: false });
  step = tapOrder(step.sequence, 2, 3);
  assert.deepEqual(step, { sequence: [2], done: false }, "a panel already numbered is ignored");
  step = tapOrder(step.sequence, 0, 3);
  step = tapOrder(step.sequence, 1, 3);
  assert.deepEqual(step, { sequence: [2, 0, 1], done: true });
  assert.deepEqual(applyOrder(grid, step.sequence), [grid[2], grid[0], grid[1]]);
});

test("the reader's reading becomes the editor's rectangles, and the fallback starts empty", () => {
  const reading = { segmented: true, panels: [{ id: "3-0", x: 0.1, y: 0.1, w: 0.3, h: 0.3 }] };
  assert.deepEqual(fromReading(reading), [{ x: 0.1, y: 0.1, w: 0.3, h: 0.3 }]);
  assert.deepEqual(fromReading({ segmented: false, panels: [] }), []);
  assert.deepEqual(fromReading(undefined), []);
  assert.deepEqual(toPayload([{ x: 0.123456, y: 0, w: 0.5, h: 0.5 }]), [{ x: 0.1235, y: 0, w: 0.5, h: 0.5 }]);
});
