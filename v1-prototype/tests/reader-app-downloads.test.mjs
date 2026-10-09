import { test } from "node:test";
import assert from "node:assert/strict";
import { Downloads } from "../src/reader-app/downloads.js";

// The download engine with the background session, the device and the
// server as fakes: what it hands the session, and when a comic is done.
const manifest = {
  fileId: "63", fileSignature: "sig", pageCount: 2, bytesEstimate: 1000, filename: "Night Harbor 001.cbz",
  run: { id: "61", title: "Night Harbor" },
  pages: [0, 1].map((index) => ({ index, readUrl: `/api/v1/files/63/pages/${index}?v=sig&size=read` })),
};

function setup({ rows = [], onDisk = { pages: [], panels: [] }, inFlight = [], checkAnswers = {} } = {}) {
  const enqueued = [];
  const posted = [];
  const listeners = {};
  const native = {
    async addListener(name, listener) { listeners[name] = listener; return { remove() {} }; },
    async enqueue(job) { enqueued.push(job); },
    async cancel() {},
    async transfers() { return { transfers: inFlight.map((id) => ({ id })) }; },
  };
  const saved = { panels: null };
  const store = {
    async downloads() { return rows; },
    async saveDownloads() {},
    async capBytes() { return 1e9; },
    async allDownloads() { return []; },
    async pagesOnDisk() { return onDisk.pages.map((index) => ({ index, size: 10 })); },
    async panelPartsOnDisk() { return onDisk.panels; },
    async gatherPanelParts(profileId, fileId, pageCount) {
      saved.gathered = pageCount;
      return { 0: { segmented: true, panels: [{ x: 0 }] } };
    },
    async savePanels(profileId, fileId, panels) { saved.panels = panels; },
    async removeComic(profileId, fileId) { saved.removed = [...(saved.removed || []), fileId]; },
    async pendingRemovals() { return []; },
    async savePendingRemovals() {},
  };
  const client = {
    origin: "http://server:8803",
    headers: () => ({ Authorization: "Bearer t" }),
    async api(path, options = {}) {
      if (path === "/api/v1/files/63/offline") return manifest;
      if (path === "/api/v1/offline/check") {
        posted.push({ path, body: JSON.parse(options.body) });
        return { files: JSON.parse(options.body).files.map(({ fileId }) => ({ fileId, status: checkAnswers[fileId] || "ok" })) };
      }
      posted.push({ path, method: options.method });
      return {};
    },
  };
  const downloads = new Downloads({ client, profileId: 3, store, native, idOfDevice: async () => "ios-test" });
  const emit = (name, event) => listeners[name](event);
  return { downloads, enqueued, posted, saved, emit };
}

test("a download hands each page and each page's panels to the background session", async () => {
  const { downloads, enqueued } = setup();
  await downloads.ready;
  await downloads.download("63");
  assert.deepEqual(enqueued.map((job) => job.id), ["3:63:0", "3:63:p0", "3:63:1", "3:63:p1"]);
  const panels = enqueued.find((job) => job.id === "3:63:p1");
  assert.equal(panels.url, "http://server:8803/api/v1/files/63/pages/1/panels?vision=no", "the local finder only");
  assert.equal(panels.path, "u3/files/63/panels-1.json");
  assert.deepEqual(panels.headers, { Authorization: "Bearer t" });
});

test("it is ready only once the pages and the panels are in; a page whose panels failed is read whole", async () => {
  const { downloads, emit, posted, saved } = setup();
  await downloads.ready;
  await downloads.download("63");
  await emit("transferDone", { id: "3:63:0", bytes: 400 });
  await emit("transferDone", { id: "3:63:1", bytes: 500 });
  assert.equal(downloads.row("63").state, "downloading", "the pages alone are not the comic");
  await emit("transferDone", { id: "3:63:p0" });
  await emit("transferFailed", { id: "3:63:p1", status: 500 });
  const row = downloads.row("63");
  assert.equal(row.state, "ready");
  assert.equal(row.bytes, 900);
  assert.equal(saved.gathered, 2, "the parts gathered into panels.json");
  assert.equal(saved.panels[0].segmented, true);
  assert.deepEqual(posted.map((item) => item.path), ["/api/v1/offline/downloads"], "and the server told");
});

test("after a launch, what landed while suspended counts, and only the missing is asked for again", async () => {
  const row = { ...((await import("../src/reader-app/offline.js")).downloadRow(manifest)) };
  delete row.panelsDone; // a row from before panels came this way
  const { downloads, enqueued } = setup({ rows: [row], onDisk: { pages: [0, 1], panels: [0] }, inFlight: [] });
  await downloads.ready;
  assert.deepEqual(enqueued.map((job) => job.id), ["3:63:p1"]);
  assert.equal(downloads.row("63").state, "downloading");
});

test("a reconcile finds a comic complete on disk and finishes it", async () => {
  const { downloadRow } = await import("../src/reader-app/offline.js");
  const { downloads, enqueued } = setup({ rows: [downloadRow(manifest)], onDisk: { pages: [0, 1], panels: [0, 1] } });
  await downloads.ready;
  assert.deepEqual(enqueued, []);
  assert.equal(downloads.row("63").state, "ready");
});

test("a page's failure still stops the download; a panel's 401 does too", async () => {
  const { downloads, emit } = setup();
  await downloads.ready;
  await downloads.download("63");
  await emit("transferFailed", { id: "3:63:p0", status: 401 });
  assert.equal(downloads.row("63").state, "failed");
  assert.match(downloads.row("63").error, /Sign in again/);
});

test("reconnecting, a comic this profile may no longer read goes; a replaced one is kept as stale", async () => {
  const { downloadRow } = await import("../src/reader-app/offline.js");
  const ready = (fileId) => ({ ...downloadRow({ ...manifest, fileId }), state: "ready" });
  const { downloads, posted, saved } = setup({
    rows: [ready("63"), ready("64"), ready("65"), { ...downloadRow({ ...manifest, fileId: "66" }) }],
    checkAnswers: { 64: "hidden", 65: "changed" },
  });
  await downloads.ready;
  assert.equal(await downloads.check(), 1);
  const asked = posted.find((item) => item.path === "/api/v1/offline/check");
  assert.deepEqual(asked.body.files.map((file) => file.fileId), ["63", "64", "65"], "only what is kept, not what is still coming");
  assert.equal(downloads.row("64"), null);
  assert.deepEqual(saved.removed, ["64"]);
  assert.ok(posted.some((item) => item.path === "/api/v1/offline/downloads/remove"), "the server's record told why");
  assert.equal(downloads.row("65").state, "stale");
  assert.equal(downloads.row("63").state, "ready");
});
