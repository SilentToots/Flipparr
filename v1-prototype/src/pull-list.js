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
// Every request lands in exactly one bucket, or none. None is a real answer: a
// run that is fully acquired and has nothing outstanding is not activity, and
// belongs on the Comics grid where its ownership already shows.

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

export const isDownloading = (request) => jobs(request).some((job) =>
  (job.downloadStatus && IN_FLIGHT_DOWNLOADS.has(job.downloadStatus))
  || (!job.downloadStatus && IN_FLIGHT_JOBS.has(job.status)));

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

/**
 * The issues a row lists on a tab, and how many more of the run it leaves out.
 *
 * A run goes to Failed when any one of its issues fails, and the rest of it is
 * still on its way. Listed there, an issue still waiting for a release read as
 * failed too -- American Vampire #21 did, beside the #19 that had. So Failed
 * lists the failures and counts the others; every other tab lists all that is
 * outstanding.
 */
export function jobsForTab(request, tab) {
  const outstanding = jobs(request).filter((job) => !["fulfilled", "cancelled"].includes(job.status));
  if (tab !== "failed") return { jobs: outstanding, others: 0 };
  const failed = outstanding.filter(jobHasFailed);
  return { jobs: failed, others: outstanding.length - failed.length };
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
 * The one tab this request belongs on, or null for none.
 *
 * Order is the point: a run can be several of these at once -- half imported,
 * one issue still downloading, another failed -- and the most urgent reading
 * wins, so nothing is ever counted twice.
 */
export function classifyRequest(request, now = new Date()) {
  if (!request) return null;
  // An unfollowed run has nothing in progress and nothing waiting on anyone,
  // so it is not Wanted, Downloading or Failed. It can still be Acquired:
  // "what turned up lately" is a matter of record, and unfollowing a run an
  // hour after thirty issues landed should not erase that they landed.
  const live = request.status !== "cancelled";
  if (live && hasFailure(request)) return "failed";
  if (live && isDownloading(request)) return "downloading";
  // A comic pulled by name is wanted from the moment it is asked for, even
  // before it ships and has nothing to search for. A followed run with only
  // unreleased issues left is different -- it is just being watched, and it
  // lives on Comics. Without this, pulling next week's issue put it on no
  // tab at all.
  if (request.status === "open"
    && (hasSomethingToFind(request) || request.coverage === "issues")) return "wanted";
  const arrived = lastArrivalAt(request);
  if (!arrived) return null;
  const cutoff = new Date(now.getTime() - RECENT_ARRIVAL_DAYS * 24 * 60 * 60 * 1000);
  return Date.parse(arrived) >= cutoff.getTime() ? "acquired" : null;
}

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
    const tab = classifyRequest(request, now);
    if (tab) buckets[tab].push({ kind, request });
  };
  for (const request of catalog?.requests || []) place(request, "series");
  for (const request of catalog?.replacementRequests || []) place(request, "replacement");
  buckets.acquired.sort((a, b) => String(lastArrivalAt(b.request) || "")
    .localeCompare(String(lastArrivalAt(a.request) || "")));
  return buckets;
}

export const tabCount = (bucket) => bucket.length;
