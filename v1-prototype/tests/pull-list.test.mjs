import test from "node:test";
import assert from "node:assert/strict";
import {
  classifyRequest, groupPullList, tabCount, lastArrivalAt, isWorking,
  RECENT_ARRIVAL_DAYS, jobsForTab,
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

test("a run is on a second tab only for issues of its own there", () => {
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

test("a run that has only just been asked for still counts as working", () => {
  // The page polls while something is working. It used to ask only about
  // downloadStatus, which does not exist until a release is grabbed -- so a
  // run added a moment ago sat on "Searching Prowlarr" until something else
  // happened to refresh it, which is exactly when a reader is watching.
  assert.equal(isWorking(req({ jobs: [{ id: 1, status: "queued" }] })), true);
  assert.equal(isWorking(req({ jobs: [{ id: 1, status: "searching" }] })), true);
  assert.equal(isWorking(req({ jobs: [{ id: 1, status: "grabbed" }] })), true);
  assert.equal(isWorking(req({ jobs: [{ id: 1, downloadStatus: "downloading" }] })), true);
  assert.equal(isWorking(req({ jobs: [{ id: 1, downloadStatus: "importing" }] })), true);
});

test("nothing left to do stops the polling", () => {
  // A failed job is waiting on a person, not on the system, and a run with
  // nothing outstanding must not keep the page fetching forever.
  assert.equal(isWorking(req({ jobs: [{ id: 1, status: "failed" }] })), false);
  assert.equal(isWorking(req({ jobs: [arrived(1, daysAgo(1))] })), false);
  assert.equal(isWorking(req({ jobs: [] })), false);
  assert.equal(isWorking(null), false);
});

test("a comic pulled before it ships waits on Wanted, not on no tab at all", () => {
  // No job exists until release, so there was nothing to search for and the
  // request fell through every bucket -- pulled, and nowhere to be seen.
  const request = req({ coverage: "issues", upcomingIssueCount: 1, wantedIssueCount: 0 });
  assert.equal(classifyRequest(request, NOW), "wanted");
});

test("a followed run waiting on unreleased issues is still not work", () => {
  // The pulled-issue rule must not sweep followed runs onto Wanted.
  const request = req({ coverage: "run", upcomingIssueCount: 3, wantedIssueCount: 0 });
  assert.equal(classifyRequest(request, NOW), null);
});

// American Vampire, a finished run: #19 failed, #20 downloading, and #21 has
// had no release found for it yet.
const vampire = () => req({ id: 36, wantedIssueCount: 2, jobs: [
  { id: 1, issueNumber: "18", status: "fulfilled", downloadStatus: "imported", importedAt: daysAgo(1) },
  { id: 2, issueNumber: "19", status: "failed", downloadStatus: "failed" },
  { id: 3, issueNumber: "20", status: "grabbed", downloadStatus: "downloading" },
  { id: 4, issueNumber: "21", status: "queued" },
] });

test("a run is on every tab it has an issue for, so one failure hides nothing", () => {
  // #19 failing put the whole run on Failed, and #21 -- still to find -- was
  // on no tab anywhere.
  const buckets = groupPullList({ requests: [vampire()] }, NOW);
  assert.deepEqual(ids(buckets.failed), [36]);
  assert.deepEqual(ids(buckets.downloading), [36]);
  assert.deepEqual(ids(buckets.wanted), [36]);
  assert.deepEqual(ids(buckets.acquired), [], "arrivals only count when nothing is outstanding");
  assert.equal(classifyRequest(vampire(), NOW), "failed", "the most urgent tab still comes first");
});

test("each tab lists only its own issues and says where the rest are", () => {
  const failed = jobsForTab(vampire(), "failed");
  assert.deepEqual(failed.jobs.map((job) => job.issueNumber), ["19"]);
  assert.deepEqual(failed.elsewhere, { downloading: 1, wanted: 1 });
  assert.deepEqual(jobsForTab(vampire(), "downloading").jobs.map((job) => job.issueNumber), ["20"]);
  const wanted = jobsForTab(vampire(), "wanted");
  assert.deepEqual(wanted.jobs.map((job) => job.issueNumber), ["21"]);
  assert.deepEqual(wanted.elsewhere, { failed: 1, downloading: 1 });
  assert.deepEqual(jobsForTab(vampire(), "acquired").jobs.map((job) => job.issueNumber), ["19", "20", "21"]);
  assert.deepEqual(jobsForTab(null, "failed"), { jobs: [], elsewhere: {} });
});

test("a run whose only open issue is failing is not also wanted", () => {
  const request = req({ wantedIssueCount: 1, jobs: [{ id: 1, status: "failed", downloadStatus: "failed" }] });
  assert.deepEqual(groupPullList({ requests: [request] }, NOW).wanted, []);
});

test("an empty release search says what came back and why none was offered", async () => {
  const { releaseSearchSummary } = await import("../src/pull-list.js");
  // Supergirl: Woman of Tomorrow #2: fifty-one results, the only #2 refused.
  const summary = releaseSearchSummary({
    resultCount: 51,
    nearMisses: [
      { title: "Supergirl - Woman of Tomorrow 02 (of 08) (2021)", score: null, copies: 3,
        reasons: ["Refused before: The download is incomplete: 22 of its 140 MB are zero-filled"] },
      { title: "Supergirl - Woman of Tomorrow 03 (of 08) (2021)", score: 65,
        reasons: ["Series title matches", "Publication year 2021 matches"] },
      { title: "Comics.FR.-.Supergirl.002", score: 0, reasons: ["Labelled as French"] },
      { title: "Week of 2022.02.16 - Batman 002", score: 40, reasons: ["Issue #2 matches"] },
    ],
  }, "#2");
  assert.equal(summary.headline, "51 results, none a usable #2");
  assert.deepEqual(summary.setAside.map((item) => [item.reason, item.copies]), [
    ["Refused before: The download is incomplete: 22 of its 140 MB are zero-filled", 3],
    ["Not this issue", 1],
    ["Labelled as French", 1],
    ["Not this series", 1],
  ]);
  assert.equal(releaseSearchSummary({ resultCount: 0 }, "#2").headline, "Nothing came back");
  assert.equal(releaseSearchSummary(null, "#2").setAside.length, 0);
});

test("a set-aside row keeps the id it can be taken by, and the server's wording", async () => {
  const { releaseSearchSummary, takeAnywayCopy } = await import("../src/pull-list.js");
  const summary = releaseSearchSummary({
    resultCount: 2,
    nearMisses: [
      { id: "aside-1", title: "009-Flashpoint -Secret Seven 01 2011", score: 50, reasons: ["Issue #1 matches"],
        reason: "Not this series", source: "usenet", setAside: true, refused: false },
      { id: "aside-2", title: "Flashpoint - Secret Seven 001 (2011)", score: null, reasons: ["Refused before: Aborted"],
        reason: "Refused before: Aborted", source: "usenet" },
      { id: "gc-1", title: "Flashpoint Vol. 1 (Collection)", score: 10, reasons: [], reason: "Not this issue",
        source: "direct_site", grabbable: false },
    ],
  }, "#1");
  assert.deepEqual(summary.setAside.map((item) => [item.id, item.reason, item.refused, item.grabbable, item.source]), [
    ["aside-1", "Not this series", false, true, "usenet"],
    ["aside-2", "Refused before: Aborted", true, true, "usenet"],
    ["gc-1", "Not this issue", false, false, "direct_site"],
  ]);
  assert.equal(takeAnywayCopy(summary.setAside[0], "Flashpoint: Secret Seven", "1"),
    "Set aside: Not this series. Taking it imports it as Flashpoint: Secret Seven #1. The file still has to be a readable comic.");
  assert.equal(takeAnywayCopy(summary.setAside[1], "Flashpoint: Secret Seven", "1"),
    "Refused before: Aborted. Taking it tries this release again and imports it as Flashpoint: Secret Seven #1. The file still has to be a readable comic.");
  // DirectSite alone: nothing from Usenet, still something to show.
  const only = releaseSearchSummary({ resultCount: 0, nearMisses: [{ id: "gc-1", title: "X", score: 10, reason: "Not this issue" }] }, "#1");
  assert.equal(only.headline, "Nothing usable came back for #1");
  assert.equal(only.setAside.length, 1);
});

test("a release says which client takes it and how it travels", async () => {
  const { releaseSendLabel, releaseTransport, downloadStateLabel } = await import("../src/pull-list.js");
  assert.equal(releaseSendLabel({ source: "usenet" }), "Send to SABnzbd");
  assert.equal(releaseSendLabel({ source: "torrent" }), "Send to qBittorrent");
  assert.equal(releaseSendLabel({ source: "direct_site" }), "Download");
  assert.equal(releaseTransport({ source: "usenet", protocol: "Usenet" }), "Usenet");
  assert.equal(releaseTransport({ source: "torrent", protocol: "Torrent", seeders: 12 }), "Torrent · 12 seeders");
  assert.equal(releaseTransport({ source: "torrent", protocol: "Torrent", seeders: 1 }), "Torrent · 1 seeder");
  assert.equal(releaseTransport({ source: "torrent", protocol: "Torrent", seeders: 0 }), "Torrent · no seeders");
  const labels = { queued: "Waiting for SABnzbd", downloading: "Downloading" };
  assert.equal(downloadStateLabel({ downloadStatus: "queued", downloadSource: "qbittorrent" }, labels), "Waiting for its file list");
  assert.equal(downloadStateLabel({ downloadStatus: "queued", downloadSource: "sabnzbd" }, labels), "Waiting for SABnzbd");
  assert.equal(downloadStateLabel({ downloadStatus: "downloading", downloadSource: "qbittorrent" }, labels), "Downloading");
  assert.equal(downloadStateLabel({}, labels), undefined);
});
