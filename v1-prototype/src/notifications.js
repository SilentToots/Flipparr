import { requestNotifications } from "./member-requests.js";
import { jobHasFailed } from "./nav-counts.js";

// The bell has two halves.
//
// What needs someone -- a download that failed, a source that rejected its
// login, series needing a match, a file that needs attention, readers'
// requests waiting -- is worked out here, from the library as it stands, so it
// goes away by itself when it is dealt with. Each carries a destination,
// because a notification that cannot be acted on is just an alarm: `view` is
// the screen and `focus` the thing on it. Dismissing one is kept on the
// server with the profile (`/api/v1/notifications/dismissed`), so it stays
// dismissed on every device.
//
// What happened -- comics arriving, a request decided -- is news. The server
// keeps it per profile (`/api/v1/notifications`), read and cleared there, so
// it is the same wherever the profile signs in. Before 2026-09-27 both halves
// were built here with their state in the browser: a new device started
// over, and arrivals piled up because the "seen since" mark never moved.

// Where the browser kept dismissals and the arrivals mark before the server
// did. Read once to move a profile's dismissals up, then removed.
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
      // Trying again is the usual answer, and the bell can do it in place.
      retryJobId: job.id,
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
// file are all blocking; an uncertain match is a judgement call that can wait.
// A reader's request waiting on the admin is work for the admin.
const RANK = { download: 0, request: 1, source: 2, file: 3, metadata: 4 };

/**
 * What needs someone, most urgent first, without what they dismissed. The
 * admin's alone: a reader's bell is news about what they asked for, none of
 * it work.
 */
export function needsAttention(catalog, dismissed = [], { admin = true } = {}) {
  if (!admin) return [];
  const hidden = new Set(dismissed);
  return [
    ...requestNotifications(catalog?.memberRequests, { admin: true }),
    ...requestJobNotifications(catalog?.requests, "series"),
    ...requestJobNotifications(catalog?.replacementRequests, "replacement"),
    ...enrichmentNotifications(catalog?.enrichment),
    ...inboxNotifications(catalog?.inbox),
  ]
    .filter((item) => !hidden.has(item.id))
    .sort((a, b) => RANK[a.kind] - RANK[b.kind]);
}

/**
 * Dismissals whose notification is gone. They are forgotten, because the ids
 * are reused -- a retried job fails under the same id -- and a stale one
 * would silence a real future failure.
 */
export function staleDismissals(catalog, dismissed = []) {
  const live = new Set(needsAttention(catalog, []).map((item) => item.id));
  return dismissed.filter((id) => !live.has(id));
}

/** How many things the bell's badge counts: work waiting, and news not yet seen. */
export function bellCount(attention, activity) {
  return (attention?.length || 0) + Number(activity?.unread || 0);
}

/** "Just now", "5m", "3h", "Yesterday", "4d", then the date. */
export function timeAgo(iso, now = new Date()) {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const minutes = Math.floor((now.getTime() - then.getTime()) / 60000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days}d`;
  return then.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(days > 300 ? { year: "numeric" } : {}) });
}

/** The dismissals this browser kept before the server did, to move them up once. */
export function readLegacyDismissed(storage) {
  try {
    const raw = storage?.getItem(DISMISSED_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((id) => typeof id === "string" && !id.startsWith("acquired:") && !id.startsWith("request:")) : [];
  } catch {
    // Private windows and blocked site data throw on access rather than
    // returning null, and a bell is not worth a blank screen.
    return [];
  }
}

/** Forget what the browser kept, once the server has it. */
export function forgetLegacyState(storage) {
  try {
    storage?.removeItem(DISMISSED_KEY);
    storage?.removeItem(SEEN_UNTIL_KEY);
  } catch {
    /* nothing to forget, or nowhere to forget it from */
  }
}
