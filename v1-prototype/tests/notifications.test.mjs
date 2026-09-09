import test from "node:test";
import assert from "node:assert/strict";
import {
  buildNotifications, pruneDismissed, readDismissed, writeDismissed, DISMISSED_KEY,
} from "../src/notifications.js";

const failedJob = (id, extra = {}) => ({ id, status: "failed", issueNumber: id, ...extra });

test("a failed download reaches the bell, which it never used to", () => {
  const catalog = {
    requests: [{ id: 7, seriesTitle: "Saga", jobs: [failedJob(1, { issueTitle: "Saga #2" })] }],
  };
  const [item] = buildNotifications(catalog);
  assert.equal(item.id, "job:1");
  assert.equal(item.kind, "download");
  assert.equal(item.title, "Download failed · Saga #2");
  assert.equal(item.detail, "Saga");
});

test("a notification says where to go and what to open there", () => {
  const catalog = {
    replacementRequests: [{ id: 9, filename: "Saga 003.cbz", jobs: [failedJob(4)] }],
    inbox: [{ id: "x1", category: "file", severity: "error", issue: "Looks French", file: "Saga 002.cbz" }],
  };
  const [download, health] = buildNotifications(catalog);
  assert.deepEqual(download.focus, { kind: "replacement", requestId: 9, jobId: 4 });
  assert.equal(download.view, "requests");
  assert.deepEqual(health.focus, { id: "x1" });
  assert.equal(health.view, "metadata");
});

test("blocking failures sort above judgement calls", () => {
  const catalog = {
    inbox: [
      { id: "m", category: "metadata", severity: "warning", issue: "Uncertain match", file: "a.cbz" },
      { id: "f", category: "file", severity: "error", issue: "Damaged", file: "b.cbz" },
    ],
    requests: [{ id: 1, jobs: [failedJob(3)] }],
  };
  assert.deepEqual(buildNotifications(catalog).map((n) => n.kind), ["download", "file", "metadata"]);
});

test("a healthy library notifies nothing", () => {
  const catalog = { requests: [{ id: 1, jobs: [{ id: 2, status: "grabbed" }] }], inbox: [] };
  assert.deepEqual(buildNotifications(catalog), []);
  assert.deepEqual(buildNotifications(null), []);
  assert.deepEqual(buildNotifications({}), []);
});

test("dismissing hides that one and leaves the rest", () => {
  const catalog = {
    requests: [{ id: 1, jobs: [failedJob(3), failedJob(4)] }],
    inbox: [{ id: "x", category: "file", severity: "error", issue: "Damaged", file: "b.cbz" }],
  };
  const shown = buildNotifications(catalog, ["job:3"]);
  assert.deepEqual(shown.map((n) => n.id), ["job:4", "inbox:x"]);
});

test("a dismissal is dropped once its notification is gone, so a retry can alert again", () => {
  // Job ids are reused when a job is retried; a dismissal that outlived its
  // job would silence the next real failure on the same id.
  const before = { requests: [{ id: 1, jobs: [failedJob(3)] }] };
  const after = { requests: [{ id: 1, jobs: [{ id: 3, status: "grabbed" }] }] };
  assert.deepEqual(pruneDismissed(before, ["job:3", "inbox:gone"]), ["job:3"]);
  assert.deepEqual(pruneDismissed(after, ["job:3"]), []);
});

test("storage that throws leaves the bell working", () => {
  const hostile = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.deepEqual(readDismissed(hostile), []);
  assert.doesNotThrow(() => writeDismissed(hostile, ["job:1"]));
  assert.deepEqual(readDismissed(undefined), []);
});

test("storage round-trips, and junk in it is ignored rather than trusted", () => {
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  writeDismissed(storage, ["job:1", "inbox:x"]);
  assert.deepEqual(readDismissed(storage), ["job:1", "inbox:x"]);
  store.set(DISMISSED_KEY, '{"not":"an array"}');
  assert.deepEqual(readDismissed(storage), []);
  store.set(DISMISSED_KEY, "[1, 2, \"job:9\"]");
  assert.deepEqual(readDismissed(storage), ["job:9"]);
});

test("a cancelled run notifies nothing, however its jobs were left", () => {
  // Unfollow cancels the request and its jobs; nothing is waiting on anyone,
  // so a failure left on one of them is history rather than a task.
  const catalog = {
    requests: [{ id: 1, status: "cancelled", jobs: [failedJob(3)] }],
    replacementRequests: [{ id: 2, status: "cancelled", jobs: [failedJob(4)] }],
  };
  assert.deepEqual(buildNotifications(catalog), []);
});

test("a live run with the same failure still notifies", () => {
  // The guard above must be about cancellation, not about failures generally.
  const catalog = { requests: [{ id: 1, status: "open", jobs: [failedJob(3)] }] };
  assert.equal(buildNotifications(catalog).length, 1);
});
