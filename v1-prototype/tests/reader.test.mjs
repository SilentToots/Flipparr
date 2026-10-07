import test from "node:test";
import assert from "node:assert/strict";
import {
  READING_DIRECTIONS, readingDirection, actionForKey, tapAction, pageForAction,
  pageWindow, isSpread, SPREAD_RATIO, clampZoom, clampPan, zoomAt, swipeAction, pagesLeft, pageFilter,
  panelFocus, panelStep, stepCount, stepAt, stepOf, REVEAL_MIN_PANELS, quadrantPanels, readablePanels, WHOLE_PAGE, panelMask,
  pointerDistance, pointerMidpoint, pinchZoom, pinchPan, pinchLeavesPanel, PINCH_OUT_OF_PANEL, MIN_ZOOM, MAX_ZOOM, isSwipe, isFlick, releasedSwipe, verticalClose, fittedSize, FLICK_WINDOW_MS, isEdgeTouch, isStolenBack, EDGE_GESTURE_GRACE_MS, loadReaderPrefs, saveReaderPrefs,
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

test("a swipe to the left turns the page forward in a comic and back in manga", () => {
  // Dragging the page left is the same gesture as tapping the right edge.
  assert.equal(swipeAction(-80, READING_DIRECTIONS.ltr), "next");
  assert.equal(swipeAction(80, READING_DIRECTIONS.ltr), "previous");
  assert.equal(swipeAction(-80, READING_DIRECTIONS.rtl), "previous");
  assert.equal(swipeAction(80, READING_DIRECTIONS.rtl), "next");
  assert.equal(swipeAction(0, READING_DIRECTIONS.ltr), null);
});

test("pan stops at the page's edges, not the window's", () => {
  // A portrait page 300 wide in a 400 wide window: at 2x it is 600 wide,
  // 100 past each side, so it can be pulled 100 either way and no further.
  const viewport = { width: 400, height: 800 };
  const page = { width: 300, height: 800 };
  assert.deepEqual(clampPan({ x: 500, y: 0 }, 2, viewport, page), { x: 100, y: 0 });
  // At 1.2x it is 360 wide and still fits: nothing to pan sideways, while the
  // height, which fills the window, can still be pulled.
  assert.deepEqual(clampPan({ x: 50, y: 500 }, 1.2, viewport, page), { x: 0, y: 80 });
});

test("zooming keeps the point under the finger where it was", () => {
  // Doubling about a point 100 right of centre: the page moves 100 left so
  // that point stays put. About the centre itself, nothing moves.
  assert.deepEqual(zoomAt({ x: 100, y: 0 }, 1, 2), { x: -100, y: 0 });
  assert.deepEqual(zoomAt({ x: 0, y: 0 }, 1, 2), { x: 0, y: 0 });
  // Zooming back out from a panned page about the same point returns it.
  assert.deepEqual(zoomAt({ x: 100, y: 0 }, 2, 1, { x: -100, y: 0 }), { x: 0, y: 0 });
});

test("a panel is shown zoomed to fit with a margin, centred", () => {
  // A 400x800 page in a 400x800 viewport; the top-left quarter fills half of
  // each side, so it fits at 1.84x (2x less the margin), and centring it
  // moves the page right and down by a quarter of its scaled size -- past the
  // page's own edge, which panel view allows: the panel is centred and the
  // page's edge shows black beside it.
  const viewport = { width: 400, height: 800 };
  const page = { width: 400, height: 800 };
  const focus = panelFocus({ x: 0, y: 0, w: 0.5, h: 0.5 }, viewport, page);
  assert.ok(Math.abs(focus.zoom - 1.84) < 1e-9, String(focus.zoom));
  assert.ok(Math.abs(focus.pan.x - 184) < 1e-9 && Math.abs(focus.pan.y - 368) < 1e-9, JSON.stringify(focus.pan));
});

test("a panel wider than the fitted page reads at 1x, never zoomed out, and is still centred", () => {
  const focus = panelFocus({ x: 0, y: 0, w: 1, h: 0.2 }, { width: 400, height: 800 }, { width: 400, height: 800 });
  assert.equal(focus.zoom, 1);
  // The strip across the top of the page is brought to the middle of the
  // screen: the page moves down by 320, and the screen above it is black.
  assert.deepEqual(focus.pan, { x: 0, y: 320 });
});

test("a low panel on a phone is centred, not left low on the screen", () => {
  // A 2:3 page on a 375x812 phone fits by width: 375x562 at 1x. Its bottom
  // strip, full width, zooms to ~1.06 and would sit at the bottom of a page
  // that ends 125px above the screen's bottom edge; instead it is centred.
  const viewport = { width: 375, height: 812 };
  const page = { width: 375, height: 562 };
  const focus = panelFocus({ x: 0.03, y: 0.75, w: 0.94, h: 0.22 }, viewport, page);
  const centreY = (0.75 + 0.11 - 0.5) * 562 * focus.zoom;
  assert.ok(Math.abs(focus.pan.y + centreY) < 1e-9, "the panel's centre lands on the viewport's centre");
});

test("the loose bound stops half a page off screen, the tight one at its edge", () => {
  const viewport = { width: 400, height: 800 };
  const page = { width: 400, height: 600 };
  assert.deepEqual(clampPan({ x: 0, y: 900 }, 1, viewport, page), { x: 0, y: 0 }, "tight: a page that fits cannot move");
  assert.deepEqual(clampPan({ x: 0, y: 900 }, 1, viewport, page, true), { x: 0, y: 400 }, "loose: as far as half the screen");
  assert.deepEqual(clampPan({ x: 0, y: 900 }, 2, viewport, page, true), { x: 0, y: 600 }, "loose at 2x: half the scaled page");
});

test("stepping walks the panels, then the pages, and stops at the ends", () => {
  const counts = [2, 3, 1];
  assert.deepEqual(panelStep({ page: 0, panel: 0 }, "next", counts), { page: 0, panel: 1 });
  assert.deepEqual(panelStep({ page: 0, panel: 1 }, "next", counts), { page: 1, panel: 0 }, "off the last panel onto the next page");
  assert.deepEqual(panelStep({ page: 1, panel: 0 }, "previous", counts), { page: 0, panel: 1 }, "back onto the previous page's last");
  assert.equal(panelStep({ page: 2, panel: 0 }, "next", counts), null, "past the end, so the finish drawer can fire");
  assert.equal(panelStep({ page: 0, panel: 0 }, "previous", counts), null);
  assert.deepEqual(panelStep({ page: 1, panel: 2 }, "first", counts), { page: 0, panel: 0 });
  assert.deepEqual(panelStep({ page: 0, panel: 0 }, "last", counts), { page: 2, panel: 0 });
  assert.deepEqual(panelStep({ page: 1, panel: 1 }, "chrome", counts), { page: 1, panel: 1 }, "not a step");
});

test("a page with no answer yet counts as one panel", () => {
  assert.deepEqual(panelStep({ page: 0, panel: 0 }, "next", [undefined, 2]), { page: 1, panel: 0 });
});

test("a page whose panels are still being found reads whole, then by its panels", () => {
  const found = [{ x: 0, y: 0, w: 1, h: 0.3 }, { x: 0, y: 0.3, w: 1, h: 0.7 }];
  assert.deepEqual(readablePanels(undefined, "ltr"), WHOLE_PAGE, "no answer yet: the whole page, one step");
  assert.equal(stepCount(readablePanels(undefined, "ltr").length, { start: true, end: true }), 1, "no whole-page steps around it");
  const viewport = { width: 390, height: 844 };
  const page = { width: 390, height: 600 };
  assert.deepEqual(panelFocus(WHOLE_PAGE[0], viewport, page), { zoom: 1, pan: { x: 0, y: 0 } }, "nothing zoomed");
  assert.deepEqual(readablePanels({ segmented: true, panels: found }, "ltr"), found);
  assert.deepEqual(readablePanels({ segmented: false, panels: [] }, "rtl"), quadrantPanels("rtl"), "unreadable: quadrants");
  assert.deepEqual(readablePanels({ segmented: true, panels: [] }, "ltr"), quadrantPanels("ltr"));
});

test("quadrants read the way the run does", () => {
  const centres = (rects) => rects.map((r) => [r.x + r.w / 2, r.y + r.h / 2]);
  assert.deepEqual(centres(quadrantPanels("ltr")), [[0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]]);
  assert.deepEqual(centres(quadrantPanels("rtl")), [[0.75, 0.25], [0.25, 0.25], [0.75, 0.75], [0.25, 0.75]]);
});

test("the reader remembers panel view, and forgets safely", () => {
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  const fresh = { panelMode: false, panelScrim: true, panelStartWhole: false, panelReveal: false };
  assert.deepEqual(loadReaderPrefs(storage, false), fresh);
  saveReaderPrefs({ panelMode: true }, storage);
  assert.deepEqual(loadReaderPrefs(storage, false), { ...fresh, panelMode: true });
  saveReaderPrefs({ panelMode: true, panelScrim: false, panelStartWhole: true, panelReveal: true }, storage);
  assert.deepEqual(loadReaderPrefs(storage, false), { panelMode: true, panelScrim: false, panelStartWhole: true, panelReveal: true });
  assert.deepEqual(loadReaderPrefs({ getItem: () => "{nope" }, false), fresh);
  assert.deepEqual(loadReaderPrefs({ getItem() { throw new Error("private"); } }, false), fresh);
  assert.deepEqual(loadReaderPrefs(undefined, false), fresh);
});

test("panel view is on for a phone until it has been chosen either way", () => {
  const empty = { getItem: () => null };
  assert.equal(loadReaderPrefs(empty, true).panelMode, true);
  assert.equal(loadReaderPrefs(empty, false).panelMode, false);
  const chosenOff = { getItem: () => JSON.stringify({ panelMode: false }) };
  assert.equal(loadReaderPrefs(chosenOff, true).panelMode, false, "a choice made on the phone stands");
});

test("a page can begin and end on the whole page when asked, and only a page of three panels or more", () => {
  assert.equal(stepCount(5, { end: true }), 6);
  assert.equal(stepCount(5, { start: true, end: true }), 7);
  assert.equal(stepCount(5), 5);
  assert.equal(stepCount(2, { start: true, end: true }), 2, "a two-panel page would only be seen twice");
  assert.equal(stepCount(REVEAL_MIN_PANELS, { end: true }), REVEAL_MIN_PANELS + 1);
  // What each step shows, and the step a panel is shown at.
  const both = { start: true, end: true };
  assert.deepEqual(stepAt(0, 5, both), { whole: true, panel: 0 }, "the page first");
  assert.deepEqual(stepAt(1, 5, both), { whole: false, panel: 0 });
  assert.deepEqual(stepAt(5, 5, both), { whole: false, panel: 4 });
  assert.deepEqual(stepAt(6, 5, both), { whole: true, panel: 4 }, "and the page again after the last");
  assert.deepEqual(stepAt(2, 5), { whole: false, panel: 2 });
  assert.equal(stepOf(3, 5, both), 4, "resuming on panel 4 skips the page-first step");
  assert.equal(stepOf(3, 5), 3);
  assert.equal(stepOf(9, 5), 4, "a panel the page no longer has is its last");
  // Stepping runs through the extra steps like any other, and off them onto the next page.
  const counts = [stepCount(3, { end: true }), stepCount(1, { end: true })];
  assert.deepEqual(panelStep({ page: 0, panel: 2 }, "next", counts), { page: 0, panel: 3 }, "the whole page after the last panel");
  assert.deepEqual(panelStep({ page: 0, panel: 3 }, "next", counts), { page: 1, panel: 0 });
});

test("the scrim's window sits on the panel, in the percentages a mask position takes", () => {
  // A panel a quarter of the way in and a quarter wide: the window is the
  // panel plus the pad each side, placed where CSS lands it -- position p%
  // puts the window's left edge at p% of the room left over.
  const hole = panelMask({ x: 0.25, y: 0.5, w: 0.25, h: 0.25 }, 0.01);
  assert.deepEqual([hole.w, hole.h].map((v) => Math.round(v)), [27, 27]);
  const left = (hole.x / 100) * (100 - hole.w);
  assert.ok(Math.abs(left - 24) < 0.01, `window starts at ${left}%`);
  const top = (hole.y / 100) * (100 - hole.h);
  assert.ok(Math.abs(top - 49) < 0.01);
  // Against the page's edge the pad is clipped, not pushed inside.
  const edge = panelMask({ x: 0, y: 0, w: 0.5, h: 0.5 }, 0.01);
  assert.deepEqual([edge.x, edge.y], [0, 0]);
  assert.ok(Math.abs(edge.w - 51) < 0.01);
  // A panel the width of the page has nowhere to go.
  assert.deepEqual(panelMask({ x: 0, y: 0.2, w: 1, h: 0.3 }, 0).x, 0);
});

test("a swipe is a quick, mostly sideways flick, in page view and panel view alike", () => {
  assert.equal(isSwipe(-80, 10, 200), true);
  assert.equal(isSwipe(80, -30, 700), true);
  assert.equal(isSwipe(40, 5, 200), false, "too short to mean it");
  assert.equal(isSwipe(80, 90, 200), false, "more down than across: a pull, not a swipe");
  assert.equal(isSwipe(200, 0, 1500), false, "slow: a drag to look around");
});

test("a flick is read while the finger is still moving, and only quickly", () => {
  assert.equal(isFlick(-40, 4, 90), true);
  assert.equal(isFlick(60, 10, FLICK_WINDOW_MS - 1), true);
  assert.equal(isFlick(60, 10, FLICK_WINDOW_MS), false, "past the window it is a look around");
  assert.equal(isFlick(30, 0, 50), false, "not far enough yet");
  assert.equal(isFlick(40, 35, 50), false, "as much down as across");
});

test("a Back that follows a cancelled edge touch is the phone's gesture, not the reader's", () => {
  assert.equal(isEdgeTouch(12, 390), true);
  assert.equal(isEdgeTouch(380, 390), true, "either side: the right edge is Forward");
  assert.equal(isEdgeTouch(120, 390), false);
  assert.equal(isStolenBack(1000, 1400), true);
  assert.equal(isStolenBack(1000, 1000 + EDGE_GESTURE_GRACE_MS), false, "a moment, not a minute");
  assert.equal(isStolenBack(0, 1400), false, "no edge touch behind it: someone left");
});

test("a pinch zooms about the fingers' midpoint, within the reader's limits", () => {
  const a = { x: 100, y: 100 };
  const b = { x: 160, y: 180 };
  assert.equal(pointerDistance(a, b), 100);
  assert.deepEqual(pointerMidpoint(a, b), { x: 130, y: 140 });
  assert.equal(pinchZoom(1, 100, 200), 2, "fingers twice as far apart, twice the zoom");
  assert.equal(pinchZoom(2, 100, 50), 1, "and halved, half");
  assert.equal(pinchZoom(1, 100, 20), MIN_ZOOM, "never smaller than the page");
  assert.equal(pinchZoom(3, 100, 400), MAX_ZOOM, "never past the reader's most");
  assert.equal(pinchZoom(1.5, 0, 80), 1.5, "a pinch that began with the fingers together asks for nothing");
});

test("pinching in past a panel's framing asks for the whole page, and nothing else does", () => {
  assert.equal(pinchLeavesPanel(1.2, 2, true, false), true);
  assert.equal(pinchLeavesPanel(2 * PINCH_OUT_OF_PANEL, 2, true, false), false, "a hair short of the framing is a wobble");
  assert.equal(pinchLeavesPanel(2.5, 2, true, false), false, "pinching out is looking closer");
  assert.equal(pinchLeavesPanel(1, 2, true, true), false, "the whole page is already shown");
  assert.equal(pinchLeavesPanel(1, 2, false, false), false, "page view has no panel to leave");
  assert.equal(pinchLeavesPanel(1, 0, true, false), false, "no framing yet, nothing to leave");
});


// ---- Gestures that went wrong (2026-10-07) ------------------------------------

test("a swipe at an ordinary speed is a swipe when the finger lifts, not a look around", () => {
  assert.equal(releasedSwipe(120, 20, 450), true);
  assert.equal(releasedSwipe(-120, 30, 590), true, "either way");
  assert.equal(releasedSwipe(60, 10, 300), false, "too short a travel to mean a turn");
  assert.equal(releasedSwipe(120, 100, 300), false, "as much down as across is a look around");
  assert.equal(releasedSwipe(200, 0, 900), false, "a slow drag is a drag");
});

test("only a clearly downward swipe closes the reader", () => {
  assert.equal(verticalClose(10, 150), true);
  assert.equal(verticalClose(130, 150), false, "150 down and 130 across used to close the comic");
  assert.equal(verticalClose(0, 100), false, "not far enough");
  assert.equal(verticalClose(0, -200), false, "up is not close");
});

test("a pinch that moves follows the hand", () => {
  assert.deepEqual(pinchPan({ x: 10, y: -5 }, { x: 100, y: 100 }, { x: 130, y: 80 }), { x: 40, y: -25 });
  assert.deepEqual(pinchPan({ x: 0, y: 0 }, { x: 50, y: 50 }, { x: 50, y: 50 }), { x: 0, y: 0 }, "still fingers, still page");
});

test("a page is framed at the size the stylesheet lays it out", () => {
  const viewport = { width: 390, height: 844 };
  assert.deepEqual(fittedSize({ width: 1300, height: 2000 }, viewport), { width: 390, height: 600 }, "a page fits by width on a phone");
  assert.deepEqual(fittedSize({ width: 300, height: 500 }, viewport), { width: 300, height: 500 }, "a small page is not blown up");
  const spread = fittedSize({ width: 2600, height: 2000 }, viewport);
  assert.equal(spread.width, 390, "a spread fits by width alone");
  assert.equal(spread.height, 300, "and is as tall as that makes it, not fitted by height");
  assert.deepEqual(fittedSize({ width: 0, height: 0 }, viewport), { width: 0, height: 0 });
});
