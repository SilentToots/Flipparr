// Which tab a request belongs on.
//
// The old four tabs did not describe four states. "Following" was a monitoring
// property pretending to be a location, so a run could be conceptually in two
// places at once; "Acquired" silently meant replacements only, so a run you
// added and downloaded landed under Following and looked lost; and nothing at
// all showed what was downloading right now -- progress lived inside a
// collapsed row. Sonarr keeps monitoring on the series and splits activity
// into what is in flight and what happened, which is the shape this follows.
//
// A request lands on every tab it has issues for, showing only those issues
// there, or on none. It used to land on one -- the most urgent -- and so a run
// with one failed issue hid every other one: American Vampire #21, which no
// search had found a release for yet, was on no tab at all because #19 had
// failed. None is
// still a real answer: a run that is fully acquired and has nothing
// outstanding is not activity, and belongs on the Comics grid where its
// ownership already shows.

import { jobHasFailed } from "./nav-counts.js";

// A download SABnzbd has taken but not finished with, or a job that is out
// looking. `downloadStatus` is absent until a release is grabbed, so the job's
// own status carries the early part of the journey.
const IN_FLIGHT_DOWNLOADS = new Set([
  "queued", "downloading", "completed", "importing", "waiting_for_files",
]);
const IN_FLIGHT_JOBS = new Set(["searching", "grabbed"]);

export const RECENT_ARRIVAL_DAYS = 30;

const jobs = (request) => request?.jobs || [];

export const hasFailure = (request) => jobs(request).some(jobHasFailed);

const settled = (job) => ["fulfilled", "cancelled"].includes(job.status);

/** Out searching, or somewhere between SABnzbd and the library. */
const jobInFlight = (job) =>
  (job.downloadStatus && IN_FLIGHT_DOWNLOADS.has(job.downloadStatus))
  || (!job.downloadStatus && IN_FLIGHT_JOBS.has(job.status));

/** Still to be found: not here, not failed, and nothing under way for it. */
const jobWanted = (job) => !settled(job) && !jobHasFailed(job) && !jobInFlight(job);

export const isDownloading = (request) => jobs(request).some(jobInFlight);

/**
 * Work that will change the screen on its own, so the page should keep looking.
 *
 * Not the same question as "is it downloading". A run added a moment ago has
 * jobs that are `queued`, then `searching`, and neither has a `downloadStatus`
 * at all -- that only appears once a release is grabbed. Watching
 * downloadStatus alone meant a new run sat on "Searching Prowlarr" until
 * something else happened to refresh the page, which is exactly the stretch
 * where a reader is watching it.
 */
export const isWorking = (request) =>
  isDownloading(request) || jobs(request).some((job) => job.status === "queued");

// What the old Wanted tab meant: something to look for now. A followed run
// whose only remaining issues are unpublished has nothing to search for.
export const hasSomethingToFind = (request) => Boolean(
  (request?.wantedIssueCount || 0)
  || (request?.queuedJobCount || 0)
  || jobs(request).some((job) => !["fulfilled", "cancelled"].includes(job.status))
);

/**
 * Whether one issue of a pull can be deleted. Not while SABnzbd holds it or a
 * search is out: the job is what imports the finished file, and deleting it
 * would leave the download to land with nothing to claim it. The server
 * refuses the same cases; this keeps the button from offering them.
 */
export const canDeleteJob = (job) => Boolean(job)
  && !(job.downloadStatus && IN_FLIGHT_DOWNLOADS.has(job.downloadStatus))
  && job.status !== "searching";

const TAB_JOBS = { failed: jobHasFailed, downloading: jobInFlight, wanted: jobWanted };

/**
 * The issues a row lists on a tab, and where the rest of the run's are.
 *
 * Each outstanding issue belongs to one tab. Listing all of them on every tab
 * made an issue still to find read as failed beside one that had --
 * American Vampire #21 beside #19. `elsewhere` counts the outstanding issues
 * on the other tabs, so the row can say where they went. Acquired, and any
 * tab not named, lists everything outstanding.
 */
export function jobsForTab(request, tab) {
  const outstanding = jobs(request).filter((job) => !settled(job));
  const belongs = TAB_JOBS[tab];
  if (!belongs) return { jobs: outstanding, elsewhere: {} };
  const elsewhere = {};
  for (const [other, test] of Object.entries(TAB_JOBS)) {
    if (other === tab) continue;
    const count = outstanding.filter(test).length;
    if (count) elsewhere[other] = count;
  }
  return { jobs: outstanding.filter(belongs), elsewhere };
}

/**
 * What an empty release search found, said plainly.
 *
 * "Prowlarr returned no Usenet results" was shown whether the indexers had
 * nothing at all or had answered with fifty-one releases -- every other issue
 * of Supergirl: Woman of Tomorrow, and the one #2 refused as incomplete.
 */
export function releaseSearchSummary(result, label) {
  const returned = Number(result?.resultCount || 0);
  if (!returned) {
    return { headline: "Nothing came back", setAside: [],
      detail: "Your indexers returned no Usenet results for this search. Try other wording." };
  }
  const setAside = (result?.nearMisses || []).map((miss) => ({
    title: miss.title, copies: miss.copies || 1, reason: setAsideReason(miss),
  }));
  return {
    headline: `${returned} result${returned === 1 ? "" : "s"}, none a usable ${label}`,
    detail: setAside.length ? "The closest, and why each was set aside:" : "None of them named this series and issue.",
    setAside,
  };
}

/** Why one release was not offered: its refusal, or what it failed to match. */
function setAsideReason(miss) {
  const reasons = miss?.reasons || [];
  // Refused before, or refused outright (a foreign edition, a wrong year).
  if (miss?.score == null || (miss.score === 0 && reasons.length)) return reasons[0] || "Set aside";
  const series = reasons.some((reason) => reason.startsWith("Series title matches"));
  const issue = reasons.some((reason) => /^Issue #.+ matches$/.test(reason));
  if (!series && !issue) return "Not this series or issue";
  if (!series) return "Not this series";
  if (!issue) return "Not this issue";
  return `Too weak a match (${miss.score} of the 85 needed)`;
}

/** Only a pull by name is deleted; a followed run is stopped by unfollowing. */
export const canDeletePull = (request) =>
  request?.coverage === "issues" && jobs(request).every(canDeleteJob);

/**
 * Issues pulled by name that have no job yet. Jobs exist only for released
 * issues, so a comic pulled before it ships has nothing in `jobs` -- and a
 * panel that lists jobs alone could not show it, let alone delete it.
 */
export function waitingIssues(request) {
  if (request?.coverage !== "issues") return [];
  const withJob = new Set(jobs(request).map((job) => String(job.issueId)));
  return (request.issues || []).filter((issue) =>
    !withJob.has(String(issue.id)) && issue.ownership === "unowned");
}

/**
 * When one issue landed. `importedAt` is the moment the file arrived;
 * `updatedAt` is the last time anything touched the job, which unfollowing a
 * run rewrites for all of them at once. Prefer the former and fall back only
 * for rows imported before that column existed.
 */
export const arrivalAt = (job) =>
  (job.downloadStatus === "imported" && (job.importedAt || job.updatedAt)) || null;

/** When this request's most recent issue landed, or null if none has. */
export function lastArrivalAt(request) {
  const arrivals = jobs(request).map(arrivalAt).filter(Boolean).sort();
  return arrivals.length ? arrivals.at(-1) : null;
}

/**
 * Every tab this request has issues on, most urgent first; empty for none.
 *
 * A run can be several of these at once -- one issue failed, another
 * downloading, another still to find -- and each of those issues is listed on
 * its own tab. An issue is only ever on one.
 */
export function requestTabs(request, now = new Date()) {
  if (!request) return [];
  // An unfollowed run has nothing in progress and nothing waiting on anyone,
  // so it is not Wanted, Downloading or Failed. It can still be Acquired:
  // "what turned up lately" is a matter of record, and unfollowing a run an
  // hour after thirty issues landed should not erase that they landed.
  const live = request.status !== "cancelled";
  const tabs = [];
  if (live && hasFailure(request)) tabs.push("failed");
  if (live && isDownloading(request)) tabs.push("downloading");
  if (request.status === "open") {
    // An issue with a job is wanted when nothing is under way for it. One
    // without a job yet counts only while nothing else is outstanding: a comic
    // pulled by name is wanted from the moment it is asked for, even before it
    // ships. A followed run with only unreleased issues left is different --
    // it is just being watched, and it lives on Comics.
    const outstanding = jobs(request).filter((job) => !settled(job));
    if (jobs(request).some(jobWanted) || (!outstanding.length
      && (hasSomethingToFind(request) || request.coverage === "issues"))) tabs.push("wanted");
  }
  if (tabs.length) return tabs;
  const arrived = lastArrivalAt(request);
  if (!arrived) return [];
  const cutoff = new Date(now.getTime() - RECENT_ARRIVAL_DAYS * 24 * 60 * 60 * 1000);
  return Date.parse(arrived) >= cutoff.getTime() ? ["acquired"] : [];
}

/** The most urgent tab this request is on, or null for none. */
export const classifyRequest = (request, now = new Date()) => requestTabs(request, now)[0] ?? null;

/**
 * Both request kinds, split into the four tabs, each already in the order it
 * should be read.
 *
 * A bucket is one list rather than a list per kind, because the order is the
 * answer and a screen that draws every replacement and then every run cannot
 * honour it -- Acquired promises "newest first" and would open on whatever
 * happens to be first in the smaller list. `kind` says which row to draw.
 *
 * Acquired sorts by arrival rather than by title: it answers "what turned up
 * lately", and the newest thing is the answer. The other three keep the
 * catalog's own order, which is by request age.
 */
export function groupPullList(catalog, now = new Date()) {
  const buckets = { wanted: [], downloading: [], acquired: [], failed: [] };
  const place = (request, kind) => {
    for (const tab of requestTabs(request, now)) buckets[tab].push({ kind, request });
  };
  for (const request of catalog?.requests || []) place(request, "series");
  for (const request of catalog?.replacementRequests || []) place(request, "replacement");
  buckets.acquired.sort((a, b) => String(lastArrivalAt(b.request) || "")
    .localeCompare(String(lastArrivalAt(a.request) || "")));
  return buckets;
}

export const tabCount = (bucket) => bucket.length;
