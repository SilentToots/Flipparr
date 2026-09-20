import test from "node:test";
import assert from "node:assert/strict";
import {
  READING_DIRECTIONS, readingDirection, actionForKey, tapAction, pageForAction,
  pageWindow, isSpread, SPREAD_RATIO, clampZoom, clampPan, pageFromInput, pagesLeft, pageFilter,
} from "../src/reader.js";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

test("manga reads right to left, and a run can say otherwise", () => {
  assert.equal(readingDirection("manga"), READING_DIRECTIONS.rtl);
  assert.equal(readingDirection("comic"), READING_DIRECTIONS.ltr);
  assert.equal(readingDirection(undefined), READING_DIRECTIONS.ltr, "unknown reads as a comic");
  assert.equal(readingDirection("manga", "ltr"), READING_DIRECTIONS.ltr, "an English edition may be flipped");
  assert.equal(readingDirection("comic", "rtl"), READING_DIRECTIONS.rtl);
  assert.equal(readingDirection("manga", "sideways"), READING_DIRECTIONS.rtl, "nonsense is ignored, not obeyed");
});

test("the arrows follow the screen and the page keys follow the story", () => {
  // The decision this file exists for. Changing it means changing this test.
  assert.equal(actionForKey("ArrowRight", "ltr"), "next");
  assert.equal(actionForKey("ArrowLeft", "ltr"), "previous");
  assert.equal(actionForKey("ArrowRight", "rtl"), "previous", "right is backwards in manga");
  assert.equal(actionForKey("ArrowLeft", "rtl"), "next");
  for (const direction of ["ltr", "rtl"]) {
    assert.equal(actionForKey(" ", direction), "next", "space always goes on with the story");
    assert.equal(actionForKey("PageDown", direction), "next");
    assert.equal(actionForKey("PageUp", direction), "previous");
    assert.equal(actionForKey("Home", direction), "first");
    assert.equal(actionForKey("End", direction), "last");
    assert.equal(actionForKey("q", direction), null);
  }
});

test("the outer thirds turn pages, mirrored for manga, and the middle is the chrome", () => {
  assert.equal(tapAction(10, 400, "ltr"), "previous");
  assert.equal(tapAction(390, 400, "ltr"), "next");
  assert.equal(tapAction(10, 400, "rtl"), "next");
  assert.equal(tapAction(390, 400, "rtl"), "previous");
  assert.equal(tapAction(200, 400, "ltr"), "chrome");
  assert.equal(tapAction(200, 400, "rtl"), "chrome");
  assert.equal(tapAction(10, 0, "ltr"), "chrome", "a width of nothing cannot turn a page");
});

test("paging stops at both ends of the comic", () => {
  assert.equal(pageForAction(0, 24, "previous"), 0);
  assert.equal(pageForAction(23, 24, "next"), 23);
  assert.equal(pageForAction(5, 24, "next"), 6);
  assert.equal(pageForAction(5, 24, "previous"), 4);
  assert.equal(pageForAction(5, 24, "first"), 0);
  assert.equal(pageForAction(5, 24, "last"), 23);
  assert.equal(pageForAction(5, 24, "nothing"), 5);
  assert.equal(pageForAction(3, 0, "next"), 0, "a comic with no pages goes nowhere");
});

test("the preload window is small, and clipped at the covers", () => {
  assert.deepEqual(pageWindow(5, 24), [4, 5, 6, 7]);
  assert.deepEqual(pageWindow(0, 24), [0, 1, 2], "nothing before the cover");
  assert.deepEqual(pageWindow(23, 24), [22, 23], "nothing past the last page");
  assert.deepEqual(pageWindow(0, 1), [0]);
  assert.deepEqual(pageWindow(0, 0), []);
});

test("a spread is wider than it is tall, by the same rule the backdrop picker uses", () => {
  assert.ok(isSpread(3000, 2000));
  assert.ok(!isSpread(2000, 3000));
  assert.ok(!isSpread(1000, 1000), "square is not a spread");
  assert.ok(!isSpread(1000, 0), "a page that reported no height is not a spread");

  // One number, two languages: the backend uses it to pick a run's header art.
  const here = path.dirname(fileURLToPath(import.meta.url));
  const backend = readFileSync(path.join(here, "..", "..", "app.py"), "utf8");
  const ratio = backend.match(/return bool\(height\) and width > height \* ([\d.]+)/);
  assert.ok(ratio, "found _page_is_spread in app.py");
  assert.equal(Number(ratio[1]), SPREAD_RATIO, "the two definitions of a spread agree");
});

test("zoom stays between the whole page and the letters, and pan cannot lose the page", () => {
  assert.equal(clampZoom(0.2), 1);
  assert.equal(clampZoom(9), 4);
  assert.equal(clampZoom(2.5), 2.5);
  assert.equal(clampZoom("nonsense"), 1);

  const viewport = { width: 400, height: 800 };
  assert.deepEqual(clampPan({ x: 500, y: -900 }, 2, viewport), { x: 200, y: -400 }, "each edge stops at its own limit");
  assert.deepEqual(clampPan({ x: 10, y: 10 }, 1, viewport), { x: 0, y: 0 }, "nothing to pan at 1x");
  assert.deepEqual(clampPan({ x: 50, y: 50 }, 2, viewport), { x: 50, y: 50 });
});

test("a page number someone typed is theirs, one-based, and refused when it is not a page", () => {
  assert.equal(pageFromInput("1", 24), 0);
  assert.equal(pageFromInput("24", 24), 23);
  assert.equal(pageFromInput(" 7 ", 24), 6);
  assert.equal(pageFromInput("0", 24), null);
  assert.equal(pageFromInput("25", 24), null, "clamping to the last page is not what was asked");
  assert.equal(pageFromInput("-3", 24), null);
  assert.equal(pageFromInput("twelve", 24), null);
  assert.equal(pageFromInput("", 24), null);
});

test("what is left is counted in pages, and the last page says so", () => {
  assert.equal(pagesLeft(0, 24), "23 pages left");
  assert.equal(pagesLeft(22, 24), "1 page left");
  assert.equal(pagesLeft(23, 24), "Last page");
  assert.equal(pagesLeft(30, 24), "Last page", "past the end is still the end");
});

test("night reading dims and warms the page, and does nothing when it is off", () => {
  assert.equal(pageFilter(), "none");
  assert.equal(pageFilter({ dim: 1, warm: 0 }), "none");
  assert.match(pageFilter({ dim: 0.6 }), /brightness\(0\.60\)/);
  assert.match(pageFilter({ warm: 1 }), /sepia\(0\.55\)/);
  assert.match(pageFilter({ dim: 0.5, warm: 0.5 }), /brightness\(0\.50\) sepia\(0\.28\)/);
  assert.match(pageFilter({ dim: 0.01 }), /brightness\(0\.35\)/, "never dark enough to look broken");
  assert.equal(pageFilter({ dim: 2, warm: -1 }), "none", "out of range is ignored");
});
