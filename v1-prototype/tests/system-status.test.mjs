import assert from "node:assert/strict";
import test from "node:test";
import { coolingProviders, formatBytes, problemTitle, waitingWork, workerAdvice, workerBadge } from "../src/system-status.js";

test("a worker's state reads as a badge, and only trouble comes with advice", () => {
  assert.deepEqual(workerBadge("running"), { label: "Running", tone: "green" });
  assert.equal(workerBadge("stopped").tone, "red");
  assert.equal(workerBadge("something-new").label, "Unknown");
  assert.equal(workerAdvice({ state: "running" }), "");
  assert.match(workerAdvice({ state: "stopped" }), /Restart Flipparr/);
});

test("a scan's state reads as a badge", async () => {
  const { scanBadge } = await import("../src/system-status.js");
  assert.deepEqual(scanBadge("complete"), { label: "Complete", tone: "green" });
  assert.equal(scanBadge("failed").tone, "red");
  assert.equal(scanBadge("interrupted").label, "Interrupted");
});

test("a problem's event name reads as a sentence", () => {
  assert.equal(problemTitle("acquisition_import_cycle_failed"), "Acquisition import cycle failed");
  assert.equal(problemTitle(""), "Problem");
});

test("sizes read as people say them", () => {
  assert.equal(formatBytes(812_000_000), "812 MB");
  assert.equal(formatBytes(1_400_000_000), "1.4 GB");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(undefined), "");
});

test("waiting work leaves out what is empty and says where to act", () => {
  const rows = waitingWork({
    downloads: [{ source: "sabnzbd", status: "downloading", count: 2 }, { source: "qbittorrent", status: "importing", count: 1 }],
    wanted: { byStatus: { queued: 3, searching: 1, failed: 2, fulfilled: 40 } },
    metadata: { byStatus: { waiting: 5, complete: 90, review: 1 } },
  });
  assert.deepEqual(rows.map((row) => [row.id, row.value, row.go || null]), [
    ["downloads", "3 (2 Usenet, 1 Torrent)", "requests"],
    ["wanted", "4", "requests"],
    ["failed", "2", "requests"],
    ["metadata", "5", null],
    ["metadata-review", "1", "health"],
  ]);
  assert.deepEqual(waitingWork({ downloads: [], wanted: { byStatus: { fulfilled: 3 } }, metadata: { byStatus: {} } }), []);
});

test("only sources still asked to wait are cooling down", () => {
  const now = new Date("2026-10-05T12:00:00Z");
  const work = { providers: [
    { provider: "metron", waitUntil: "2026-10-05T12:05:00Z", consecutiveFailures: 2, lastError: "429" },
    { provider: "gcd", waitUntil: "2026-10-05T11:00:00Z", consecutiveFailures: 0 },
    { provider: "comic_vine", waitUntil: null },
  ] };
  assert.deepEqual(coolingProviders(work, now).map((item) => item.provider), ["metron"]);
});

test("times read as a phrase", async () => {
  const { agoPhrase } = await import("../src/system-status.js");
  const now = new Date("2026-10-05T12:00:00Z");
  assert.equal(agoPhrase("2026-10-05T11:59:40Z", now), "just now");
  assert.equal(agoPhrase("2026-10-05T11:59:00Z", now), "1 minute ago");
  assert.equal(agoPhrase("2026-10-05T09:00:00Z", now), "3 hours ago");
  assert.equal(agoPhrase("2026-10-04T10:00:00Z", now), "yesterday");
  assert.equal(agoPhrase("2026-10-01T10:00:00Z", now), "on 1 Oct");
  assert.equal(agoPhrase("not a date", now), "");
});
