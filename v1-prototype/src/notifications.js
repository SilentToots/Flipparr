import { requestNotifications } from "./member-requests.js";

// Everything waiting on the reader, in one list, each knowing where it goes.
//
// The bell used to show `catalog.inbox` alone, so a download that failed --
// the one state that stops until a person acts -- appeared on Pull List and
// nowhere else. A reader watching the bell would never learn about it.
//
// Every entry carries a destination, because a notification that cannot be
// acted on is just an alarm: `view` is the screen and `focus` is the thing on
// it, which is what the app's navigate/focus plumbing already takes.

import { jobHasFailed } from "./nav-counts.js";
import { arrivalAt } from "./pull-list.js";

export const DISMISSED_KEY = "flipparr.notifications.dismissed";
export const SEEN_UNTIL_KEY = "flipparr.notifications.seenUntil";

function requestJobNotifications(requests, kind) {
  return (requests || [])
    .filter((request) => request?.status !== "cancelled")
    .flatMap((request) =>
    (request?.jobs || []).filter(jobHasFailed).map((job) => ({
      id: `job:${job.id}`,
      kind: "download",
      severity: "error",
      title: `Download failed · ${job.issueTitle || `Issue ${job.issueNumber}`}`,
      detail: request.seriesTitle || request.targetTitle || request.filename || "Pull List",
      // Both request kinds land on Pull List; the tab differs because a
      // replacement lives under Wanted only until it is fulfilled.
      view: "requests",
      focus: { kind, requestId: request.id, jobId: job.id },
    })));
}

function inboxNotifications(inbox) {
  return (inbox || []).map((item) => ({
    id: `inbox:${item.id}`,
    kind: item.category === "file" ? "file" : "metadata",
    severity: item.severity === "error" ? "error" : "warning",
    title: item.issue,
    detail: item.file,
    view: "metadata",
    focus: { id: item.id },
  }));
}

// Comics arriving. The bell reported only bad news, so the one thing a
// downloader most wants to be told -- your comics are here -- was the one
// thing it never said.
//
// At both levels, because a batch and a single arrival are different events:
// adding a run brings thirty issues at once and wants one line about the run,
// while a followed run picking up its next issue wants to name that issue. The
// only test needed to tell them apart is whether more than one arrived.
//
// `since` is a high-water mark, not a filter on age: without it, shipping this
// would announce every issue ever imported, all at once. A client that has
// never stored one is starting from now and announces nothing historical.
function acquisitionNotifications(catalog, since) {
  if (!since) return [];
  return (catalog?.requests || []).flatMap((request) => {
    // Unlike a failure, an arrival is not work waiting on anyone, so
    // unfollowing the run afterwards does not make it untrue. The times come
    // from `arrivalAt` -- the moment the file landed -- precisely so that
    // unfollowing, which rewrites every job, cannot look like fresh arrivals.
    const arrived = (request.jobs || []).filter((job) => (arrivalAt(job) || "") > since);
    if (!arrived.length) return [];
    const at = arrived.map(arrivalAt).sort().at(-1);
    const runTitle = request.title || arrived[0].seriesTitle || "A run";
    if (arrived.length === 1) {
      const job = arrived[0];
      return [{
        id: `acquired:job:${job.id}:${arrivalAt(job)}`,
        kind: "acquired",
        severity: "info",
        title: `${runTitle} #${job.issueNumber} added`,
        detail: job.issueTitle || "Downloaded and added to your library",
        view: "requests",
        focus: { kind: "series", requestId: request.id, jobId: job.id },
      }];
    }
    return [{
      // Keyed on the moment as well as the run, so a run added to again later
      // announces that too rather than being deduped against an old dismissal.
      id: `acquired:run:${request.id}:${at}`,
      kind: "acquired",
      severity: "info",
      title: `${runTitle} — ${arrived.length} issues added`,
      detail: request.publisher || "Downloaded and added to your library",
      view: "requests",
      focus: { kind: "series", requestId: request.id },
    }];
  });
}

export const METADATA_PROVIDER_LABELS = {
  gcd: "Grand Comics Database",
  metron: "Metron",
  comic_vine: "Comic Vine",
};

const CREDENTIAL_ERROR = /(?:401|unauthori[sz]ed|authentication|credentials?)/i;

// What the dashboard's metadata banner used to say, reduced to the parts a
// person has to act on. Progress ("checking 18 of 34") and "details are
// ready" were never work for anyone, so they are not here.
//
// A source that rejected its saved login stops until someone reconnects it; a
// source that is only rate-limited retries on its own and says nothing.
// Series left needing a match are counted once the checking has finished --
// while it runs the count climbs, and a row keyed on it would reappear after
// every dismissal.
function enrichmentNotifications(enrichment) {
  if (!enrichment) return [];
  const rejected = (enrichment.providerCooldowns || [])
    .filter((cooldown) => CREDENTIAL_ERROR.test(cooldown?.error || ""))
    .map((cooldown) => ({
      id: `metadata-source:${cooldown.provider}`,
      kind: "source",
      severity: "error",
      title: `${METADATA_PROVIDER_LABELS[cooldown.provider] || cooldown.provider || "A metadata source"} rejected its saved login`,
      detail: "Reconnect it in Metadata sources",
      view: "settings",
      focus: { section: "metadata" },
    }));
  const review = Number(enrichment.review || 0);
  const failed = Number(enrichment.failed || 0);
  if (!(review + failed) || Number(enrichment.active || 0) > 0) return rejected;
  const count = review + failed;
  return [...rejected, {
    // Keyed on the counts, so a later check that leaves different series
    // unmatched is announced again rather than matched against an old dismissal.
    id: `metadata-match:${review}:${failed}`,
    kind: "metadata",
    severity: "warning",
    title: `${count} series ${count === 1 ? "needs" : "need"} a match`,
    detail: [review ? `${review} to review` : null, failed ? `${failed} found no match` : null].filter(Boolean).join(" · "),
    view: "metadata",
    focus: {},
  }];
}

// Failures first: a stopped download, a source that stopped, and a damaged
// file are all blocking; an uncertain match is a judgement call that can wait,
// and news that something arrived needs nothing at all.
// A reader's request waiting on the admin is work for the admin; a decision
// is news for the reader.
const RANK = { download: 0, request: 1, source: 2, file: 3, metadata: 4, acquired: 5 };

export function buildNotifications(catalog, dismissed = [], seenUntil = null, { admin = true } = {}) {
  const hidden = new Set(dismissed);
  return [
    ...requestNotifications(catalog?.memberRequests, { admin, since: seenUntil }),
    ...requestJobNotifications(catalog?.requests, "series"),
    ...requestJobNotifications(catalog?.replacementRequests, "replacement"),
    ...enrichmentNotifications(catalog?.enrichment),
    ...inboxNotifications(catalog?.inbox),
    ...acquisitionNotifications(catalog, seenUntil),
  ]
    .filter((item) => !hidden.has(item.id))
    .sort((a, b) => RANK[a.kind] - RANK[b.kind]);
}

// A dismissal outlives the thing it dismissed unless it is pruned, and the ids
// are reused when a job is retried -- so a stale entry would silence a real
// future failure.
export function pruneDismissed(catalog, dismissed = []) {
  const live = new Set(buildNotifications(catalog, [], null).map((item) => item.id));
  // An acquisition announcement stops being generated as soon as the watermark
  // moves past it, so pruning against the live set would forget its dismissal
  // and let it return. Those ids are kept until their run is gone.
  // An arrival announcement stops being generated as soon as the watermark
  // moves past it, so pruning against the live set alone would forget its
  // dismissal and let it come back. They are kept while the run still exists.
  // A reader's decision announcements are the same: kept while they last.
  return dismissed.filter((id) => live.has(id) || id.startsWith("acquired:") || id.startsWith("request:"));
}

export function readSeenUntil(storage) {
  try {
    return storage?.getItem(SEEN_UNTIL_KEY) || null;
  } catch {
    return null;
  }
}

export function writeSeenUntil(storage, value) {
  try {
    storage?.setItem(SEEN_UNTIL_KEY, value);
  } catch {
    /* the watermark is lost; the bell still works */
  }
}

export function readDismissed(storage) {
  try {
    const raw = storage?.getItem(DISMISSED_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((id) => typeof id === "string") : [];
  } catch {
    // Private windows and blocked site data throw on access rather than
    // returning null, and a bell is not worth a blank screen.
    return [];
  }
}

export function writeDismissed(storage, ids) {
  try {
    storage?.setItem(DISMISSED_KEY, JSON.stringify(ids));
  } catch {
    /* same as above: the dismissal is lost, the app is not */
  }
}
