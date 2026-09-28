import test from "node:test";
import assert from "node:assert/strict";
import {
  needsAttention, staleDismissals, readLegacyDismissed, forgetLegacyState, bellCount, timeAgo,
  DISMISSED_KEY, SEEN_UNTIL_KEY,
} from "../src/notifications.js";

const failedJob = (id, extra = {}) => ({ id, status: "failed", issueNumber: id, ...extra });

test("a failed download reaches the bell, which it never used to", () => {
  const catalog = {
    requests: [{ id: 7, seriesTitle: "Saga", jobs: [failedJob(1, { issueTitle: "Saga #2" })] }],
  };
  const [item] = needsAttention(catalog);
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
  const [download, health] = needsAttention(catalog);
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
  assert.deepEqual(needsAttention(catalog).map((n) => n.kind), ["download", "file", "metadata"]);
});

test("a healthy library notifies nothing", () => {
  const catalog = { requests: [{ id: 1, jobs: [{ id: 2, status: "grabbed" }] }], inbox: [] };
  assert.deepEqual(needsAttention(catalog), []);
  assert.deepEqual(needsAttention(null), []);
  assert.deepEqual(needsAttention({}), []);
});

test("dismissing hides that one and leaves the rest", () => {
  const catalog = {
    requests: [{ id: 1, jobs: [failedJob(3), failedJob(4)] }],
    inbox: [{ id: "x", category: "file", severity: "error", issue: "Damaged", file: "b.cbz" }],
  };
  const shown = needsAttention(catalog, ["job:3"]);
  assert.deepEqual(shown.map((n) => n.id), ["job:4", "inbox:x"]);
});

test("a dismissal is dropped once its notification is gone, so a retry can alert again", () => {
  // Job ids are reused when a job is retried; a dismissal that outlived its
  // job would silence the next real failure on the same id.
  const before = { requests: [{ id: 1, jobs: [failedJob(3)] }] };
  const after = { requests: [{ id: 1, jobs: [{ id: 3, status: "grabbed" }] }] };
  assert.deepEqual(staleDismissals(before, ["job:3", "inbox:gone"]), ["inbox:gone"]);
  assert.deepEqual(staleDismissals(after, ["job:3"]), ["job:3"]);
});

test("what the browser kept is moved up once, and hostile storage leaves the bell working", () => {
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
  store.set(DISMISSED_KEY, JSON.stringify(["job:1", "inbox:x", "acquired:run:4:2026", "request:9:declined", 7]));
  store.set(SEEN_UNTIL_KEY, "2026-09-09T12:00:00Z");
  assert.deepEqual(readLegacyDismissed(storage), ["job:1", "inbox:x"], "news is the server's now; junk is ignored");
  forgetLegacyState(storage);
  assert.equal(store.size, 0);
  store.set(DISMISSED_KEY, '{"not":"an array"}');
  assert.deepEqual(readLegacyDismissed(storage), []);
  const hostile = { getItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } };
  assert.deepEqual(readLegacyDismissed(hostile), []);
  assert.doesNotThrow(() => forgetLegacyState(hostile));
  assert.deepEqual(readLegacyDismissed(undefined), []);
});

test("a cancelled run notifies nothing, however its jobs were left", () => {
  // Unfollow cancels the request and its jobs; nothing is waiting on anyone,
  // so a failure left on one of them is history rather than a task.
  const catalog = {
    requests: [{ id: 1, status: "cancelled", jobs: [failedJob(3)] }],
    replacementRequests: [{ id: 2, status: "cancelled", jobs: [failedJob(4)] }],
  };
  assert.deepEqual(needsAttention(catalog), []);
});

test("a live run with the same failure still notifies", () => {
  // The guard above must be about cancellation, not about failures generally.
  const catalog = { requests: [{ id: 1, status: "open", jobs: [failedJob(3)] }] };
  assert.equal(needsAttention(catalog).length, 1);
});

test("a reader's bell has no work in it", () => {
  const catalog = { requests: [{ id: 1, jobs: [failedJob(3)] }], memberRequests: [{ id: 2, status: "pending", title: "Saga" }] };
  assert.deepEqual(needsAttention(catalog, [], { admin: false }), []);
});

test("a failed download can be retried from the bell", () => {
  const [item] = needsAttention({ requests: [{ id: 1, jobs: [failedJob(3)] }] });
  assert.equal(item.retryJobId, 3);
});

test("the badge counts work waiting and news not yet seen", () => {
  assert.equal(bellCount([{}, {}], { unread: 3 }), 5);
  assert.equal(bellCount(null, null), 0);
});

test("times read the way a phone's notifications do", () => {
  const now = new Date("2026-09-27T12:00:00Z");
  assert.equal(timeAgo("2026-09-27T11:59:40Z", now), "Just now");
  assert.equal(timeAgo("2026-09-27T11:55:00Z", now), "5m");
  assert.equal(timeAgo("2026-09-27T09:00:00Z", now), "3h");
  assert.equal(timeAgo("2026-09-26T09:00:00Z", now), "Yesterday");
  assert.equal(timeAgo("2026-09-23T12:00:00Z", now), "4d");
  assert.match(timeAgo("2026-09-01T12:00:00Z", now), /Sep|1/);
  assert.equal(timeAgo("not a date", now), "");
});

test("a metadata source that rejected its login reaches the bell, and opens its settings", () => {
  const catalog = {
    enrichment: {
      active: 4,
      providerCooldowns: [
        { provider: "metron", error: "HTTP 401 Unauthorized", nextRetryAt: "2026-09-15T18:00:00Z" },
        { provider: "comic_vine", error: "HTTP 429 Too Many Requests", nextRetryAt: "2026-09-15T18:00:00Z" },
      ],
    },
  };
  const items = needsAttention(catalog);
  assert.equal(items.length, 1, "a rate limit retries on its own and is not announced");
  assert.equal(items[0].id, "metadata-source:metron");
  assert.equal(items[0].title, "Metron rejected its saved login");
  assert.equal(items[0].view, "settings");
  assert.deepEqual(items[0].focus, { section: "metadata" });
});

test("series left without a match are announced once checking has finished", () => {
  const running = { enrichment: { active: 2, review: 1, failed: 1 } };
  assert.deepEqual(needsAttention(running), [], "a count that is still climbing is not announced");
  const settled = { enrichment: { active: 0, review: 2, failed: 1 } };
  const [item] = needsAttention(settled);
  assert.equal(item.id, "metadata-match:2:1");
  assert.equal(item.kind, "metadata");
  assert.equal(item.title, "3 series need a match");
  assert.equal(item.detail, "2 to review · 1 found no match");
  assert.equal(item.view, "metadata");
  assert.deepEqual(needsAttention({ enrichment: { active: 0, review: 0, failed: 0, complete: 34 } }), [], "all matched is not news");
});

test("a stopped metadata source sorts between a failed download and a damaged file", () => {
  const catalog = {
    requests: [{ id: 1, jobs: [failedJob(3)] }],
    inbox: [{ id: "f", category: "file", severity: "error", issue: "Damaged", file: "b.cbz" }],
    enrichment: { active: 0, review: 1, failed: 0, providerCooldowns: [{ provider: "gcd", error: "authentication failed" }] },
  };
  assert.deepEqual(needsAttention(catalog).map((item) => item.kind), ["download", "source", "file", "metadata"]);
});
