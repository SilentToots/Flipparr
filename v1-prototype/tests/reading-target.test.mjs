import test from "node:test";
import assert from "node:assert/strict";
import {
  READING_STATES, readingLabel, readingDetail, readingFraction, readingAriaLabel,
} from "../src/reading-target.js";

const target = (state, extra = {}) => ({ state, issueNumber: "7", ...extra });

test("each state says something different, and says which issue", () => {
  assert.equal(readingLabel(target(READING_STATES.unstarted, { issueNumber: "1" }), "comic"), "Read #1");
  assert.equal(readingLabel(target(READING_STATES.next, { issueNumber: "8" }), "comic"), "Read #8");
  assert.equal(readingLabel(target(READING_STATES.continue), "comic"), "Continue #7");
  assert.equal(readingLabel(target(READING_STATES.finished), "comic"), "Read again #7");
  assert.equal(readingLabel(target(READING_STATES.none), "comic"), "",
    "nothing readable says nothing at all, rather than a disabled button");
  assert.equal(readingLabel(null, "comic"), "");
});

test("a collection is named as a collection, whatever the run is", () => {
  // Nothing records where an issue starts inside an omnibus, so the label
  // never claims to open one.
  const volume = target(READING_STATES.volumeOnly, { issueNumber: "7", volumeLabel: "Vol. 2" });
  assert.equal(readingLabel(volume, "comic"), "Read Vol. 2");
  assert.equal(readingLabel(volume, "manga"), "Read Vol. 2");
  assert.equal(readingLabel(target(READING_STATES.volumeOnly, { volumeLabel: "" }), "comic"), "Read volume");
});

test("manga counts in volumes", () => {
  assert.equal(readingLabel(target(READING_STATES.unstarted, { issueNumber: "1" }), "manga"), "Read Vol. 1");
  assert.equal(readingLabel(target(READING_STATES.continue), "manga"), "Continue Vol. 7");
});

test("a file with no issue number is offered without inventing one", () => {
  assert.equal(readingLabel(target(READING_STATES.unstarted, { issueNumber: null }), "comic"), "Read");
});

test("only a part-read comic reports a page, counted from one", () => {
  assert.equal(readingDetail(target(READING_STATES.continue, { page: 11, pageCount: 24 })), "page 12 of 24");
  assert.equal(readingDetail(target(READING_STATES.unstarted, { page: 0, pageCount: 24 })), "",
    "a comic never opened is not on page 1");
  assert.equal(readingDetail(target(READING_STATES.finished, { page: 0, pageCount: 24 })), "");
  assert.equal(readingDetail(target(READING_STATES.continue, { page: 3 })), "",
    "a page out of an unknown number of pages says nothing");
  assert.equal(readingDetail(null), "");
});

test("the hairline is drawn only for a comic actually part-read", () => {
  assert.equal(readingFraction(target(READING_STATES.continue, { page: 11, pageCount: 24 })), 0.5);
  assert.equal(readingFraction(target(READING_STATES.continue, { page: 23, pageCount: 24 })), 1);
  assert.equal(readingFraction(target(READING_STATES.unstarted, { page: 0, pageCount: 24 })), 0);
  assert.equal(readingFraction(target(READING_STATES.finished, { page: 0, pageCount: 24 })), 0,
    "a finished run draws no bar: it is offering a re-read, not a place");
  assert.equal(readingFraction(target(READING_STATES.continue, { page: 40, pageCount: 24 })), 1,
    "never past the end, whatever the record says");
  assert.equal(readingFraction(null), 0);
});

test("a screen reader hears the comic, the state and the place", () => {
  assert.equal(
    readingAriaLabel(target(READING_STATES.continue, { page: 11, pageCount: 24 }), "comic", "Saga"),
    "Continue #7 of Saga, page 12 of 24");
  assert.equal(
    readingAriaLabel(target(READING_STATES.unstarted, { issueNumber: "1" }), "comic", "Saga"),
    "Read #1 of Saga");
  assert.equal(readingAriaLabel(target(READING_STATES.none), "comic", "Saga"), "");
});
