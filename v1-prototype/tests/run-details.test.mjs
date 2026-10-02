import test from "node:test";
import assert from "node:assert/strict";
import {
  creatorRoleLabel, normalizedPublisher, orderedCreators, relatedRuns, sharedPublisher, arcPublisher,
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

test("runs with no publisher are not grouped under the placeholder", () => {
  const series = { id: 1, title: "A", publisher: "Publisher unknown" };
  const all = [series, { id: 2, title: "B", publisher: "Publisher unknown" }];
  assert.equal(relatedRuns(series, all).publisher, null);
});

test("a group names its publisher only when every run has the same one", () => {
  assert.equal(sharedPublisher([{ publisher: "BOOM! Studios" }, { publisher: "Boom! Studios" }]), "BOOM! Studios");
  assert.equal(sharedPublisher([{ publisher: "Image" }, { publisher: "DC Comics" }]), "");
  assert.equal(sharedPublisher([{ publisher: "Image" }, { publisher: "Publisher unknown" }]), "");
  assert.equal(sharedPublisher([{ publisher: "Image" }, {}]), "");
  assert.equal(sharedPublisher([]), "");
});

test("an arc names its publisher only when every issue is the library's", () => {
  const byId = new Map([["1", { id: 1, publisher: "DC Comics" }], ["2", { id: 2, publisher: "DC" }]]);
  assert.equal(arcPublisher({ issueCount: 3, issueIds: [7, 8, 9], runIds: [1, 2] }, byId), "DC Comics");
  // One issue from outside the library could be anyone's.
  assert.equal(arcPublisher({ issueCount: 4, issueIds: [7, 8, 9], runIds: [1, 2] }, byId), "");
  // A run this profile cannot see is not known to agree.
  assert.equal(arcPublisher({ issueCount: 2, issueIds: [7, 8], runIds: [1, 5] }, byId), "");
  // The drawer's arc, item by item.
  assert.equal(arcPublisher({ items: [{ runId: "1" }, { runId: "2" }] }, byId), "DC Comics");
  assert.equal(arcPublisher({ items: [{ runId: "1" }, { runId: null }] }, byId), "");
});
