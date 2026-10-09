import { test } from "node:test";
import assert from "node:assert/strict";
import {
  answerWhileWaiting, changeFor, dropChange, queueChange, replayRequest, withChanges, withPlaces,
} from "../src/reader-app/offline-changes.js";

test("ratings, reading-list entries and run and arc marks can wait; nothing else does", () => {
  assert.equal(changeFor("/api/v1/issues/5/rating", "POST", { rating: 4 }).key, "rating:issue:5");
  assert.equal(changeFor("/api/v1/series/61/rating", "POST", { rating: null }).body.rating, null);
  assert.equal(changeFor("/api/v1/me/reading-list/run/61", "POST").key, "list:run:61");
  assert.equal(changeFor("/api/v1/me/reading-list/run/61", "DELETE").key, "list:run:61", "adding and taking off are one thing");
  assert.equal(changeFor("/api/v1/series/61/reading", "POST", { read: true }).key, "run-read:61");
  assert.equal(changeFor("/api/v1/reading-lists/7/reading", "POST", { read: false }).key, "arc-read:7");
  assert.equal(changeFor("/api/v1/series/61/reading", "GET"), null);
  assert.equal(changeFor("/api/v1/series/61/reading", "POST", {}), null, "a mark says read or unread");
  assert.equal(changeFor("/api/v1/member-requests", "POST", {}), null);
  assert.equal(changeFor("/api/v1/profiles/switch", "POST", {}), null);
});

test("one row per thing, the latest; dropped only if it is still the row that was sent", () => {
  const add = changeFor("/api/v1/me/reading-list/run/61", "POST");
  const off = changeFor("/api/v1/me/reading-list/run/61", "DELETE");
  let queue = queueChange([], add, "2026-10-09T10:00:00Z");
  queue = queueChange(queue, changeFor("/api/v1/issues/5/rating", "POST", { rating: 3 }), "2026-10-09T10:01:00Z");
  queue = queueChange(queue, off, "2026-10-09T10:02:00Z");
  assert.equal(queue.length, 2);
  assert.equal(queue.find((row) => row.key === "list:run:61").method, "DELETE");
  assert.equal(dropChange(queue, "list:run:61", "2026-10-09T10:00:00Z").length, 2, "a newer change waits on");
  assert.equal(dropChange(queue, "list:run:61", "2026-10-09T10:02:00Z").length, 1);
});

test("a waiting change is sent with when it was made", () => {
  const rating = queueChange([], changeFor("/api/v1/issues/5/rating", "POST", { rating: 3 }), "2026-10-09T10:00:00Z")[0];
  const sent = replayRequest(rating);
  assert.equal(sent.path, "/api/v1/issues/5/rating");
  assert.deepEqual(JSON.parse(sent.options.body), { rating: 3, at: "2026-10-09T10:00:00Z" });
  const off = queueChange([], changeFor("/api/v1/me/reading-list/arc/7", "DELETE"), "2026-10-09T10:00:00Z")[0];
  assert.deepEqual(replayRequest(off), { path: "/api/v1/me/reading-list/arc/7?at=2026-10-09T10%3A00%3A00Z", options: { method: "DELETE" } });
});

const catalog = { series: [
  { id: "61", yourRating: null, issues: [{ id: "5", yourRating: 2 }, { id: "6", yourRating: null }] },
  { id: "62", yourRating: 4, issues: [] },
] };

test("the screens show a waiting rating at once", () => {
  let queue = queueChange([], changeFor("/api/v1/issues/5/rating", "POST", { rating: 5 }), "t1");
  queue = queueChange(queue, changeFor("/api/v1/series/62/rating", "POST", { rating: null }), "t2");
  const shown = withChanges("/api/v1/catalog", catalog, queue);
  assert.equal(shown.series[0].issues[0].yourRating, 5);
  assert.equal(shown.series[0].issues[1].yourRating, null);
  assert.equal(shown.series[1].yourRating, null, "taken back");
  assert.equal(catalog.series[0].issues[0].yourRating, 2, "the kept answer itself is untouched");
  assert.equal(withChanges("/api/v1/catalog", catalog, []), catalog);
});

test("the reading list shows a waiting entry, added or taken off", () => {
  const entries = { runs: [{ id: "61", at: "a" }], arcs: [], collections: [] };
  let queue = queueChange([], changeFor("/api/v1/me/reading-list/run/61", "DELETE"), "t1");
  queue = queueChange(queue, changeFor("/api/v1/me/reading-list/arc/7", "POST"), "t2");
  const shown = withChanges("/api/v1/me/reading-list", entries, queue);
  assert.deepEqual(shown.runs, []);
  assert.deepEqual(shown.arcs, [{ id: "7", at: "t2" }]);
  assert.deepEqual(answerWhileWaiting(queue[1], entries, queue), shown, "and the add answers with the list as it now is");
});

test("a run marked read shows read in its drawer, its card and the arcs it is in", () => {
  const queue = queueChange([], changeFor("/api/v1/series/61/reading", "POST", { read: true }), "2026-10-09T10:00:00Z");
  const reading = { issues: [
    { id: "63", page: 0, pageCount: 10, finishedAt: null },
    { id: "64", page: 3, pageCount: 8, finishedAt: null, readable: false },
  ], volumes: [] };
  const shown = withChanges("/api/v1/series/61/reading", reading, queue);
  assert.deepEqual([shown.issues[0].page, shown.issues[0].finishedAt], [9, "2026-10-09T10:00:00Z"]);
  assert.equal(shown.issues[1].finishedAt, null, "what cannot be read is passed over, as the server does");
  assert.equal(withChanges("/api/v1/series/70/reading", reading, queue), reading, "another run is untouched");

  const runs = withChanges("/api/v1/reading/runs", { runs: { 61: { state: "continue", read: 1, total: 3 } } }, queue);
  assert.deepEqual([runs.runs[61].state, runs.runs[61].read], ["finished", 3]);

  const arc = withChanges("/api/v1/reading-lists/7", { items: [{ fileId: "63", runId: "61", page: 0, pageCount: 10, finishedAt: null }, { fileId: "90", runId: "80", page: 0 }] }, queue);
  assert.equal(arc.items[0].finishedAt, "2026-10-09T10:00:00Z");
  assert.equal(arc.items[1].page, 0);
});

test("a run or arc marked unread has no place left", () => {
  let queue = queueChange([], changeFor("/api/v1/series/61/reading", "POST", { read: false }), "t1");
  queue = queueChange(queue, changeFor("/api/v1/reading-lists/7/reading", "POST", { read: false }), "t2");
  const runs = withChanges("/api/v1/reading/runs", { runs: { 61: { state: "finished", read: 3, total: 3 }, 62: { state: "next" } } }, queue);
  assert.deepEqual(Object.keys(runs.runs), ["62"]);
  const lists = withChanges("/api/v1/reading/lists", { lists: { 7: { state: "continue" } } }, queue);
  assert.deepEqual(lists.lists, {});
  const arc = withChanges("/api/v1/reading-lists/7", { items: [{ fileId: "63", runId: "99", page: 4, pageCount: 10, finishedAt: "x" }] }, queue);
  assert.deepEqual([arc.items[0].page, arc.items[0].finishedAt], [0, null]);
});

test("a comic marked or read offline shows so in its run and its arcs", () => {
  const places = [
    { fileId: "63", body: { read: true }, at: "t1" },
    { fileId: "64", body: { page: 7 }, at: "t2" },
    { fileId: "65", body: { read: false }, at: "t3" },
  ];
  const reading = { issues: [
    { id: "63", page: 0, pageCount: 10, finishedAt: null },
    { id: "64", page: 0, pageCount: 8, finishedAt: null },
    { id: "65", page: 5, pageCount: 8, finishedAt: "x" },
    { id: "66", page: 2, pageCount: 8, finishedAt: null },
  ] };
  const shown = withPlaces("/api/v1/series/61/reading", reading, places);
  assert.deepEqual(shown.issues.map((file) => [file.page, file.finishedAt]), [[9, "t1"], [7, "t2"], [0, null], [2, null]]);
  const arc = withPlaces("/api/v1/reading-lists/7", { items: [{ fileId: "64", page: 0, pageCount: 8 }, { fileId: null }] }, places);
  assert.deepEqual([arc.items[0].page, arc.items[0].finishedAt], [7, "t2"], "the last page finishes it");
  assert.equal(withPlaces("/api/v1/catalog", reading, places), reading);
});
