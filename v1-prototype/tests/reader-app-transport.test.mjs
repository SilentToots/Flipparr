import { test } from "node:test";
import assert from "node:assert/strict";
import { createAppTransport } from "../src/reader-app/transport.js";

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
      return { ok: true };
    },
  };
  const files = {};
  const store = {
    async progressQueue(id) { return files[`q${id}`] || []; },
    async saveProgressQueue(id, queue) { files[`q${id}`] = queue; },
    async answer(id, path) { return files[`a${id}${path}`] ?? null; },
    async saveAnswer(id, path, value) { files[`a${id}${path}`] = value; },
    async panels() { return { 1: { segmented: true, panels: [{ x: 0 }] } }; },
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
  assert.deepEqual(waits, [5000]);
  assert.equal(transport.online, false, "and the server counts as unreachable");
});

