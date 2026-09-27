import test from "node:test";
import assert from "node:assert/strict";
import {
  requestKey, waitingKeys, runRequested, requestScope, adminQueue, requestNotifications,
  REQUEST_STATE_LABELS, ADMIN_STATE_LABELS,
} from "../src/member-requests.js";

test("a request is known by the same key the server gives it", () => {
  assert.equal(requestKey("run", { seriesId: 12 }), "run:12");
  assert.equal(requestKey("collection", { collectionId: 3 }), "collection:3");
  assert.equal(requestKey("discover_run", { provider: "metron", providerSeriesId: "42" }), "discover:metron:42");
  assert.equal(requestKey("discover_issues", { provider: "metron", providerSeriesId: "42", numbers: ["2", "1", "2"] }),
    "discover:metron:42#1,2", "sorted, once each");
  assert.equal(requestKey("discover_issues", { provider: "metron", providerSeriesId: "42", released: true }),
    "discover:metron:42#released");
});

test("what is still waiting marks its card, whether the run or some of its issues", () => {
  const waiting = waitingKeys([
    { status: "pending", targetKey: "discover:metron:42#1" },
    { status: "failed", targetKey: "run:7" },
    { status: "declined", targetKey: "discover:metron:9" },
  ]);
  assert.deepEqual([...waiting].sort(), ["discover:metron:42#1", "run:7"]);
  assert.equal(runRequested(waiting, "metron", "42"), true);
  assert.equal(runRequested(waiting, "metron", "4"), false, "not a prefix of another run's id");
  assert.equal(runRequested(waiting, "metron", "9"), false, "declined is not waiting");
});

test("a reader is told a failed approval is still waiting; the admin that it failed", () => {
  assert.equal(REQUEST_STATE_LABELS.failed, "Waiting for approval");
  assert.equal(ADMIN_STATE_LABELS.failed, "Couldn't be done");
});

test("what was asked for reads as a line", () => {
  assert.equal(requestScope({ kind: "run" }), "The whole run");
  assert.equal(requestScope({ kind: "discover_issues", detail: { numbers: ["4"] } }), "Issue 4");
  assert.equal(requestScope({ kind: "discover_issues", detail: { numbers: ["1", "2", "3", "4", "5", "6"] } }),
    "Issues 1, 2, 3, 4 and 2 more");
  assert.equal(requestScope({ kind: "discover_issues", params: { released: true } }), "Every released issue");
});

test("the admin's queue puts what waits first, oldest first", () => {
  const { waiting, decided } = adminQueue([
    { id: 3, status: "pending", createdAt: "2026-09-27T10:00:00Z" },
    { id: 2, status: "approved", createdAt: "2026-09-26T10:00:00Z" },
    { id: 1, status: "failed", createdAt: "2026-09-25T10:00:00Z" },
  ]);
  assert.deepEqual(waiting.map((r) => r.id), [1, 3]);
  assert.deepEqual(decided.map((r) => r.id), [2]);
});

test("the admin hears that requests wait; a reader hears what was decided since they last looked", () => {
  const requests = [
    { id: 5, status: "pending", state: "pending", title: "Saga", kind: "run", requestedBy: { name: "Sam" } },
    { id: 6, status: "pending", state: "pending", title: "Paper Girls", kind: "run", requestedBy: { name: "Pat" } },
  ];
  const [admin] = requestNotifications(requests, { admin: true });
  assert.equal(admin.title, "2 requests waiting");
  assert.equal(admin.detail, "From Sam and Pat");
  assert.equal(admin.id, "requests-waiting:6", "a new request is news again");
  const [one] = requestNotifications(requests.slice(0, 1), { admin: true });
  assert.equal(one.title, "Sam asked for Saga");
  const since = "2026-09-27T00:00:00Z";
  const decided = [
    { id: 1, state: "declined", title: "Old", decidedAt: "2026-09-20T00:00:00Z" },
    { id: 2, state: "declined", title: "Saga", decidedAt: "2026-09-27T09:00:00Z", declineReason: "Too scary" },
    { id: 3, state: "available", title: "Bone", decidedAt: "2026-09-26T00:00:00Z", updatedAt: "2026-09-27T11:00:00Z", kind: "run" },
    { id: 4, state: "pending", title: "Waiting", decidedAt: null },
  ];
  const reader = requestNotifications(decided, { since });
  assert.deepEqual(reader.map((item) => item.title), ["Saga was declined", "Bone is in your library"]);
  assert.equal(reader[0].detail, "Too scary");
  assert.equal(reader[1].kind, "acquired");
  assert.ok(reader.every((item) => item.news), "nothing here waits on the reader");
  const [approved] = requestNotifications([{ id: 9, state: "approved", title: "X", decidedAt: "2026-09-28T00:00:00Z" }], { since });
  assert.deepEqual([approved.kind, approved.severity], ["request", "info"], "approved is not yet added");
});
