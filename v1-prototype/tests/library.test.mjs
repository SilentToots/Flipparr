import test from "node:test";
import assert from "node:assert/strict";
import {
  SORT_OPTIONS, LIBRARY_DEFAULTS, SCOPES, recencyOf, sortLibrary, inProgress, loadLibraryPrefs, saveLibraryPrefs,
} from "../src/library.js";

const runs = [
  { id: "1", title: "Saga", addedAt: "2026-09-01T00:00:00+00:00" },
  { id: "2", title: "Chew", addedAt: "2026-09-10T00:00:00+00:00" },
  { id: "3", title: "Fables", addedAt: "2026-09-05T00:00:00+00:00" },
  { id: "4", title: "Birthright", addedAt: null },
];
const titles = (items) => items.map((run) => run.title);

test("Recent leads the sort options and is the default", () => {
  assert.equal(SORT_OPTIONS[0].value, "recent");
  assert.equal(LIBRARY_DEFAULTS.sort, "recent");
});

test("a run is as recent as the newer of being read and gaining a comic", () => {
  const item = { addedAt: "2026-09-10T00:00:00+00:00" };
  assert.equal(recencyOf(item, { lastReadAt: "2026-09-20T00:00:00+00:00" }), "2026-09-20T00:00:00+00:00");
  assert.equal(recencyOf(item, { lastReadAt: "2026-09-01T00:00:00+00:00" }), "2026-09-10T00:00:00+00:00");
  assert.equal(recencyOf(item, undefined), "2026-09-10T00:00:00+00:00", "never opened: when its newest comic came");
  assert.equal(recencyOf({ addedAt: null }, null), "", "a followed run nothing has arrived for");
});

test("Recent puts what you are reading and what just arrived at the top", () => {
  const reading = {
    "1": { state: "continue", lastReadAt: "2026-09-20T00:00:00+00:00" },
    "3": { state: "next", lastReadAt: "2026-09-02T00:00:00+00:00" },
  };
  // Saga read today; Chew arrived on the 10th; Fables read on the 2nd but its
  // comic arrived on the 5th, which is the newer of the two; Birthright has
  // nothing to say and sinks.
  assert.deepEqual(titles(sortLibrary(runs, "recent", reading)), ["Saga", "Chew", "Fables", "Birthright"]);
});

test("runs nobody has read still order by when their comics arrived", () => {
  assert.deepEqual(titles(sortLibrary(runs, "recent", {})), ["Chew", "Fables", "Saga", "Birthright"]);
  assert.deepEqual(titles(sortLibrary(runs, "recent")), ["Chew", "Fables", "Saga", "Birthright"], "with no map at all");
});

test("ties on recency fall back to the title, never to arrival order", () => {
  const same = [
    { id: "b", title: "Zorro", addedAt: "2026-09-01T00:00:00+00:00" },
    { id: "a", title: "Akira", addedAt: "2026-09-01T00:00:00+00:00" },
  ];
  assert.deepEqual(titles(sortLibrary(same, "recent", {})), ["Akira", "Zorro"]);
});

test("the other sorts are untouched", () => {
  assert.deepEqual(titles(sortLibrary(runs, "title")), ["Birthright", "Chew", "Fables", "Saga"]);
  assert.deepEqual(titles(sortLibrary(runs, "added")), ["Chew", "Fables", "Saga", "Birthright"]);
  assert.deepEqual(titles(sortLibrary(runs, "nonsense")), ["Birthright", "Chew", "Fables", "Saga"], "an unknown sort is the title sort");
});

test("sorting never reorders the caller's array", () => {
  const before = titles(runs);
  sortLibrary(runs, "title");
  assert.deepEqual(titles(runs), before);
});

test("in progress is a run you are reading, whether mid-page or between issues", () => {
  assert.equal(inProgress({ state: "continue" }), true);
  assert.equal(inProgress({ state: "next" }), true, "finished #3 with #4 on the shelf is the middle of a run");
  assert.equal(inProgress({ state: "finished" }), false, "read to the end, however recently");
  assert.equal(inProgress(undefined), false, "never opened");
});

class FakeStorage {
  constructor(seed = {}) { this.map = new Map(Object.entries(seed)); }
  getItem(key) { return this.map.has(key) ? this.map.get(key) : null; }
  setItem(key, value) { this.map.set(key, String(value)); }
}

test("what was chosen last time comes back", () => {
  const storage = new FakeStorage();
  saveLibraryPrefs({ sort: "rated", view: "list", scope: "collections", followingOnly: true, inProgressOnly: true }, storage);
  assert.deepEqual(loadLibraryPrefs(storage), { sort: "rated", view: "list", scope: "collections", followingOnly: true, inProgressOnly: true });
});

test("nothing remembered means the defaults", () => {
  assert.deepEqual(loadLibraryPrefs(new FakeStorage()), { ...LIBRARY_DEFAULTS });
  assert.deepEqual(loadLibraryPrefs(undefined), { ...LIBRARY_DEFAULTS }, "no storage at all");
});

test("a stale or hand-edited key cannot wedge the grid", () => {
  for (const raw of ["{not json", '"a string"', "null", JSON.stringify({ sort: "attention", view: "carousel", scope: "shelves", followingOnly: "yes", inProgressOnly: 1 })]) {
    assert.deepEqual(loadLibraryPrefs(new FakeStorage({ "flipparr.library": raw })), { ...LIBRARY_DEFAULTS }, raw);
  }
});

test("a storage that throws is the same as an empty one", () => {
  const hostile = { getItem() { throw new Error("private mode"); }, setItem() { throw new Error("quota"); } };
  assert.deepEqual(loadLibraryPrefs(hostile), { ...LIBRARY_DEFAULTS });
  assert.doesNotThrow(() => saveLibraryPrefs({ sort: "title", view: "grid", scope: "runs", followingOnly: false }, hostile));
});

test("the story arcs scope is remembered, and an unknown scope falls back to runs", () => {
  const storage = new Map();
  const memory = { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) };
  saveLibraryPrefs({ sort: "recent", view: "grid", scope: "arcs", followingOnly: false, inProgressOnly: true }, memory);
  assert.equal(loadLibraryPrefs(memory).scope, "arcs");
  assert.deepEqual([...SCOPES], ["runs", "collections", "arcs"]);
  saveLibraryPrefs({ sort: "recent", view: "grid", scope: "shelves", followingOnly: false, inProgressOnly: false }, memory);
  assert.equal(loadLibraryPrefs(memory).scope, "runs");
});
