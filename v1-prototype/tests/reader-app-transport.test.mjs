import { test } from "node:test";
import assert from "node:assert/strict";
import { CHECK_TIMEOUT_MS, PANELS_TIMEOUT_MS, createAppTransport } from "../src/reader-app/transport.js";

// A server that can be switched off, a device store in memory, and one
// downloaded comic.
function setup({ reachable = true } = {}) {
  const sent = [];
  const server = {
    reachable,
    progress: { fileId: "63", page: 5, panel: 0, pageCount: 3, finishedAt: null, updatedAt: "2026-10-08T10:00:00Z" },
    async api(path, options = {}) {
      sent.push({ path, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null });
      if (!server.reachable) throw Object.assign(new Error("Flipparr could not be reached"), { network: true });
      if (path === "/api/v1/catalog") return { series: [{ id: "61" }] };
      if (path === "/api/v1/files/63/progress" && !options.method) return server.progress;
      if (path === "/api/v1/files/99/progress") throw Object.assign(new Error("gone"), { status: 404 });
      if (path === "/api/v1/issues/5/rating") return { id: "5", yourRating: JSON.parse(options.body).rating };
      if (path === "/api/v1/series/61/rating" && server.refuseRatings) throw Object.assign(new Error("no"), { status: 404 });
      return { ok: true };
    },
  };
  const files = {};
  const store = {
    async progressQueue(id) { return files[`q${id}`] || []; },
    async saveProgressQueue(id, queue) { files[`q${id}`] = queue; },
    async answer(id, path) { return files[`a${id}${path}`] ?? null; },
    async saveAnswer(id, path, value) { files[`a${id}${path}`] = value; },
    async panels(id, fileId) { return files[`p${id}-${fileId}`] ?? { 1: { segmented: true, panels: [{ x: 0 }] } }; },
    async savePanels(id, fileId, panels) { files[`p${id}-${fileId}`] = panels; },
    async changeQueue(id) { return files[`c${id}`] || []; },
    async saveChangeQueue(id, queue) { files[`c${id}`] = queue; },
  };
  const removed = [];
  const read = [];
  const downloads = {
    row: (id) => (String(id) === "63" ? { fileId: "63", state: "ready", pageCount: 3, pageUrls: ["/p0", "/p1", "/p2"] } : null),
    async noteRead(id, detail) { read.push([id, detail.finished]); },
    async remove(id, reason) { removed.push([id, reason]); },
  };
  let clock = 0;
  const transport = createAppTransport({
    client: server, store, now: () => `2026-10-08T12:00:0${clock++}Z`,
    setTimer: () => 0, clearTimer: () => {},
  });
  transport.attach("2", downloads);
  return { server, store, files, sent, removed, read, transport };
}

const save = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

test("online, answers come from the server, and reading's are kept for later", async () => {
  const { transport, files } = setup();
  assert.deepEqual(await transport.transport("/api/v1/catalog"), { series: [{ id: "61" }] });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(files["a2/api/v1/catalog"], { series: [{ id: "61" }] });
});

test("unreachable, it says so once and answers from the device", async () => {
  const { transport, server, sent } = setup();
  await transport.transport("/api/v1/catalog");
  await new Promise((resolve) => setImmediate(resolve));
  server.reachable = false;
  const changes = [];
  transport.subscribe((online) => changes.push(online));
  assert.deepEqual(await transport.transport("/api/v1/catalog"), { series: [{ id: "61" }] }, "the library as it last was");
  assert.equal(transport.online, false);
  assert.deepEqual(changes, [false]);
  const before = sent.length;
  assert.equal((await transport.transport("/api/v1/files/63/pages")).pages[2].readUrl, "/p2");
  assert.equal((await transport.transport("/api/v1/files/63/pages/1/panels")).segmented, true);
  assert.deepEqual((await transport.transport("/api/v1/files/63/pages/2/panels")).panels, [], "no panels: read whole");
  assert.equal(sent.length, before, "offline, nothing waits on the network");
  await assert.rejects(transport.transport("/api/v1/files/70/pages"), (error) => error.network === true, "not downloaded");
});

test("a place saved offline waits, answers the reader, and is sent with when it was made", async () => {
  const { transport, server, sent, files, read } = setup({ reachable: false });
  transport.setOffline();
  const answered = await transport.transport("/api/v1/files/63/progress", save({ page: 1, panel: 2 }));
  assert.deepEqual([answered.page, answered.panel], [1, 2]);
  assert.deepEqual(read, [["63", false]], "the download notes it was read");
  assert.equal((await transport.transport("/api/v1/files/63/progress")).page, 1, "the reader reopens there");
  assert.equal(files.q2.length, 1);
  server.reachable = true;
  assert.equal(await transport.probe(), true);
  await transport.replay();
  const replayed = sent.filter((item) => item.method === "POST");
  assert.deepEqual(replayed.at(-1).body, { page: 1, panel: 2, at: files.q2?.[0]?.at ?? replayed.at(-1).body.at });
  assert.ok(replayed.at(-1).body.at, "a replayed save carries its time");
  assert.deepEqual(files.q2, [], "sent, and gone from the queue");
});

test("a live save carries no time, and leaves nothing waiting", async () => {
  const { transport, sent, files } = setup();
  await transport.transport("/api/v1/files/63/progress", save({ page: 2 }));
  assert.deepEqual(sent.at(-1).body, { page: 2 });
  assert.deepEqual(files.q2, []);
});

test("a waiting save newer than the server's place wins; an older one does not", async () => {
  const { transport, server, files } = setup();
  // A save still waiting to be sent (its replay not yet run).
  files.q2 = [{ fileId: "63", body: { page: 1 }, at: "2026-10-08T11:00:00Z" }];
  assert.equal((await transport.transport("/api/v1/files/63/progress")).page, 1, "newer than the server's");
  server.progress = { ...server.progress, page: 2, updatedAt: "2026-10-09T00:00:00Z" };
  assert.equal((await transport.transport("/api/v1/files/63/progress")).page, 2, "read on the web since");
});

test("replay stops when the server goes again, and drops what it refuses", async () => {
  const { transport, server, files, removed } = setup({ reachable: false });
  transport.setOffline();
  await transport.transport("/api/v1/files/99/progress", save({ page: 0 }));
  await transport.transport("/api/v1/files/63/progress", save({ page: 1 }));
  server.reachable = true;
  await transport.probe();
  await transport.replay();
  assert.deepEqual(files.q2, [], "the 404 is dropped, the other sent");
  assert.deepEqual(removed, [["99", "hidden"]], "and its download, if any, goes as hidden");
});

test("offline, a comic opens where the server last had it", async () => {
  const { transport, server } = setup();
  await transport.transport("/api/v1/files/63/progress");
  await new Promise((resolve) => setImmediate(resolve));
  server.reachable = false;
  const place = await transport.transport("/api/v1/files/63/progress");
  assert.equal(place.page, 5, "the server's place, kept from the last answer");
});

test("a request can wait less than the usual time, as a launch's check does", async () => {
  const waits = [];
  const transport = createAppTransport({
    client: { api: () => new Promise(() => {}) }, store: { progressQueue: async () => [] },
    setTimer: (fire, ms) => { waits.push(ms); fire(); return 0; }, clearTimer: () => {},
  });
  await assert.rejects(transport.transport("/api/v1/auth/status", { timeoutMs: 5000 }), (error) => error.network === true);
  assert.deepEqual(waits, [5000, CHECK_TIMEOUT_MS], "then a short check whether the server answers at all");
  assert.equal(transport.online, false, "and, as it does not, the server counts as unreachable");
});

// The owner's iPhone, 2026-10-12: a panel reading took 23 s on a busy
// server, the app counted it as gone, and Fix panels vanished mid-fix.
function slowServer() {
  const waits = [];
  const asked = [];
  const transport = createAppTransport({
    client: { api: (path) => { asked.push(path); return path === "/api/v1/app" ? Promise.resolve({ name: "Flipparr" }) : new Promise(() => {}); } },
    store: { progressQueue: async () => [], saveProgressQueue: async () => {}, answer: async () => null, saveAnswer: async () => {},
      panels: async () => null, changeQueue: async () => [], saveChangeQueue: async () => {} },
    setTimer: (fire, ms) => { waits.push(ms); fire(); return 0; }, clearTimer: () => {},
  });
  return { transport, waits, asked };
}

test("a request that only runs out of time leaves the app online when the server still answers", async () => {
  const { transport, asked } = slowServer();
  const changes = [];
  transport.subscribe((online) => changes.push(online));
  await assert.rejects(transport.transport("/api/v1/catalog"), (error) => error.network === true && error.timedOut === true);
  assert.deepEqual(asked, ["/api/v1/catalog", "/api/v1/app"]);
  assert.equal(transport.online, true);
  assert.deepEqual(changes, [], "never offline, so nothing hides");
});

test("a page's panels are waited for longer, as the vision model can take half a minute", async () => {
  const { transport, waits } = slowServer();
  await assert.rejects(transport.transport("/api/v1/files/63/pages/4/panels"));
  assert.equal(waits[0], PANELS_TIMEOUT_MS);
  assert.ok(PANELS_TIMEOUT_MS >= 30000);
});

test("a place saved to a slow server waits on the device without taking the app offline", async () => {
  const { transport } = slowServer();
  transport.attach("2", null);
  const place = await transport.transport("/api/v1/files/63/progress", save({ page: 2 }));
  assert.equal(place.page, 2);
  assert.equal(transport.online, true);
});


test("a rating made offline waits, shows at once, and is sent with its time on reconnecting", async () => {
  const { transport, server, sent, files } = setup();
  files["a2/api/v1/catalog"] = { series: [{ id: "61", yourRating: null, issues: [{ id: "5", yourRating: null }] }] };
  server.reachable = false;
  transport.setOffline();
  assert.deepEqual(await transport.transport("/api/v1/issues/5/rating", save({ rating: 4 })), { id: "5", yourRating: 4 });
  const catalog = await transport.transport("/api/v1/catalog");
  assert.equal(catalog.series[0].issues[0].yourRating, 4, "the stars stay as given");
  assert.equal(files.c2.length, 1);
  server.reachable = true;
  await transport.probe();
  await transport.replay();
  const replayed = sent.filter((item) => item.path === "/api/v1/issues/5/rating").at(-1);
  assert.equal(replayed.body.rating, 4);
  assert.ok(replayed.body.at, "with when it was given");
  assert.deepEqual(files.c2, [], "and nothing waits");
});

test("a page's panels fixed offline: read so at once, kept in the downloaded comic, sent on reconnecting", async () => {
  const { transport, server, sent, files } = setup();
  server.reachable = false;
  transport.setOffline();
  const box = { x: 0.1, y: 0.1, w: 0.5, h: 0.4 };
  const fixed = await transport.transport("/api/v1/files/63/pages/2/panels", {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ panels: [box] }),
  });
  assert.equal(fixed.source, "manual");
  assert.deepEqual(fixed.panels.map(({ x, y, w, h }) => ({ x, y, w, h })), [box]);
  assert.equal(files.c2.length, 1, "the fix waits");
  const reread = await transport.transport("/api/v1/files/63/pages/2/panels");
  assert.equal(reread.source, "manual", "the reader asks again and gets the fix");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(files["p2-63"][2].source, "manual", "the downloaded comic keeps it");
  assert.ok(files["p2-63"][1], "beside the pages it had");
  server.reachable = true;
  await transport.probe();
  await transport.replay();
  const replayed = sent.filter((item) => item.path === "/api/v1/files/63/pages/2/panels").at(-1);
  assert.equal(replayed.method, "PATCH");
  assert.deepEqual(replayed.body.panels, [box]);
  assert.deepEqual(files.c2, [], "and nothing waits");
});

test("reading a page again needs the server: offline it is refused, not kept", async () => {
  const { transport, server, files } = setup();
  server.reachable = false;
  transport.setOffline();
  await assert.rejects(transport.transport("/api/v1/files/63/pages/2/panels", { method: "DELETE" }), (error) => error.network === true);
  assert.equal((files.c2 || []).length, 0);
});

test("a reading-list entry taken off offline is sent as a DELETE with ?at=", async () => {
  const { transport, server, sent, files } = setup();
  files["a2/api/v1/me/reading-list"] = { runs: [{ id: "61", at: "x" }], arcs: [], collections: [] };
  server.reachable = false;
  transport.setOffline();
  const answer = await transport.transport("/api/v1/me/reading-list/run/61", { method: "DELETE" });
  assert.deepEqual(answer.runs, [], "the list as it now is");
  assert.deepEqual((await transport.transport("/api/v1/me/reading-list")).runs, []);
  server.reachable = true;
  await transport.probe();
  await transport.replay();
  assert.match(sent.at(-1).path, /^\/api\/v1\/me\/reading-list\/run\/61\?at=/);
  assert.equal(sent.at(-1).method, "DELETE");
});

test("online, a change goes straight to the server and leaves nothing waiting; one the server stops answering waits", async () => {
  const { transport, server, files } = setup();
  assert.deepEqual(await transport.transport("/api/v1/issues/5/rating", save({ rating: 2 })), { id: "5", yourRating: 2 });
  assert.equal(files.c2, undefined);
  server.reachable = false;
  const answer = await transport.transport("/api/v1/series/61/reading", save({ read: true }));
  assert.equal(transport.online, false);
  assert.equal(files.c2[0].key, "run-read:61");
  assert.ok(answer, "answered from the device");
});

test("a waiting change the server refuses is dropped, not retried forever", async () => {
  const { transport, server, files } = setup();
  server.reachable = false;
  transport.setOffline();
  await transport.transport("/api/v1/series/61/rating", save({ rating: 3 }));
  server.reachable = true;
  server.refuseRatings = true;
  await transport.probe();
  await transport.replay();
  assert.deepEqual(files.c2, []);
});

test("told the same profile again, it keeps that profile's downloads: a read is still noted", async () => {
  const { transport, read } = setup();
  // The server's answer arriving after the kept session opened the app.
  transport.attach("2");
  await transport.transport("/api/v1/files/63/progress", save({ page: 2 }));
  assert.deepEqual(read, [["63", true]], "the last page, noted on the downloaded comic");
  // Another profile starts without them.
  transport.attach("3");
  await transport.transport("/api/v1/files/63/progress", save({ page: 1 }));
  assert.equal(read.length, 1);
});
