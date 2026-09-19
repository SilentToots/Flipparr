import test from "node:test";
import assert from "node:assert/strict";
import { readRecent, recentEntry, rememberRecent, writeRecent, RECENT_SEARCHES_KEY } from "../src/recent-searches.js";

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return { getItem: (key) => data[key] ?? null, setItem: (key, value) => { data[key] = value; }, data };
}

test("a reopened item moves to the front instead of repeating", () => {
  const a = recentEntry("series", { id: 1 });
  const b = recentEntry("run", { provider: "metron", providerSeriesId: 9, title: "Sisu" });
  const list = rememberRecent(rememberRecent(rememberRecent([], a), b), a);
  assert.deepEqual(list.map((item) => item.key), ["series:1", "run:metron-9"]);
});

test("a search is remembered once, whatever its case", () => {
  const list = rememberRecent(rememberRecent([], recentEntry("query", " Batman ")), recentEntry("query", "batman"));
  assert.deepEqual(list, [{ key: "query:batman", kind: "query", text: "batman" }]);
});

test("the list keeps only the newest", () => {
  let list = [];
  for (let id = 0; id < 12; id += 1) list = rememberRecent(list, recentEntry("series", { id }), 8);
  assert.equal(list.length, 8);
  assert.equal(list[0].key, "series:11");
});

test("storage round-trips, and bad or blocked storage reads as empty", () => {
  const storage = memoryStorage();
  writeRecent(storage, [recentEntry("series", { id: 3 })]);
  assert.equal(readRecent(storage)[0].key, "series:3");
  assert.deepEqual(readRecent(memoryStorage({ [RECENT_SEARCHES_KEY]: "{not json" })), []);
  assert.deepEqual(readRecent({ getItem() { throw new Error("blocked"); } }), []);
  assert.deepEqual(readRecent(memoryStorage({ [RECENT_SEARCHES_KEY]: '[{"key":1},{"key":"x","kind":"other"}]' })), []);
});
