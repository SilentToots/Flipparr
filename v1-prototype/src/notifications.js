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

export const DISMISSED_KEY = "flipparr.notifications.dismissed";

function requestJobNotifications(requests, kind) {
  return (requests || []).flatMap((request) =>
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

// Failures first: a stopped download and a damaged file are both blocking,
// where an uncertain match is a judgement call that can wait.
const RANK = { download: 0, file: 1, metadata: 2 };

export function buildNotifications(catalog, dismissed = []) {
  const hidden = new Set(dismissed);
  return [
    ...requestJobNotifications(catalog?.requests, "series"),
    ...requestJobNotifications(catalog?.replacementRequests, "replacement"),
    ...inboxNotifications(catalog?.inbox),
  ]
    .filter((item) => !hidden.has(item.id))
    .sort((a, b) => RANK[a.kind] - RANK[b.kind]);
}

// A dismissal outlives the thing it dismissed unless it is pruned, and the ids
// are reused when a job is retried -- so a stale entry would silence a real
// future failure.
export function pruneDismissed(catalog, dismissed = []) {
  const live = new Set(buildNotifications(catalog, []).map((item) => item.id));
  return dismissed.filter((id) => live.has(id));
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
