import { test } from "node:test";
import assert from "node:assert/strict";
import {
  answerName, keepsAnswer, mergeProgress, pageListFromRow, panelsFor, progressFromBody, queueDrop, queueSave,
  readerRoute, replayOrder,
} from "../src/reader-app/offline-reading.js";

const row = { fileId: "63", pageUrls: [0, 1, 2].map((index) => `/api/v1/files/63/pages/${index}?v=sig&size=read`) };

test("a downloaded comic's page list is its stored addresses, exactly", () => {
  const list = pageListFromRow(row);
  assert.equal(list.pageCount, 3);
  assert.deepEqual(list.pages[1], { index: 1, url: row.pageUrls[1], readUrl: row.pageUrls[1] });
});

test("panels come from panels.json; a page without any is read whole", () => {
  const saved = { 0: { segmented: true, panels: [{ x: 0 }] } };
  assert.equal(panelsFor(saved, "63", 0).segmented, true);
  assert.deepEqual(panelsFor(saved, "63", 2), { fileId: "63", page: 2, segmented: false, panels: [] });
  assert.deepEqual(panelsFor(null, "63", 1).panels, []);
});

test("the reader's requests are told apart by path", () => {
  assert.deepEqual(readerRoute("/api/v1/files/63/pages"), { kind: "pages", fileId: "63" });
  assert.deepEqual(readerRoute("/api/v1/files/63/pages/4/panels?vision=no"), { kind: "panels", fileId: "63", page: 4 });
  assert.deepEqual(readerRoute("/api/v1/files/63/progress"), { kind: "progress", fileId: "63" });
  assert.equal(readerRoute("/api/v1/files/63/pages/4"), null);
});

test("only reading's answers are kept for offline browsing", () => {
  for (const path of ["/api/v1/catalog", "/api/v1/reading", "/api/v1/reading/runs", "/api/v1/reading-lists/7",
    "/api/v1/me/reading-list", "/api/v1/series/61/reading"]) assert.ok(keepsAnswer(path), path);
  for (const path of ["/api/v1/settings", "/api/v1/series/61/synopsis", "/api/v1/profiles", "/api/v1/catalog?x=1"]) {
    assert.ok(!keepsAnswer(path), path);
  }
  assert.equal(answerName("/api/v1/series/61/reading"), "series__61__reading.json");
  assert.notEqual(answerName("/api/v1/reading/lists"), answerName("/api/v1/reading-lists"), "each answer its own file");
});

test("the queue holds one row per comic, the latest; it is sent oldest first", () => {
  let queue = queueSave([], "63", { page: 1, panel: 0 }, "2026-10-08T10:00:00Z");
  queue = queueSave(queue, "62", { page: 4 }, "2026-10-08T09:00:00Z");
  queue = queueSave(queue, "63", { page: 2, panel: 1 }, "2026-10-08T11:00:00Z");
  assert.equal(queue.length, 2);
  assert.deepEqual(queue.find((item) => item.fileId === "63").body, { page: 2, panel: 1 });
  assert.deepEqual(replayOrder(queue).map((item) => item.fileId), ["62", "63"]);
  // Dropped only if it is still the row that was sent: a newer save made
  // meanwhile stays to be sent.
  assert.equal(queueDrop(queue, "63", "2026-10-08T10:00:00Z").length, 2);
  assert.equal(queueDrop(queue, "63", "2026-10-08T11:00:00Z").length, 1);
});

test("the later of a waiting save and the server's place is where the reader opens", () => {
  const server = { fileId: "63", page: 5, panel: 0, pageCount: 10, finishedAt: null, updatedAt: "2026-10-08T10:00:00Z" };
  const later = { fileId: "63", body: { page: 7, panel: 2 }, at: "2026-10-08T11:00:00Z" };
  const earlier = { fileId: "63", body: { page: 3 }, at: "2026-10-08T09:00:00Z" };
  assert.deepEqual([mergeProgress(later, server).page, mergeProgress(later, server).panel], [7, 2]);
  assert.equal(mergeProgress(earlier, server).page, 5, "a newer place from the web is not undone");
  assert.equal(mergeProgress(null, server), server);
  assert.equal(mergeProgress(later, null).page, 7);
});

test("a queued save answers as a place: read, cleared, or a page", () => {
  const known = { pageCount: 10 };
  assert.equal(progressFromBody("63", { read: true }, "t", known).finishedAt, "t");
  assert.equal(progressFromBody("63", { read: true }, "t", known).page, 9);
  assert.equal(progressFromBody("63", {}, "t", known).page, 0);
  assert.equal(progressFromBody("63", { page: 9 }, "t", known).finishedAt, "t", "the last page finishes it");
  assert.equal(progressFromBody("63", { page: 3 }, "t", known).finishedAt, null);
});
