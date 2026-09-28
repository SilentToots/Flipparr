import test from "node:test";
import assert from "node:assert/strict";
import { sheetPullDecision, SHEET_PULL_SLOP } from "../src/sheet.js";

const decide = (over) => sheetPullDecision({ dx: 0, dy: 0, atTop: true, onGrabber: false, pullAnywhere: true, ...over });

test("nothing is decided on the first pixel", () => {
  assert.equal(decide({ dx: 1, dy: 2 }), "wait");
  assert.equal(decide({ dx: 0, dy: SHEET_PULL_SLOP }), "pull");
});

test("sideways is the page's swipe; up is its scroll", () => {
  assert.equal(decide({ dx: 12, dy: 8 }), "pass", "a carousel's swipe with some drift");
  assert.equal(decide({ dx: 1, dy: -10 }), "pass");
  assert.equal(decide({ dx: 1, dy: -10, onGrabber: true }), "pull", "the grabber pulls up, to a taller detent");
});

test("down inside anything with somewhere to scroll is a scroll", () => {
  assert.equal(decide({ dy: 10, atTop: false }), "pass");
  assert.equal(decide({ dy: 10, atTop: false, onGrabber: true }), "pull", "the grabber is never inside a list");
  assert.equal(decide({ dy: 10, atTop: true }), "pull");
});

test("a sheet holding a form pulls only by its grabber", () => {
  assert.equal(decide({ dy: 20, pullAnywhere: false }), "pass");
  assert.equal(decide({ dy: 20, pullAnywhere: false, onGrabber: true }), "pull");
});
