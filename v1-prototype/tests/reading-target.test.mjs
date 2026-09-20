import test from "node:test";
import assert from "node:assert/strict";
import {
  READING_STATES, readingLabel, readingDetail, readingFraction, readingAriaLabel,
} from "../src/reading-target.js";

const target = (state, extra = {}) => ({ state, issueNumber: "7", ...extra });

test("the button talks about the run, not about one comic", () => {
  assert.equal(readingLabel(target(READING_STATES.unstarted)), "Start Series");
  assert.equal(readingLabel(target(READING_STATES.continue)), "Continue Series");
  assert.equal(readingLabel(target(READING_STATES.next)), "Continue Series",
    "finishing an issue continues the run; it does not start it again");
  assert.equal(readingLabel(target(READING_STATES.finished)), "Restart Series");
  assert.equal(readingLabel(target(READING_STATES.volumeOnly)), "Start Series");
});

test("nothing readable says nothing at all", () => {
  // Not a disabled button: that would claim the library holds something it
  // does not.
  assert.equal(readingLabel(target(READING_STATES.none)), "");
  assert.equal(readingLabel(null), "");
  assert.equal(readingLabel(undefined), "");
});

test("the comic it opens is the detail under the action", () => {
  assert.equal(readingDetail(target(READING_STATES.unstarted, { issueNumber: "1" }), "comic"), "#1");
  assert.equal(readingDetail(target(READING_STATES.next, { issueNumber: "8" }), "comic"), "#8");
  assert.equal(
    readingDetail(target(READING_STATES.continue, { page: 11, pageCount: 24 }), "comic"),
    "#7 · page 12 of 24", "pages are counted from one");
  assert.equal(readingDetail(target(READING_STATES.none), "comic"), "");
  assert.equal(readingDetail(null, "comic"), "");
});

test("a collection is named as a collection, whatever the run is", () => {
  // Nothing records where an issue starts inside an omnibus, so the button
  // never claims to open one.
  const volume = target(READING_STATES.volumeOnly, { volumeLabel: "Vol. 2" });
  assert.equal(readingLabel(volume), "Start Series");
  assert.equal(readingDetail(volume, "comic"), "Vol. 2");
  assert.equal(readingDetail(volume, "manga"), "Vol. 2");
});

test("manga counts in volumes", () => {
  assert.equal(readingDetail(target(READING_STATES.unstarted, { issueNumber: "1" }), "manga"), "Vol. 1");
  assert.equal(
    readingDetail(target(READING_STATES.continue, { page: 3, pageCount: 200 }), "manga"),
    "Vol. 7 · page 4 of 200");
});

test("a comic with no issue number is opened without inventing one", () => {
  assert.equal(readingDetail(target(READING_STATES.unstarted, { issueNumber: null }), "comic"), "");
  assert.equal(
    readingDetail(target(READING_STATES.continue, { issueNumber: null, page: 1, pageCount: 8 }), "comic"),
    "page 2 of 8", "the place still means something without a number");
});

test("the hairline is drawn only for a comic actually part-read", () => {
  assert.equal(readingFraction(target(READING_STATES.continue, { page: 11, pageCount: 24 })), 0.5);
  assert.equal(readingFraction(target(READING_STATES.continue, { page: 23, pageCount: 24 })), 1);
  assert.equal(readingFraction(target(READING_STATES.unstarted, { page: 0, pageCount: 24 })), 0);
  assert.equal(readingFraction(target(READING_STATES.finished, { page: 0, pageCount: 24 })), 0,
    "a finished run offers a restart, not a place");
  assert.equal(readingFraction(target(READING_STATES.continue, { page: 40, pageCount: 24 })), 1,
    "never past the end, whatever the record says");
  assert.equal(readingFraction(null), 0);
});

test("a screen reader hears the action, the run and the place", () => {
  assert.equal(
    readingAriaLabel(target(READING_STATES.continue, { page: 11, pageCount: 24 }), "comic", "Saga"),
    "Continue Series, Saga, #7 · page 12 of 24");
  assert.equal(
    readingAriaLabel(target(READING_STATES.unstarted, { issueNumber: "1" }), "comic", "Saga"),
    "Start Series, Saga, #1");
  assert.equal(readingAriaLabel(target(READING_STATES.none), "comic", "Saga"), "");
});
