// Readers' requests on the client: what a request is called, where one stands,
// and what it announces. The server keeps them (`memberRequests` in the
// catalog: everyone's for the admin, a reader's own for them); this is the
// wording and the bookkeeping, tested away from the JSX.

// The same keys the server gives a request (app.member_request_spec), so a
// card can say "Requested" for something asked for on another screen or
// another device.
export function requestKey(kind, target) {
  if (kind === "run") return `run:${target.seriesId}`;
  if (kind === "collection") return `collection:${target.collectionId}`;
  const run = `discover:${target.provider}:${target.providerSeriesId}`;
  if (kind === "discover_run") return run;
  if (target.released) return `${run}#released`;
  return `${run}#${[...new Set((target.numbers || []).map(String))].sort().join(",")}`;
}

/** Keys of what is still waiting: pending, or failed and waiting on the admin again. */
export function waitingKeys(requests) {
  return new Set((requests || [])
    .filter((request) => request.status === "pending" || request.status === "failed")
    .map((request) => request.targetKey));
}

/** Whether a Discover run has any request waiting -- the run itself or some of its issues. */
export function runRequested(waiting, provider, providerSeriesId) {
  const run = `discover:${provider}:${providerSeriesId}`;
  for (const key of waiting) if (key === run || key.startsWith(`${run}#`)) return true;
  return false;
}

// Where a request stands, in the reader's words. A failed approval is the
// admin's to retry, so the reader is only told it is still waiting.
export const REQUEST_STATE_LABELS = {
  pending: "Waiting for approval",
  failed: "Waiting for approval",
  approved: "Approved",
  available: "In your library",
  declined: "Declined",
  cancelled: "Cancelled",
};

// The admin's words for the same states: a failure is theirs to act on.
export const ADMIN_STATE_LABELS = { ...REQUEST_STATE_LABELS, pending: "Waiting", failed: "Couldn't be done" };

/**
 * Whether a request asks for a follow -- the run (or collection) kept up
 * with, new issues and all -- rather than a pull of particular issues.
 */
export function isFollow(request) {
  return ["run", "collection", "discover_run"].includes(request?.kind);
}

/** What was asked for, in a line: "Issues 1, 2 and 5", "Follow the run". */
export function requestScope(request) {
  const numbers = request?.detail?.numbers || request?.params?.numbers || [];
  if (request?.kind === "discover_issues") {
    if (request.params?.released) return "Every released issue";
    if (numbers.length === 1) return `Issue ${numbers[0]}`;
    const shown = numbers.slice(0, 4);
    const more = numbers.length - shown.length;
    return `Issues ${shown.join(", ")}${more > 0 ? ` and ${more} more` : ""}`;
  }
  if (request?.kind === "collection") return "Follow the collection";
  return "Follow the run";
}

/** The admin's queue: waiting first (oldest first, as asked), then the rest, newest first. */
export function adminQueue(requests) {
  const all = requests || [];
  const waiting = all.filter((request) => request.status === "pending" || request.status === "failed")
    .sort((a, b) => String(a.createdAt).localeCompare(String(b.createdAt)));
  const decided = all.filter((request) => !(request.status === "pending" || request.status === "failed"));
  return { waiting, decided };
}

/**
 * What requests announce in the bell. For the admin, one line while anything
 * is waiting (keyed on the newest, so a new request is news again after a
 * dismissal). For a reader, each decision since this device started looking.
 */
export function requestNotifications(requests, { admin = false, since = null } = {}) {
  const all = requests || [];
  if (admin) {
    const waiting = all.filter((request) => request.status === "pending");
    if (!waiting.length) return [];
    const newest = waiting.reduce((a, b) => (Number(b.id) > Number(a.id) ? b : a));
    const who = [...new Set(waiting.map((request) => request.requestedBy?.name).filter(Boolean))];
    return [{
      id: `requests-waiting:${newest.id}`,
      kind: "request",
      severity: "warning",
      title: waiting.length === 1
        ? `${newest.requestedBy?.name || "A reader"} asked ${isFollow(newest) ? "to follow" : "for"} ${newest.title}`
        : `${waiting.length} requests waiting`,
      detail: waiting.length === 1 ? (isFollow(newest) ? "New issues as they come out" : requestScope(newest))
        : who.length === 1 ? `From ${who[0]}` : `From ${who.slice(0, -1).join(", ")} and ${who.at(-1)}`,
      view: "requests",
      focus: { tab: "asks" },
    }];
  }
  const cutoff = since || "";
  return all.flatMap((request) => {
    const state = request.state;
    if (!["approved", "available", "declined"].includes(state)) return [];
    const at = state === "available" ? request.updatedAt : request.decidedAt;
    if (!at || String(at) <= cutoff) return [];
    return [{
      id: `request:${request.id}:${state}`,
      // News, not work: nothing here waits on the reader.
      news: true,
      kind: state === "available" ? "acquired" : "request",
      severity: state === "declined" ? "warning" : "info",
      // A follow approved is a promise about the future, not one download.
      title: state === "available" ? `${request.title} is in your library`
        : state === "approved" ? (isFollow(request) ? `You'll get ${request.title}'s new issues` : `${request.title} was approved`)
        : `${request.title} was declined`,
      detail: state === "declined" ? (request.declineReason || "No reason given")
        : state === "approved" ? (isFollow(request) ? "Followed · each issue arrives as it comes out" : "It will arrive once it's downloaded")
        : requestScope(request),
      view: "requests",
      focus: { requestId: request.id },
    }];
  });
}
