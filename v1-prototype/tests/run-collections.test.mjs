import test from "node:test";
import assert from "node:assert/strict";
import {
  orderedRuns, collectionCards, sortCollections, collectionsFor, collectionMatches, moveRun,
} from "../src/run-collections.js";

const series = [
  { id: 1, title: "Saga", year: "2012", cover: "/saga.jpg" },
  { id: 2, title: "Paper Girls", year: "2015", coverCandidates: ["/pg.jpg", "/pg2.jpg"] },
  { id: 3, title: "Y: The Last Man", year: "2002", cover: null },
];
const byId = new Map(series.map((run) => [String(run.id), run]));

test("a collection shows its runs in its own order, by title, or by year", () => {
  const collection = { runIds: ["2", "1", "3"], sortMode: "custom" };
  assert.deepEqual(orderedRuns(collection, byId).map((run) => run.id), [2, 1, 3]);
  assert.deepEqual(orderedRuns({ ...collection, sortMode: "title" }, byId).map((run) => run.id), [2, 1, 3]);
  assert.deepEqual(orderedRuns({ ...collection, sortMode: "year" }, byId).map((run) => run.id), [3, 1, 2]);
  assert.deepEqual(orderedRuns({ runIds: ["9", "1"] }, byId).map((run) => run.id), [1], "a run not on the page is left out");
});

test("a card's cover is the chosen run's, else its first run's, and its years span its runs", () => {
  const [card] = collectionCards([{ id: "5", name: "Vertigo-ish", runIds: ["3", "2", "1"], sortMode: "custom" }], series);
  assert.equal(card.cover, "/pg.jpg", "Y has no cover, so the next run's");
  assert.equal(card.runCount, 3);
  assert.equal(card.years, "2002–2015");
  const [chosen] = collectionCards([{ id: "5", name: "x", runIds: ["2", "1"], coverSeriesId: "1" }], series);
  assert.equal(chosen.cover, "/saga.jpg");
  const [empty] = collectionCards([{ id: "6", name: "New", runIds: [] }], series);
  assert.equal(empty.runCount, 0, "an empty collection the admin just made still shows");
});

test("collections sort by name or newest, and say which a run is in", () => {
  const cards = [{ name: "b", createdAt: "2026-01-02" }, { name: "A", createdAt: "2026-01-03" }];
  assert.deepEqual(sortCollections(cards, "title").map((card) => card.name), ["A", "b"]);
  assert.deepEqual(sortCollections(cards, "added").map((card) => card.name), ["A", "b"]);
  const collections = [{ name: "Z", runIds: ["1"] }, { name: "M", runIds: ["2", "1"] }, { name: "Q", runIds: ["3"] }];
  assert.deepEqual(collectionsFor(1, collections).map((item) => item.name), ["M", "Z"]);
});

test("a search finds a collection by its name or a run in it", () => {
  const [card] = collectionCards([{ id: "5", name: "Image essentials", runIds: ["1", "2"] }], series);
  assert.equal(collectionMatches(card, "essentials"), true);
  assert.equal(collectionMatches(card, "paper"), true);
  assert.equal(collectionMatches(card, "batman"), false);
});

test("moving a run gives the whole order to save", () => {
  assert.deepEqual(moveRun(["1", "2", "3"], "3", 0), ["3", "1", "2"]);
  assert.deepEqual(moveRun(["1", "2", "3"], 1, 9), ["2", "3", "1"]);
});

test("an uploaded picture is a collection's cover before any run's", () => {
  const series = [{ id: "1", title: "A", cover: "/a.jpg" }, { id: "2", title: "B", cover: "/b.jpg" }];
  const [card] = collectionCards([{ id: "9", name: "Both", runIds: ["1", "2"], coverSeriesId: "2", coverImage: "/api/v1/run-collections/9/cover/image?v=1" }], series);
  assert.equal(card.cover, "/api/v1/run-collections/9/cover/image?v=1");
  assert.deepEqual(card.coverCandidates, ["/api/v1/run-collections/9/cover/image?v=1", "/b.jpg", "/a.jpg"]);
  const [plain] = collectionCards([{ id: "9", name: "Both", runIds: ["1", "2"], coverSeriesId: "2", coverImage: null }], series);
  assert.equal(plain.cover, "/b.jpg");
});
