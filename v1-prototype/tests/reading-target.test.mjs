import test from "node:test";
import assert from "node:assert/strict";
import {
  READING_STATES, readingVerb, readingLabel, readingDetail, readingFraction, readingAriaLabel,
} from "../src/reading-target.js";

const target = (state, extra = {}) => ({ state, issueNumber: "7", ...extra });

test("the button talks about the comic that opens, not about the run", () => {
  assert.equal(readingLabel(target(READING_STATES.unstarted, { issueNumber: "1" }), "comic"), "Begin Issue #1");
  assert.equal(readingLabel(target(READING_STATES.continue), "comic"), "Continue Issue #7");
  assert.equal(readingLabel(target(READING_STATES.next, { issueNumber: "8" }), "comic"), "Begin Issue #8",
    "finishing one issue and moving on is beginning the next, not continuing it");
  assert.equal(readingLabel(target(READING_STATES.finished, { issueNumber: "1" }), "comic"), "Restart Issue #1");
});

test("continue is reserved for a comic actually part-read", () => {
  assert.equal(readingVerb(target(READING_STATES.continue)), "Continue");
  assert.equal(readingVerb(target(READING_STATES.next)), "Begin");
  assert.equal(readingVerb(target(READING_STATES.unstarted)), "Begin");
  assert.equal(readingVerb(target(READING_STATES.volumeOnly)), "Begin");
  assert.equal(readingVerb(target(READING_STATES.finished)), "Restart");
});

test("nothing readable says nothing at all", () => {
  // Not a disabled button: that would claim the library holds something it
  // does not.
  assert.equal(readingLabel(target(READING_STATES.none), "comic"), "");
  assert.equal(readingLabel(null, "comic"), "");
  assert.equal(readingLabel(undefined, "comic"), "");
  assert.equal(readingVerb(null), "");
});

test("the detail under the action is the comic and the place", () => {
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
  // never claims to open one -- and "Vol. 2" already names itself, so it is
  // not called an issue.
  const volume = target(READING_STATES.volumeOnly, { volumeLabel: "Vol. 2" });
  assert.equal(readingLabel(volume, "comic"), "Begin Vol. 2");
  assert.equal(readingLabel(volume, "manga"), "Begin Vol. 2");
  assert.equal(readingDetail(volume, "comic"), "Vol. 2");
});

test("manga counts in volumes", () => {
  assert.equal(readingLabel(target(READING_STATES.unstarted, { issueNumber: "1" }), "manga"), "Begin Vol. 1");
  assert.equal(readingLabel(target(READING_STATES.continue), "manga"), "Continue Vol. 7");
  assert.equal(
    readingDetail(target(READING_STATES.continue, { page: 3, pageCount: 200 }), "manga"),
    "Vol. 7 · page 4 of 200");
});

test("a comic with no issue number is opened without inventing one", () => {
  assert.equal(readingLabel(target(READING_STATES.unstarted, { issueNumber: null }), "comic"), "Begin");
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

test("a screen reader hears the action, the comic, the run and the place", () => {
  assert.equal(
    readingAriaLabel(target(READING_STATES.continue, { page: 11, pageCount: 24 }), "comic", "Saga"),
    "Continue Issue #7, Saga, page 12 of 24");
  assert.equal(
    readingAriaLabel(target(READING_STATES.unstarted, { issueNumber: "1" }), "comic", "Saga"),
    "Begin Issue #1, Saga");
  assert.equal(readingAriaLabel(target(READING_STATES.none), "comic", "Saga"), "");
});
