import { test } from "node:test";
import assert from "node:assert/strict";
import {
  downloadComplete, downloadRow, evictionOrder, formatBytes, pagePath, panelPartPath, panelsUrl, parseTransferId, planDownload, progressOf,
  transferId, usageBytes,
} from "../src/reader-app/offline.js";

const MB = 1024 * 1024;
const manifest = {
  fileId: "63", fileSignature: "sig", pageCount: 3, bytesEstimate: 3 * MB, filename: "Night Harbor 001.cbz",
  issueNumber: "1", readingDirection: "ltr", run: { id: "61", title: "Night Harbor", medium: "comic" },
  pages: [0, 1, 2].map((index) => ({ index, readUrl: `/api/v1/files/63/pages/${index}?v=sig&size=read` })),
};

test("a row is made from the manifest, ready to download", () => {
  const row = downloadRow(manifest, { groupKey: "run:61" });
  assert.equal(row.state, "downloading");
  assert.deepEqual([row.fileId, row.seriesTitle, row.pageCount, row.groupKey], ["63", "Night Harbor", 3, "run:61"]);
  assert.equal(row.pageUrls.length, 3);
  assert.deepEqual(row.panelsDone, []);
  assert.equal(progressOf({ ...row, pagesDone: [0, 1] }), 2 / 6, "pages and their panels both count");
  assert.equal(progressOf({ ...row, pagesDone: [0, 1, 2], panelsDone: [0, 1, 2] }), 1);
});

test("a download is complete only once every page and every page's panels are in", () => {
  const row = downloadRow(manifest);
  assert.equal(downloadComplete({ ...row, pagesDone: [0, 1, 2] }), false, "panels still coming");
  assert.equal(downloadComplete({ ...row, pagesDone: [0, 1, 2], panelsDone: [0, 1, 2] }), true);
  assert.equal(downloadComplete({ ...row, pagesDone: [0, 1, 2], panelsDone: undefined }), false, "a row from before panels came this way");
});

test("files and transfers are named by profile, comic and page", () => {
  assert.equal(pagePath(7, "63", 2), "u7/files/63/2.jpg");
  assert.deepEqual(parseTransferId(transferId(7, "63", 2)), { profileId: "7", fileId: "63", index: 2, kind: "page" });
  assert.equal(transferId(7, "63", 2, "panels"), "7:63:p2");
  assert.deepEqual(parseTransferId("7:63:p2"), { profileId: "7", fileId: "63", index: 2, kind: "panels" });
  assert.ok(transferId(7, "63", 2, "panels").startsWith("7:63:"), "cancelled with the comic's pages");
  assert.equal(panelPartPath(7, "63", 2), "u7/files/63/panels-2.json");
  assert.equal(panelsUrl("63", 2), "/api/v1/files/63/pages/2/panels?vision=no", "the local finder only");
});

const ready = (fileId, bytes, extra = {}) => ({ fileId, bytes, state: "ready", finished: false, downloadedAt: "2026-10-01T00:00:00Z", ...extra });

test("only finished comics make way, the one read longest ago first, never the one open", () => {
  const rows = [
    ready("a", 10 * MB, { finished: true, lastReadAt: "2026-10-05T00:00:00Z" }),
    ready("b", 10 * MB, { finished: true, lastReadAt: "2026-10-02T00:00:00Z" }),
    ready("c", 10 * MB, { finished: false }),
    { fileId: "d", bytes: 0, estimate: 10 * MB, state: "downloading" },
    ready("e", 10 * MB, { finished: true, lastReadAt: "2026-09-01T00:00:00Z" }),
  ];
  assert.deepEqual(evictionOrder(rows, { reading: "e" }).map((row) => row.fileId), ["b", "a"]);
});

test("a download fits, makes room, or says how much more it needs", () => {
  const rows = [ready("a", 20 * MB, { finished: true }), ready("b", 20 * MB)];
  assert.deepEqual(planDownload(5 * MB, { capBytes: 50 * MB, rows }), { fits: true, evict: [], shortfall: 0 });
  assert.deepEqual(planDownload(25 * MB, { capBytes: 50 * MB, rows }), { fits: true, evict: ["a"], shortfall: 0 });
  assert.deepEqual(planDownload(45 * MB, { capBytes: 50 * MB, rows }), { fits: false, evict: [], shortfall: 15 * MB });
  assert.equal(usageBytes([...rows, { fileId: "c", bytes: 1 * MB, estimate: 4 * MB, state: "downloading" }]), 44 * MB);
});

test("sizes read as a person reads them", () => {
  assert.equal(formatBytes(900 * 1024), "900 KB");
  assert.equal(formatBytes(12.4 * MB), "12 MB");
  assert.equal(formatBytes(3.25 * MB), "3.3 MB");
  assert.equal(formatBytes(1.8 * 1024 * MB), "1.8 GB");
});
