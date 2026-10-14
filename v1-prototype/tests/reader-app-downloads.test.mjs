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

function setup({ rows = [], onDisk = { pages: [], panels: [] }, inFlight = [], checkAnswers = {}, checkReading = {},
  answer = manifest, files = {} } = {}) {
  const enqueued = [];
  const posted = [];
  const listeners = {};
  const native = {
    async addListener(name, listener) { listeners[name] = listener; return { remove() {} }; },
    async enqueue(job) { enqueued.push(job); },
    async cancel() {},
    async transfers() { return { transfers: inFlight.map((id) => ({ id })) }; },
    async unpack({ path, folder }) { unpacked.push({ path, folder }); onDisk.pages = [0, 1]; return { pages: 2 }; },
  };
  const unpacked = [];
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
    async takeBulkPanels() { saved.bulk = true; return { 1: { segmented: true, panels: [{ x: 1 }] } }; },
    async exists(path) { return Boolean(files[path]); },
  };
  const client = {
    origin: "http://server:8803",
    headers: () => ({ Authorization: "Bearer t" }),
    async api(path, options = {}) {
      if (path === "/api/v1/files/63/offline") return answer;
      const other = path.match(/^\/api\/v1\/files\/(\d+)\/offline$/);
      if (other && answer.package) {
        return { ...answer, fileId: other[1], package: { ...answer.package, url: `/api/v1/files/${other[1]}/package?v=sig` },
                 panelsUrl: `/api/v1/files/${other[1]}/panels?stored=only` };
      }
      if (path === "/api/v1/offline/check") {
        posted.push({ path, body: JSON.parse(options.body) });
        return { files: JSON.parse(options.body).files.map(({ fileId }) => ({ fileId, status: checkAnswers[fileId] || "ok", ...(checkReading[fileId] || {}) })) };
      }
      posted.push({ path, method: options.method });
      return {};
    },
  };
  const downloads = new Downloads({ client, profileId: 3, store, native, idOfDevice: async () => "ios-test" });
  const emit = (name, event) => listeners[name](event);
  return { downloads, enqueued, posted, saved, emit, unpacked };
}

// The server packs a comic (2026-10-12): one file and one answer of panels.
const packaged = {
  ...manifest, package: { url: "/api/v1/files/63/package?v=sig", quality: "read", bytes: null },
  panelsUrl: "/api/v1/files/63/panels?stored=only",
};

test("a download hands each page and each page's panels to the background session", async () => {
  const { downloads, enqueued } = setup();
  await downloads.ready;
  await downloads.download("63");
  assert.deepEqual(enqueued.map((job) => job.id), ["3:63:0", "3:63:p0", "3:63:1", "3:63:p1"]);
  const panels = enqueued.find((job) => job.id === "3:63:p1");
  assert.equal(panels.url, "http://server:8803/api/v1/files/63/pages/1/panels?stored=only", "only what the server keeps");
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

test("several comics are downloaded as one choice, stopping at the first that cannot fit", async () => {
  const { downloads } = setup();
  await downloads.ready;
  const asked = [];
  downloads.download = async (fileId, { groupKey }) => {
    asked.push([fileId, groupKey]);
    if (fileId === "3") throw new Error("Needs 12 MB more than this device's download limit allows");
  };
  const result = await downloads.downloadMany(["1", "2", "3", "4"], { groupKey: "run:61" });
  assert.deepEqual(result, { started: 2, error: "Needs 12 MB more than this device's download limit allows" });
  assert.deepEqual(asked.map(([id]) => id), ["1", "2", "3"], "nothing after the one that did not fit");
  assert.equal(asked[0][1], "run:61");
});

test("finished downloads can be cleared at once; one still being read stays", async () => {
  const { downloadRow } = await import("../src/reader-app/offline.js");
  const row = (fileId, extra) => ({ ...downloadRow({ ...manifest, fileId }), state: "ready", ...extra });
  const { downloads } = setup({ rows: [row("63", { finished: true }), row("64", { finished: false }), row("65", { finished: true })],
    onDisk: { pages: [0, 1], panels: [0, 1] } });
  await downloads.ready;
  assert.equal(await downloads.removeFinished(), 2);
  assert.deepEqual(downloads.rows.map((item) => item.fileId), ["64"]);
});

test("a download finished while the server could not be told is told when the app is back", async () => {
  const { downloads, emit, posted } = setup();
  await downloads.ready;
  await downloads.download("63");
  // Locked: the note fails as unreachable.
  downloads.client.api = async (path) => {
    if (path === "/api/v1/offline/downloads") throw Object.assign(new Error("unreachable"), { network: true });
    return {};
  };
  await emit("transferDone", { id: "3:63:0" }); await emit("transferDone", { id: "3:63:1" });
  await emit("transferDone", { id: "3:63:p0" }); await emit("transferDone", { id: "3:63:p1" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(downloads.row("63").noted, false);
  const told = [];
  downloads.client.api = async (path, options) => { told.push(path); posted.push({ path, method: options?.method }); return {}; };
  await downloads.flushRemovals();
  assert.ok(told.includes("/api/v1/offline/downloads"));
  assert.equal(downloads.row("63").noted, true);
  told.length = 0;
  await downloads.flushRemovals();
  assert.ok(!told.includes("/api/v1/offline/downloads"), "told once");
});

test("reading a comic while it downloads is kept with it", async () => {
  const { downloads } = setup();
  await downloads.ready;
  await downloads.download("63");
  assert.equal(downloads.row("63").state, "downloading");
  await downloads.noteRead("63", { at: "2026-10-10T15:00:00.000Z" });
  assert.equal(downloads.row("63").lastReadAt, "2026-10-10T15:00:00.000Z", "not Unread once it arrives");
  assert.equal(downloads.row("63").finished, false);
});

test("the check takes where this profile is with each comic from the server, when newer", async () => {
  const { downloadRow } = await import("../src/reader-app/offline.js");
  const ready = (fileId, extra) => ({ ...downloadRow({ ...manifest, fileId }), state: "ready", ...extra });
  const { downloads } = setup({
    rows: [ready("63"), ready("64", { lastReadAt: "2026-10-11T22:00:00.000Z", finished: false })],
    checkReading: {
      63: { lastReadAt: "2026-10-11T21:28:14.636240+00:00", finished: true },
      64: { lastReadAt: "2026-10-11T21:00:00+00:00", finished: true },
    },
  });
  await downloads.ready;
  await downloads.check();
  assert.equal(downloads.row("63").finished, true, "finished on another device: Read here too");
  assert.equal(downloads.row("63").lastReadAt, "2026-10-11T21:28:14.636240+00:00");
  assert.equal(downloads.row("64").finished, false, "a later read on this device is kept");
});

test("a packed comic is two transfers: the package, unpacked as it lands, and every page's panels in one answer", async () => {
  const { downloads, enqueued, emit, saved, posted } = setup({ answer: packaged });
  await downloads.ready;
  await downloads.download("63");
  assert.deepEqual(enqueued.map((job) => job.id), ["3:63:pkg", "3:63:panels"], "not one request a page");
  const [pkg, panels] = enqueued;
  assert.equal(pkg.url, "http://server:8803/api/v1/files/63/package?v=sig");
  assert.equal(pkg.unpack, "u3/files/63", "unpacked into the comic's folder, where the reader looks");
  assert.equal(panels.url, "http://server:8803/api/v1/files/63/panels?stored=only");
  assert.equal(panels.path, "u3/files/63/panels-all.json");
  await emit("transferProgress", { id: "3:63:pkg", written: 500, expected: 1000 });
  const { progressOf } = await import("../src/reader-app/offline.js");
  assert.ok(Math.abs(progressOf(downloads.row("63")) - 0.485) < 0.001, "the ring follows the bytes");
  await emit("transferDone", { id: "3:63:pkg", pages: 2, bytes: 900 });
  assert.equal(downloads.row("63").state, "downloading", "the pages alone are not the comic");
  await emit("transferDone", { id: "3:63:panels" });
  const row = downloads.row("63");
  assert.equal(row.state, "ready");
  assert.equal(row.bytes, 900);
  assert.equal(saved.bulk, true, "the one answer becomes panels.json");
  assert.equal(saved.panels[1].segmented, true);
  assert.deepEqual(posted.map((item) => item.path), ["/api/v1/offline/downloads"]);
});

test("a run's comics start a few seconds apart, all handed to iOS at once", async () => {
  // 2026-10-14: eleven comics' requests together queued the server's other
  // answers behind them for a moment.
  const { DOWNLOAD_STAGGER_SECONDS } = await import("../src/reader-app/downloads.js");
  const { downloads, enqueued } = setup({ answer: packaged });
  await downloads.ready;
  const { started } = await downloads.downloadMany(["63", "64", "65"]);
  assert.equal(started, 3);
  const startOf = (id) => enqueued.find((job) => job.id === id).startIn;
  assert.deepEqual(["3:63:pkg", "3:64:pkg", "3:65:pkg"].map(startOf), [0, DOWNLOAD_STAGGER_SECONDS, 2 * DOWNLOAD_STAGGER_SECONDS]);
  assert.equal(startOf("3:64:panels"), DOWNLOAD_STAGGER_SECONDS, "a comic's panels start with it");
});

test("a comic still being packed is Preparing, not 100%: the wait's own answer is not the comic", async () => {
  // The owner, 2026-10-14: every issue of a run showed "Downloading 100%"
  // while its package was still being made.
  const { downloads, emit } = setup({ answer: packaged });
  await downloads.ready;
  await downloads.download("63");
  await emit("transferDone", { id: "3:63:panels" });
  await emit("transferProgress", { id: "3:63:pkg", written: 64, expected: 64 });
  await emit("transferWaiting", { id: "3:63:pkg", seconds: 10 });
  const { progressOf } = await import("../src/reader-app/offline.js");
  const row = downloads.row("63");
  assert.equal(row.preparing, true);
  assert.ok(progressOf(row) < 0.05, `not done: ${progressOf(row)}`);
});

test("a packed comic whose panels could not be had reads whole; one changed on the server is downloaded again as it is", async () => {
  const { downloads, enqueued, emit } = setup({ answer: packaged });
  await downloads.ready;
  await downloads.download("63");
  await emit("transferFailed", { id: "3:63:panels", status: 500 });
  await emit("transferDone", { id: "3:63:pkg", pages: 2, bytes: 900 });
  assert.equal(downloads.row("63").state, "ready");
  const again = setup({ answer: packaged });
  await again.downloads.ready;
  await again.downloads.download("63");
  await again.emit("transferFailed", { id: "3:63:pkg", status: 409 });
  assert.equal(again.downloads.row("63").state, "downloading", "asked for again, not failed");
  assert.equal(again.enqueued.filter((job) => job.id === "3:63:pkg").length, 2);
  assert.equal(enqueued.length, 2);
});

test("after a launch, a package left whole is unpacked, a panels answer on disk counts, and nothing is asked twice", async () => {
  const { downloadRow } = await import("../src/reader-app/offline.js");
  const onDisk = { pages: [], panels: [] };
  const { downloads, enqueued, unpacked } = setup({
    rows: [downloadRow(packaged)], onDisk,
    files: { "u3/files/63/package.zip": true, "u3/files/63/panels-all.json": true },
  });
  await downloads.ready;
  assert.deepEqual(unpacked, [{ path: "u3/files/63/package.zip", folder: "u3/files/63" }]);
  assert.deepEqual(enqueued, []);
  assert.equal(downloads.row("63").state, "ready");
  const inFlight = setup({ rows: [downloadRow(packaged)], inFlight: ["3:63:pkg", "3:63:panels"] });
  await inFlight.downloads.ready;
  assert.deepEqual(inFlight.enqueued, [], "in flight: left to the session");
  const lost = setup({ rows: [downloadRow(packaged)] });
  await lost.downloads.ready;
  assert.deepEqual(lost.enqueued.map((job) => job.id), ["3:63:pkg", "3:63:panels"], "neither here nor in flight: asked again");
});
