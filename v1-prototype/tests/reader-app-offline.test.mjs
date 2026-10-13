import { test } from "node:test";
import assert from "node:assert/strict";
import {
  downloadComplete, downloadRow, evictionOrder, formatBytes, nextUnread, pagePath, panelPartPath, panelsUrl, parseTransferId, planDownload,
  planDownloads, progressOf, readerCatalog, transferId, usageBytes, coverStatusFor, batchProgress, groupDownloads, nextDownloaded,
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
  assert.equal(panelsUrl("63", 2), "/api/v1/files/63/pages/2/panels?stored=only", "only what the server keeps");
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

test("the next unread are taken in the drawer's order, past what is read, here or unreadable", () => {
  const files = [
    { fileId: "1", finishedAt: "x" }, { fileId: "2" }, { fileId: "3", readable: false },
    { fileId: "4" }, { fileId: "5" }, { fileId: "2" }, { fileId: null }, { fileId: "6" },
  ];
  assert.deepEqual(nextUnread(files, 5), ["2", "4", "5", "6"]);
  assert.deepEqual(nextUnread(files, 2), ["2", "4"]);
  assert.deepEqual(nextUnread(files, 5, { onDevice: new Set(["4"]) }), ["2", "5", "6"], "already here is not asked for again");
});

test("a batch is planned whole: finished comics make way, and when it cannot all fit, how many can", () => {
  const rows = [
    ready("old", 10 * MB, { finished: true, lastReadAt: "2026-10-01T00:00:00Z" }),
    ready("open", 10 * MB, { finished: false }),
  ];
  const batch = [{ fileId: "a", bytes: 8 * MB }, { fileId: "b", bytes: 8 * MB }];
  const plan = planDownloads(batch, { capBytes: 30 * MB, rows });
  assert.deepEqual([plan.fits, plan.total, plan.evict], [true, 16 * MB, ["old"]], "the finished one goes, never the open one");
  const tight = planDownloads([...batch, { fileId: "c", bytes: 8 * MB }], { capBytes: 30 * MB, rows });
  assert.deepEqual([tight.fits, tight.fitting, tight.shortfall], [false, 2, 4 * MB]);
});

const catalogFixture = {
  series: [
    { id: "61", owned: 2, issues: [{ id: "1", fileId: "63" }, { id: "2", fileId: "64" }, { id: "3", fileId: null, ownership: "unowned" }],
      fileDetails: [{ id: "63" }, { id: "64" }] },
    { id: "62", owned: 0, issues: [{ id: "4", fileId: null }], fileDetails: [] },
    { id: "70", owned: 1, issues: [{ id: "5", fileId: "80" }], fileDetails: [{ id: "80" }] },
  ],
  runCollections: [{ id: "c1", runIds: ["61", "62"] }, { id: "c2", runIds: ["62"] }],
  families: [{ id: "f1", runs: [{ id: "61" }, { id: "70" }], runIds: ["61", "70"], runCount: 2 }],
};

test("the reader's library holds only what there is to read: no missing issues, no empty runs", () => {
  const shown = readerCatalog(catalogFixture);
  assert.deepEqual(shown.series.map((run) => run.id), ["61", "70"]);
  assert.deepEqual(shown.series[0].issues.map((issue) => issue.id), ["1", "2"]);
  assert.deepEqual(shown.runCollections.map((collection) => [collection.id, collection.runIds]), [["c1", ["61"]]]);
  assert.equal(catalogFixture.series[0].issues.length, 3, "the catalog itself is untouched");
});

test("offline, only what is on this device: runs, collections and families narrow to it", () => {
  const shown = readerCatalog(catalogFixture, { onDevice: new Set(["64"]) });
  assert.deepEqual(shown.series.map((run) => [run.id, run.issues.map((issue) => issue.fileId), run.owned]), [["61", ["64"], 1]]);
  assert.deepEqual(shown.runCollections.map((collection) => collection.id), ["c1"]);
  assert.deepEqual(shown.families.map((family) => [family.id, family.runIds, family.runCount]), [["f1", ["61"], 1]]);
  assert.deepEqual(readerCatalog(catalogFixture, { onDevice: new Set() }).series, []);
});

test("a cover's corner: a ring while its comics download, a mark once some are here", () => {
  const row = (fileId, extra) => ({ fileId, seriesRunId: "61", pageCount: 2, pagesDone: [], panelsDone: [], state: "ready", ...extra });
  const rows = [
    row("63"),
    row("64", { state: "downloading", pagesDone: [0, 1], panelsDone: [] }),
    row("90", { seriesRunId: "70", groupKey: "arc:7" }),
  ];
  assert.deepEqual(coverStatusFor(rows, { kind: "issue", fileId: "63" }), { state: "done", label: "On this device" });
  assert.deepEqual(coverStatusFor(rows, { kind: "issue", fileId: "64" }), { state: "downloading", progress: 0.5 });
  assert.equal(coverStatusFor(rows, { kind: "issue", fileId: "99" }), null);
  assert.equal(coverStatusFor(rows, { kind: "run", id: "61" }).state, "downloading", "a run still coming says so");
  assert.deepEqual(coverStatusFor(rows, { kind: "run", id: "70" }), { state: "done", label: "1 on this device" });
  assert.equal(coverStatusFor(rows, { kind: "collection", id: "c1", runIds: ["70", "80"] }).state, "done");
  const issueFiles = new Map([["5", "63"]]);
  assert.equal(coverStatusFor(rows, { kind: "arc", id: "9", issueIds: ["5"] }, { issueFiles }).state, "done", "an arc by its issues");
  assert.equal(coverStatusFor(rows, { kind: "arc", id: "7", issueIds: [] }).state, "done", "or by what was downloaded for it");
  assert.equal(coverStatusFor(rows, { kind: "arc", id: "8", issueIds: ["6"] }, { issueFiles }), null);
});

test("a batch's progress only goes forward as its comics finish", () => {
  const at = (minute) => `2026-10-10T15:0${minute}:00.000Z`;
  const row = (fileId, minute, extra) => ({ ...downloadRow({ ...manifest, fileId }, { groupKey: "run:61", at: at(minute) }), ...extra });
  const half = { pagesDone: [0, 1, 2], panelsDone: [] };
  const whole = { state: "ready", pagesDone: [0, 1, 2], panelsDone: [0, 1, 2] };
  const before = [row("1", 1, half), row("2", 1), row("3", 1)];
  const after = [row("1", 1, whole), row("2", 1), row("3", 1)];
  assert.equal(batchProgress(before), 0.5 / 3);
  assert.equal(batchProgress(after), 1 / 3, "the finished comic still counts");
  assert.ok(batchProgress(after) > batchProgress(before));
  assert.equal(batchProgress([row("1", 1, whole), row("2", 1, whole)]), null, "nothing coming");
  assert.equal(batchProgress([row("9", 0, whole), row("2", 1)]), 0, "a comic downloaded before the batch is not part of it");
  assert.equal(batchProgress([row("1", 1, { state: "failed" }), row("2", 1)]), 0.5, "a failed one is done trying");
  const old = { ...row("2", 1), startedAt: undefined };
  assert.equal(batchProgress([row("1", 1, whole), old]), 0, "a row from an older build counts only while coming");
  assert.equal(coverStatusFor(after, { kind: "run", id: "61" }).progress, 1 / 3);
});

test("downloads group by run as Kindle shelves a series; a run with one comic here is that comic", () => {
  const row = (fileId, runId, number, extra = {}) => ({
    ...downloadRow({ ...manifest, fileId, issueNumber: number, run: runId ? { id: runId, title: `Run ${runId}` } : null }),
    state: "ready", downloadedAt: "2026-10-10T10:00:00Z", ...extra,
  });
  const entries = groupDownloads([
    row("1", "61", "10", { lastReadAt: "2026-10-11T09:00:00Z", finished: true }),
    row("2", "61", "2"),
    row("3", "61", "23.1"),
    row("4", "70", "1", { lastReadAt: "2026-10-11T12:00:00Z" }),
    row("5", null, null),
  ]);
  assert.deepEqual(entries.map((entry) => entry.kind), ["issue", "run", "issue"], "newest first: the lone run-70 comic, then run 61");
  const run = entries[1];
  assert.deepEqual(run.rows.map((item) => item.issueNumber), ["2", "10", "23.1"], "in issue order");
  assert.equal(run.read, 1);
  assert.equal(run.cover, run.rows[1].pageUrls[0], "the cover of the one read most lately");
  assert.equal(nextDownloaded(run.rows).fileId, "2", "carry on from the first not finished");
  assert.equal(nextDownloaded(run.rows.map((item) => ({ ...item, finished: true }))).fileId, "2", "all read: from the first");
});

test("a packed comic's transfers have their own ids, and its progress follows the bytes", async () => {
  const { transferId, parseTransferId, downloadRow, progressOf, packagePath, bulkPanelsPath } = await import("../src/reader-app/offline.js");
  assert.equal(transferId("3", "63", null, "package"), "3:63:pkg");
  assert.equal(transferId("3", "63", null, "allPanels"), "3:63:panels");
  assert.deepEqual(parseTransferId("3:63:pkg"), { profileId: "3", fileId: "63", index: null, kind: "package" });
  assert.deepEqual(parseTransferId("3:63:panels"), { profileId: "3", fileId: "63", index: null, kind: "allPanels" });
  assert.deepEqual(parseTransferId("3:63:p4"), { profileId: "3", fileId: "63", index: 4, kind: "panels" }, "a page's panels as before");
  assert.equal(packagePath("3", "63"), "u3/files/63/package.zip");
  assert.equal(bulkPanelsPath("3", "63"), "u3/files/63/panels-all.json");
  const row = downloadRow({ fileId: "63", pageCount: 4, bytesEstimate: 2000, pages: [],
    package: { url: "/api/v1/files/63/package?v=s", bytes: null }, panelsUrl: "/api/v1/files/63/panels?stored=only" });
  assert.equal(progressOf(row), 0);
  row.received = 1000;
  assert.ok(Math.abs(progressOf(row) - 0.485) < 0.001, "half the estimate");
  row.expected = 4000;
  assert.ok(Math.abs(progressOf(row) - 0.2425) < 0.001, "the server's length, once known");
  row.pagesDone = [0, 1, 2, 3];
  row.panelsDone = [0, 1, 2, 3];
  assert.equal(progressOf(row), 1);
});
