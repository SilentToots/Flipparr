import test from "node:test";
import assert from "node:assert/strict";
import { nextTabBarState, canTuck } from "../src/tab-bar.js";

// Feed a run of scroll positions through the decision, as the scroll listener
// does, and report the bar's state after each.
function replay(positions, { maxScroll = 3000, start = { collapsed: false, lastY: 0 } } = {}) {
  let state = start;
  return positions.map((scrollY) => {
    state = nextTabBarState({ scrollY, maxScroll, lastY: state.lastY, collapsed: state.collapsed });
    return state.collapsed;
  });
}

test("scrolling down tucks the bar and scrolling back up brings it back", () => {
  assert.deepEqual(replay([200, 400, 600, 500, 300]), [true, true, true, false, false]);
});

test("the bar is always open near the top of the page", () => {
  assert.deepEqual(replay([300, 40, -20, 0]), [true, false, false, false]);
});

test("small wobbles do not flip the bar", () => {
  assert.deepEqual(replay([400, 404, 399, 402]), [true, true, true, true]);
});

test("iOS rubber-banding past the bottom does not open and close the bar", () => {
  // Reach the bottom, overshoot as iOS does, then settle back -- twice.
  const bounce = [2800, 3000, 3060, 3110, 3040, 3000, 3080, 3000, 2996];
  assert.deepEqual(replay(bounce), bounce.map(() => true));
});

test("scrolling back up from the bottom still opens the bar once clear of the end", () => {
  assert.deepEqual(replay([2800, 3000, 2980, 2900, 2800]), [true, true, true, false, false]);
});

test("a page too short to scroll keeps the bar open", () => {
  assert.deepEqual(replay([0, 30, 60], { maxScroll: 40 }), [false, false, false]);
});

test("the bar tucks only into a tab the phone shows", () => {
  const items = [{ id: "search", desktopOnly: true }, { id: "library" }, { id: "settings" }];
  assert.equal(canTuck("library", items), true);
  assert.equal(canTuck("profile", items), false, "your profile has no tab: an empty pill (2026-09-27)");
  assert.equal(canTuck("search", items), false, "Search's tab is the desktop's");
  assert.equal(canTuck("library", null), false);
});
