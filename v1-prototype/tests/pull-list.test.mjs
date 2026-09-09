import test from "node:test";
import assert from "node:assert/strict";
import {
  classifyRequest, groupPullList, tabCount, lastArrivalAt, RECENT_ARRIVAL_DAYS,
} from "../src/pull-list.js";

const NOW = new Date("2026-09-09T12:00:00Z");
const daysAgo = (n) => new Date(NOW.getTime() - n * 24 * 60 * 60 * 1000).toISOString();

const req = (over = {}) => ({ id: 1, status: "open", jobs: [], ...over });
const arrived = (id, at) => ({ id, status: "fulfilled", downloadStatus: "imported", importedAt: at });
const ids = (bucket) => bucket.map((entry) => entry.request.id);

test("a stopped download outranks everything else about the run", () => {
  // A run can be several states at once. The most urgent reading wins, so the
  // same run never appears on two tabs and never gets counted twice.
  const request = req({ jobs: [
    { id: 1, status: "failed" },
    { id: 2, downloadStatus: "downloading" },
    arrived(3, daysAgo(1)),
  ], wantedIssueCount: 2 });
  assert.equal(classifyRequest(request, NOW), "failed");
});

test("work in flight outranks work still to look for", () => {
  const request = req({ jobs: [{ id: 1, downloadStatus: "downloading" }], wantedIssueCount: 3 });
  assert.equal(classifyRequest(request, NOW), "downloading");
});

test("a job out searching counts as in flight before it has a download", () => {
  // downloadStatus is absent until a release is grabbed, so the early part of
  // the journey is only visible on the job itself.
  assert.equal(classifyRequest(req({ jobs: [{ id: 1, status: "searching" }] }), NOW), "downloading");
  assert.equal(classifyRequest(req({ jobs: [{ id: 1, status: "grabbed" }] }), NOW), "downloading");
});

test("an open run with issues to find and nothing moving is wanted", () => {
  assert.equal(classifyRequest(req({ wantedIssueCount: 2 }), NOW), "wanted");
});

test("a run whose issues arrived recently is acquired", () => {
  const request = req({ status: "fulfilled", jobs: [arrived(1, daysAgo(2))] });
  assert.equal(classifyRequest(request, NOW), "acquired");
});

test("a run that finished long ago is on no tab at all", () => {
  // This is the point of dropping Following: a fully acquired, quiet run is
  // not activity. It lives on the Comics grid, where its ownership shows.
  const request = req({ status: "fulfilled", jobs: [arrived(1, daysAgo(RECENT_ARRIVAL_DAYS + 1))] });
  assert.equal(classifyRequest(request, NOW), null);
});

test("an unfollowed run stops being work but keeps its arrivals", () => {
  // Unfollowing an hour after thirty issues landed must not erase that they
  // landed. Nothing is waiting on anyone, so it is not Failed or Downloading;
  // what turned up lately is still a matter of record.
  const request = req({ status: "cancelled", jobs: [
    { id: 1, status: "failed" }, arrived(2, daysAgo(1)),
  ] });
  assert.equal(classifyRequest(request, NOW), "acquired");
});

test("an unfollowed run with nothing recent is on no tab", () => {
  const request = req({ status: "cancelled", jobs: [{ id: 1, status: "failed" }] });
  assert.equal(classifyRequest(request, NOW), null);
});

test("a followed run with only unpublished issues left is not wanted", () => {
  // There is nothing to search for, so it is not work; it is just waiting.
  const request = req({ upcomingIssueCount: 3, wantedIssueCount: 0, queuedJobCount: 0 });
  assert.equal(classifyRequest(request, NOW), null);
});

test("replacements are classified the same way as runs", () => {
  const catalog = {
    requests: [req({ id: 1, wantedIssueCount: 1 })],
    replacementRequests: [
      req({ id: 2, status: "fulfilled", jobs: [arrived(9, daysAgo(1))] }),
      req({ id: 3, jobs: [{ id: 10, downloadStatus: "failed" }] }),
    ],
  };
  const buckets = groupPullList(catalog, NOW);
  assert.deepEqual(buckets.wanted, [{ kind: "series", request: catalog.requests[0] }]);
  assert.deepEqual(ids(buckets.acquired), [2]);
  assert.equal(buckets.acquired[0].kind, "replacement");
  assert.deepEqual(ids(buckets.failed), [3]);
});

test("nothing is ever in two buckets", () => {
  const catalog = { requests: [
    req({ id: 1, jobs: [{ id: 1, status: "failed" }, arrived(2, daysAgo(1))], wantedIssueCount: 5 }),
    req({ id: 2, jobs: [{ id: 3, downloadStatus: "importing" }], wantedIssueCount: 2 }),
    req({ id: 3, status: "fulfilled", jobs: [arrived(4, daysAgo(3))] }),
    req({ id: 4, wantedIssueCount: 1 }),
  ] };
  const buckets = groupPullList(catalog, NOW);
  const total = Object.values(buckets).reduce((sum, b) => sum + tabCount(b), 0);
  assert.equal(total, 4, "four requests, four placements");
  const seen = Object.values(buckets).flatMap(ids);
  assert.equal(new Set(seen).size, seen.length, "no request appears twice");
});

test("acquired reads newest first, because that is the question it answers", () => {
  const catalog = { requests: [
    req({ id: 1, status: "fulfilled", jobs: [arrived(1, daysAgo(9))] }),
    req({ id: 2, status: "fulfilled", jobs: [arrived(2, daysAgo(1))] }),
    req({ id: 3, status: "fulfilled", jobs: [arrived(3, daysAgo(5))] }),
  ] };
  assert.deepEqual(ids(groupPullList(catalog, NOW).acquired), [2, 3, 1]);
});

test("acquired interleaves replacements with runs rather than listing each in turn", () => {
  // Drawing all replacements and then all runs would open the tab on whichever
  // list happened to be first, which is not "newest first" by any reading.
  const catalog = {
    requests: [
      req({ id: 1, status: "fulfilled", jobs: [arrived(1, daysAgo(1))] }),
      req({ id: 3, status: "fulfilled", jobs: [arrived(3, daysAgo(5))] }),
    ],
    replacementRequests: [
      req({ id: 2, status: "fulfilled", jobs: [arrived(2, daysAgo(3))] }),
      req({ id: 4, status: "fulfilled", jobs: [arrived(4, daysAgo(7))] }),
    ],
  };
  const acquired = groupPullList(catalog, NOW).acquired;
  assert.deepEqual(ids(acquired), [1, 2, 3, 4]);
  assert.deepEqual(acquired.map((e) => e.kind),
    ["series", "replacement", "series", "replacement"]);
});

test("a run's arrival time is its most recent issue, not its first", () => {
  const request = req({ jobs: [arrived(1, daysAgo(20)), arrived(2, daysAgo(2)), arrived(3, daysAgo(11))] });
  assert.equal(lastArrivalAt(request), daysAgo(2));
  assert.equal(lastArrivalAt(req()), null);
});

test("arrival is when the file landed, not when the job was last touched", () => {
  // Unfollowing a run rewrites every job's updatedAt, so reading arrival off
  // that dates thirty old issues to the moment they stopped being followed.
  const request = req({ status: "cancelled", jobs: [
    { id: 1, status: "cancelled", downloadStatus: "imported",
      importedAt: daysAgo(40), updatedAt: daysAgo(0) },
  ] });
  assert.equal(lastArrivalAt(request), daysAgo(40));
  assert.equal(classifyRequest(request, NOW), null, "a 40-day-old arrival is not recent");
});

test("an issue imported before arrivals were recorded still dates itself", () => {
  const request = req({ jobs: [
    { id: 1, status: "fulfilled", downloadStatus: "imported", updatedAt: daysAgo(3) },
  ] });
  assert.equal(lastArrivalAt(request), daysAgo(3));
});

test("an empty or absent catalog groups into four empty tabs", () => {
  for (const catalog of [null, {}, { requests: [], replacementRequests: [] }]) {
    const buckets = groupPullList(catalog, NOW);
    assert.deepEqual(Object.keys(buckets), ["wanted", "downloading", "acquired", "failed"]);
    assert.equal(Object.values(buckets).reduce((s, b) => s + tabCount(b), 0), 0);
    assert.ok(Object.values(buckets).every(Array.isArray));
  }
});
