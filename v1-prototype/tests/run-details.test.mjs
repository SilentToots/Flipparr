import test from "node:test";
import assert from "node:assert/strict";
import {
  coverTint, creatorInitials, creatorRoleLabel, normalizedPublisher, orderedCreators, relatedRuns,
} from "../src/run-details.js";

const run = (id, title, year, publisher, creators = []) => ({ id, title, year, publisher, creators });

test("creators are one entry per person, writers first", () => {
  const ordered = orderedCreators([
    { name: "Dave McCaig", roles: ["colorist"] },
    { name: "Scott Snyder", roles: ["writer"] },
    { name: "Nick Dragotta", roles: ["penciller"] },
    { name: "Nick Dragotta", roles: ["inker"] },
  ]);
  assert.deepEqual(ordered.map((c) => c.name), ["Scott Snyder", "Nick Dragotta", "Dave McCaig"]);
  assert.deepEqual(ordered[1].roles, ["penciller", "inker"]);
  assert.equal(creatorRoleLabel(ordered[1].roles), "Pencils · Inks");
});

test("initials survive accents, suffixes and a single name", () => {
  assert.equal(creatorInitials("Marcos Martín"), "MM");
  assert.equal(creatorInitials("James Tynion IV"), "JT");
  assert.equal(creatorInitials("Moebius"), "M");
  assert.equal(creatorInitials(""), "?");
});

test("publisher names that differ only in a suffix are one publisher", () => {
  assert.equal(normalizedPublisher("Image Comics"), normalizedPublisher("Image"));
  assert.equal(normalizedPublisher("BOOM! Studios"), normalizedPublisher("Boom Studios"));
  assert.notEqual(normalizedPublisher("Dark Horse Comics"), normalizedPublisher("DC Comics"));
});

test("More by follows the writer or artist with the most other runs, not the letterer", () => {
  const snyder = { name: "Scott Snyder", roles: ["writer"] };
  const wands = { name: "Steve Wands", roles: ["letterer"] };
  const series = run(1, "Absolute Batman", 2024, "DC Comics", [snyder, wands, { name: "Nick Dragotta", roles: ["penciller"] }]);
  const all = [
    series,
    run(2, "American Vampire", 2010, "Vertigo", [snyder]),
    run(3, "Wytches", 2014, "Image", [snyder, wands]),
    run(4, "Saga", 2012, "Image", [wands]),
    run(5, "Something Else", 2020, "Image", [wands]),
    run(6, "East of West", 2013, "Image", [{ name: "Nick Dragotta", roles: ["artist"] }]),
  ];
  const { moreBy } = relatedRuns(series, all);
  assert.equal(moreBy.name, "Scott Snyder");
  assert.deepEqual(moreBy.runs.map((r) => r.title), ["Wytches", "American Vampire"], "newest first, and never the run itself");
});

test("the publisher row skips what More by already shows and matches suffix variants", () => {
  const snyder = { name: "Scott Snyder", roles: ["writer"] };
  const series = run(1, "Wytches", 2014, "Image Comics", [snyder]);
  const all = [
    series,
    run(2, "Nocterra", 2021, "Image", [snyder]),
    run(3, "Saga", 2012, "Image"),
    run(4, "Paper Girls", 2015, "Image Comics"),
    run(5, "Batman", 2011, "DC Comics"),
  ];
  const { moreBy, publisher } = relatedRuns(series, all);
  assert.deepEqual(moreBy.runs.map((r) => r.title), ["Nocterra"]);
  assert.deepEqual(publisher.runs.map((r) => r.title), ["Paper Girls", "Saga"], "closest in year first");
  assert.equal(relatedRuns(run(9, "Solo", 2000, "Nobody"), all).publisher, null);
});

test("the cover tint is the art's hue, dark enough for text on it", () => {
  const red = new Uint8ClampedArray([220, 40, 30, 255, 200, 60, 40, 255, 250, 250, 250, 255]);
  const [r, g, b] = coverTint(red).split(" ").map(Number);
  assert.ok(r > g && r > b, "keeps the red");
  const lum = (v) => { const x = v / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; };
  const L = 0.2126 * lum(r) + 0.7152 * lum(g) + 0.0722 * lum(b);
  assert.ok((1.05) / (L + 0.05) > 10, "white text on it is well past AA");
  assert.equal(coverTint(new Uint8ClampedArray([0, 0, 0, 0])), null, "a fully transparent image has no tint");
});
