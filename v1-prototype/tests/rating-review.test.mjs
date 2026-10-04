import test from "node:test";
import assert from "node:assert/strict";
import {
  profileSees, limitedProfiles, publisherKey, ratingNote, ratingRows, publisherChoices, filterRatingRows, ratingOutcome,
} from "../src/rating-review.js";

const harrison = { id: 2, name: "Harrison", role: "reader", maxRating: "teen", allowUnrated: true };
const strict = { id: 3, name: "Ada", role: "reader", maxRating: "everyone", allowUnrated: false };
const admin = { id: 1, name: "Sam", role: "admin", maxRating: null };

const series = [
  { id: 1, title: "Something Is Killing the Children", year: 2019, publisher: "Boom! Studios", ageRating: null },
  { id: 2, title: "The Woods", year: 2014, publisher: "BOOM! Studios", ageRating: null },
  { id: 3, title: "Saga", year: 2012, publisher: "Image Comics", ageRating: "mature", ageRatingFound: "mature", ageRatingFoundSource: "metron" },
  { id: 4, title: "Chew", year: 2009, publisher: "Image", ageRating: null },
  { id: 5, title: "Batman", year: "Unknown", publisher: "DC Comics", ageRating: "teen", ageRatingFound: "teen_plus",
    ageRatingFoundSource: "metron", ageRatingOverride: "teen" },
];

test("visibility mirrors the server's rule", () => {
  assert.equal(profileSees(harrison, null), true, "unrated allowed");
  assert.equal(profileSees(strict, null), false, "unrated not allowed");
  assert.equal(profileSees(harrison, "teen"), true);
  assert.equal(profileSees(harrison, "teen_plus"), false);
  assert.equal(profileSees(admin, "mature"), true);
  assert.equal(profileSees({ role: "reader", maxRating: null }, "mature"), true, "no limit, no gate");
  assert.deepEqual(limitedProfiles([admin, harrison, strict, { ...strict, id: 4, disabled: true }]).map((p) => p.id), [2, 3]);
});

test("a publisher is one house however it is spelled", () => {
  assert.equal(publisherKey("Boom! Studios"), publisherKey("BOOM! Studios"));
  assert.equal(publisherKey("Image"), publisherKey("Image Comics"));
  assert.equal(publisherKey("DC Comics"), "dc");
  const rows = ratingRows(series);
  assert.deepEqual(publisherChoices(rows).map(({ label, count }) => [label, count]),
    [["Image", 2], ["Boom! Studios", 2], ["DC Comics", 1]].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])));
});

test("rows say where a rating came from, sorted by title", () => {
  const rows = ratingRows(series);
  assert.deepEqual(rows.map((row) => row.title),
    ["Batman", "Chew", "Saga", "Something Is Killing the Children", "The Woods"]);
  const note = Object.fromEntries(rows.map((row) => [row.title, ratingNote(row)]));
  assert.equal(note.Batman, "Teen · set by you");
  assert.equal(note.Saga, "Mature · from Metron");
  assert.equal(note.Chew, "No rating");
  assert.equal(rows[0].year, "", "an unknown year is left out");
  const unknown = ratingRows([{ id: 9, title: "Blue Lock", publisher: "Publisher unknown" }])[0];
  assert.deepEqual([unknown.publisher, unknown.publisherKey, publisherChoices([unknown]).length], ["", "", 0],
    "the catalog's placeholder is no publisher");
});

test("a view is the unrated, everything, or what one profile can see", () => {
  const rows = ratingRows(series);
  const titles = (options) => filterRatingRows(rows, { profiles: [harrison, strict], ...options }).map((row) => row.title);
  assert.deepEqual(titles({}), ["Chew", "Something Is Killing the Children", "The Woods"]);
  assert.deepEqual(titles({ show: "2" }), ["Batman", "Chew", "Something Is Killing the Children", "The Woods"],
    "Harrison sees his Teen run and every unrated one");
  assert.deepEqual(titles({ show: "3" }), [], "Ada sees nothing here");
  assert.deepEqual(titles({ show: "all", publisher: publisherKey("BOOM! Studios") }),
    ["Something Is Killing the Children", "The Woods"]);
  assert.deepEqual(titles({ show: "all", query: "kill 2019" }), ["Something Is Killing the Children"]);
});

test("the outcome says who no longer sees what", () => {
  const rows = ratingRows(series);
  assert.equal(ratingOutcome(rows, ["1", "2"], "mature", [admin, harrison, strict]),
    "Rated 2 runs Mature. Harrison no longer sees them.");
  assert.equal(ratingOutcome(rows, ["4"], "everyone", [harrison, strict]), "Rated 1 run Everyone. Ada now sees it.");
  assert.equal(ratingOutcome(rows, ["5"], null, [harrison]),
    "1 run went back to the rating found for it. Harrison no longer sees it.", "Teen+ was found");
  assert.equal(ratingOutcome(rows, ["1", "5"], "teen", [harrison]), "Rated 2 runs Teen.");
});
