import test from "node:test";
import assert from "node:assert/strict";
import {
  buildNotifications, pruneDismissed, readDismissed, writeDismissed, DISMISSED_KEY,
  readSeenUntil, writeSeenUntil, SEEN_UNTIL_KEY,
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

const imported = (id, issue, at) => ({
  id, issueNumber: issue, issueTitle: `Issue ${issue}`,
  status: "fulfilled", downloadStatus: "imported", importedAt: at,
});
const SINCE = "2026-09-09T12:00:00Z";

test("a batch arriving announces the run once, not thirty times", () => {
  const catalog = { requests: [{ id: 20, title: "Paper Girls", publisher: "Image Comics", status: "fulfilled",
    jobs: [imported(1, "1", "2026-09-09T12:05:00Z"), imported(2, "2", "2026-09-09T12:06:00Z"),
           imported(3, "3", "2026-09-09T12:07:00Z")] }] };
  const items = buildNotifications(catalog, [], SINCE);
  assert.equal(items.length, 1);
  assert.equal(items[0].title, "Paper Girls — 3 issues added");
  assert.equal(items[0].kind, "acquired");
  assert.equal(items[0].severity, "info");
});

test("a single arrival names the issue, because that is the news", () => {
  const catalog = { requests: [{ id: 14, title: "Saga", status: "open",
    jobs: [imported(9, "73", "2026-09-09T12:30:00Z"), imported(8, "72", "2026-09-08T09:00:00Z")] }] };
  const [item] = buildNotifications(catalog, [], SINCE);
  assert.equal(item.title, "Saga #73 added");
  assert.deepEqual(item.focus, { kind: "series", requestId: 14, jobId: 9 });
});

test("nothing that arrived before this client started looking is announced", () => {
  // Shipping this must not narrate the entire back catalogue on first load.
  const catalog = { requests: [{ id: 20, title: "Paper Girls", status: "fulfilled",
    jobs: [imported(1, "1", "2026-09-01T00:00:00Z"), imported(2, "2", "2026-09-02T00:00:00Z")] }] };
  assert.deepEqual(buildNotifications(catalog, [], SINCE), []);
  assert.deepEqual(buildNotifications(catalog, [], null), [],
    "a client with no watermark yet starts from now rather than from the beginning");
});

test("an unfollowed run still announces what arrived before it was unfollowed", () => {
  // Unfollowing is about future work; it does not retract the comics that
  // turned up. The failure guard above is the opposite case, and stands.
  const catalog = { requests: [{ id: 20, title: "Paper Girls", status: "cancelled",
    jobs: [imported(1, "1", "2026-09-09T12:05:00Z"), imported(2, "2", "2026-09-09T12:06:00Z")] }] };
  const [item] = buildNotifications(catalog, [], SINCE);
  assert.equal(item.title, "Paper Girls — 2 issues added");
});

test("unfollowing a run does not read as thirty comics arriving at once", () => {
  // Cancelling rewrites every job's updatedAt. Reading arrival off that would
  // announce the whole run again at the moment it stopped being followed.
  const catalog = { requests: [{ id: 20, title: "Paper Girls", status: "cancelled", jobs: [
    { id: 1, issueNumber: "1", status: "cancelled", downloadStatus: "imported",
      importedAt: "2026-09-01T00:00:00Z", updatedAt: "2026-09-09T12:20:00Z" },
  ] }] };
  assert.deepEqual(buildNotifications(catalog, [], SINCE), []);
});

test("arrivals sort below anything that needs a decision", () => {
  const catalog = {
    requests: [{ id: 20, title: "Paper Girls", status: "fulfilled",
      jobs: [imported(1, "1", "2026-09-09T12:05:00Z"), imported(2, "2", "2026-09-09T12:06:00Z"),
             { id: 3, status: "failed", issueNumber: "3" }] }],
    inbox: [{ id: "m", category: "metadata", severity: "warning", issue: "Uncertain", file: "a.cbz" }],
  };
  assert.deepEqual(buildNotifications(catalog, [], SINCE).map((n) => n.kind),
    ["download", "metadata", "acquired"]);
});

test("dismissing an arrival keeps it dismissed once the watermark moves past it", () => {
  // The prune drops ids the live set no longer contains, and an arrival stops
  // being generated the moment the watermark advances -- so without the
  // exception it would forget the dismissal and announce itself again.
  const catalog = { requests: [{ id: 20, title: "Paper Girls", status: "fulfilled",
    jobs: [imported(1, "1", "2026-09-09T12:05:00Z")] }] };
  assert.deepEqual(
    pruneDismissed(catalog, ["acquired:job:1:2026-09-09T12:05:00Z"]),
    ["acquired:job:1:2026-09-09T12:05:00Z"],
  );
});

test("the watermark round-trips and survives hostile storage", () => {
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  assert.equal(readSeenUntil(storage), null);
  writeSeenUntil(storage, SINCE);
  assert.equal(readSeenUntil(storage), SINCE);
  assert.equal(store.get(SEEN_UNTIL_KEY), SINCE);
  const hostile = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.equal(readSeenUntil(hostile), null);
  assert.doesNotThrow(() => writeSeenUntil(hostile, SINCE));
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
  const items = buildNotifications(catalog);
  assert.equal(items.length, 1, "a rate limit retries on its own and is not announced");
  assert.equal(items[0].id, "metadata-source:metron");
  assert.equal(items[0].title, "Metron rejected its saved login");
  assert.equal(items[0].view, "settings");
  assert.deepEqual(items[0].focus, { section: "metadata" });
});

test("series left without a match are announced once checking has finished", () => {
  const running = { enrichment: { active: 2, review: 1, failed: 1 } };
  assert.deepEqual(buildNotifications(running), [], "a count that is still climbing is not announced");
  const settled = { enrichment: { active: 0, review: 2, failed: 1 } };
  const [item] = buildNotifications(settled);
  assert.equal(item.id, "metadata-match:2:1");
  assert.equal(item.kind, "metadata");
  assert.equal(item.title, "3 series need a match");
  assert.equal(item.detail, "2 to review · 1 found no match");
  assert.equal(item.view, "metadata");
  assert.deepEqual(buildNotifications({ enrichment: { active: 0, review: 0, failed: 0, complete: 34 } }), [], "all matched is not news");
});

test("a stopped metadata source sorts between a failed download and a damaged file", () => {
  const catalog = {
    requests: [{ id: 1, jobs: [failedJob(3)] }],
    inbox: [{ id: "f", category: "file", severity: "error", issue: "Damaged", file: "b.cbz" }],
    enrichment: { active: 0, review: 1, failed: 0, providerCooldowns: [{ provider: "gcd", error: "authentication failed" }] },
  };
  assert.deepEqual(buildNotifications(catalog).map((item) => item.kind), ["download", "source", "file", "metadata"]);
});
