import test from "node:test";
import assert from "node:assert/strict";
import { isOnReadingList, withEntry, readingListItems, sortReadingList, EMPTY_ENTRIES } from "../src/reading-list-tab.js";

const series = [{ id: 1, title: "Saga" }, { id: 2, title: "Paper Girls" }, { id: 3, title: "Ascender" }];
const lists = [{ id: 7, name: "Hush", createdAt: "2026-09-01" }];
const collections = [{ id: "4", name: "Image essentials", runCount: 2 }];
const entries = {
  runs: [{ id: "2", at: "2026-10-01T09:00:00Z" }, { id: "1", at: "2026-09-30T09:00:00Z" }, { id: "9", at: "2026-10-01T10:00:00Z" }],
  arcs: [{ id: "7", at: "2026-10-01T08:00:00Z" }],
  collections: [{ id: "4", at: "2026-10-01T11:00:00Z" }],
};

test("it says what is on the list, and changes before the server answers", () => {
  assert.equal(isOnReadingList(entries, "run", 1), true);
  assert.equal(isOnReadingList(entries, "arc", "7"), true);
  assert.equal(isOnReadingList(entries, "collection", 5), false);
  const added = withEntry(entries, "run", 3, true, "2026-10-02T00:00:00Z");
  assert.equal(added.runs[0].id, "3", "newest first");
  assert.equal(withEntry(added, "run", 3, false).runs.some((entry) => entry.id === "3"), false);
  assert.deepEqual(withEntry(null, "arc", 7, true, "x").arcs, [{ id: "7", at: "x" }]);
  assert.deepEqual(EMPTY_ENTRIES, { runs: [], arcs: [], collections: [] });
});

test("the tab holds what was added, newest first, and a finished run steps out", () => {
  const items = readingListItems(entries, { series, lists, collections, runReading: { 1: { state: "finished" } } });
  assert.deepEqual(items.map((entry) => [entry.kind, entry.item.title ?? entry.item.name]), [
    ["collection", "Image essentials"],
    ["run", "Paper Girls"],
    ["arc", "Hush"],
  ], "run 9 is not on the page (hidden or gone), and Saga was read to its end");
  const arc = items.find((entry) => entry.kind === "arc").item;
  assert.deepEqual([arc.id, arc.listId, arc.kind], ["arc-7", "7", "arc"]);
  assert.equal(readingListItems(entries, { series, lists, collections, listReading: { 7: { state: "finished" } } })
    .some((entry) => entry.kind === "arc"), false);
});

test("it sorts by title when asked", () => {
  const items = readingListItems(entries, { series, lists, collections });
  assert.deepEqual(sortReadingList(items, "title").map((entry) => entry.item.title ?? entry.item.name),
    ["Hush", "Image essentials", "Paper Girls", "Saga"]);
  assert.equal(sortReadingList(items, "added"), items);
});
