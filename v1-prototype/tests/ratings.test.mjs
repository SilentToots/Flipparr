import test from "node:test";
import assert from "node:assert/strict";
import {
  RATING_SOURCES, runRating, filledStars, ratingForPress, ratingLabel, byRating,
} from "../src/ratings.js";

test("your own rating is what a run shows when you gave one", () => {
  const rated = runRating({ yourRating: 4, issueRating: { average: 2, count: 9 } });
  assert.equal(rated.value, 4);
  assert.equal(rated.source, RATING_SOURCES.yours);
  // You rated the run. That is the answer to what you think of the run, even
  // when the issues you rated say something else.
});

test("a run nobody rated falls back to what its rated issues average", () => {
  const averaged = runRating({ yourRating: null, issueRating: { average: 4.5, count: 2 } });
  assert.equal(averaged.value, 4.5);
  assert.equal(averaged.source, RATING_SOURCES.average);
  assert.equal(averaged.count, 2, "and says how many said so");
});

test("nothing rated shows nothing rather than a zero", () => {
  assert.equal(runRating({}).source, RATING_SOURCES.none);
  assert.equal(runRating({ yourRating: null, issueRating: null }).value, 0);
  assert.equal(runRating(null).source, RATING_SOURCES.none);
  assert.equal(runRating({ issueRating: { average: 0, count: 0 } }).source, RATING_SOURCES.none);
});

test("an average rounds to the nearest half star, a rating of yours never has to", () => {
  assert.equal(filledStars(4.5), 4.5);
  assert.equal(filledStars(4.3), 4.5);
  assert.equal(filledStars(4.2), 4);
  assert.equal(filledStars(3), 3);
  assert.equal(filledStars(9), 5, "never past the last star");
  assert.equal(filledStars(-2), 0);
  assert.equal(filledStars("nonsense"), 0);
});

test("pressing the star you already gave takes the rating back", () => {
  // The only way to undo without a second control beside it.
  assert.equal(ratingForPress(3, 3), null);
  assert.equal(ratingForPress(4, 3), 4);
  assert.equal(ratingForPress(1, 0), 1);
  assert.equal(ratingForPress(1, null), 1);
});

test("what a screen reader hears says whose opinion it is", () => {
  assert.equal(ratingLabel(runRating({}), "Saga"), "Rate Saga");
  assert.equal(
    ratingLabel(runRating({ yourRating: 4 }), "Saga"),
    "Your rating for Saga: 4 of 5 stars");
  assert.equal(
    ratingLabel(runRating({ issueRating: { average: 4.5, count: 2 } }), "Saga"),
    "Saga averages 4.5 of 5 stars across 2 rated issues");
  assert.equal(
    ratingLabel(runRating({ issueRating: { average: 5, count: 1 } }), "Saga"),
    "Saga averages 5 of 5 stars across 1 rated issue");
});

test("sorting puts the highest first and leaves the unrated at the bottom", () => {
  const runs = [
    { title: "unrated" },
    { title: "yours", yourRating: 3 },
    { title: "averaged", issueRating: { average: 5, count: 4 } },
  ];
  assert.deepEqual([...runs].sort(byRating).map((run) => run.title),
    ["averaged", "yours", "unrated"]);
});
