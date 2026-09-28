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
  if (kind === "discover_arc") return `discover:arc:metron:${target.arcId}`;
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
  if (request?.kind === "discover_arc") {
    const count = request.detail?.issueCount;
    return count ? `Story arc · ${count} issue${count === 1 ? "" : "s"}` : "Story arc";
  }
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
 * What readers' requests need of the admin, for the bell: one line while
 * anything is waiting (keyed on the newest, so a new request is news again
 * after a dismissal), and one for each approval that could not be carried
 * out. A reader hears how their requests went from the server's own record
 * (`/api/v1/notifications`), which follows them to every device.
 */
export function requestNotifications(requests, { admin = false } = {}) {
  if (!admin) return [];
  const all = requests || [];
  const waiting = all.filter((request) => request.status === "pending");
  const failed = all.filter((request) => request.status === "failed").map((request) => ({
    id: `request-failed:${request.id}:${request.updatedAt || ""}`,
    kind: "request",
    severity: "error",
    title: `Couldn't do ${request.requestedBy?.name || "a reader"}'s request for ${request.title}`,
    detail: request.failure || "Approve it again once the problem is fixed",
    view: "requests",
    focus: { tab: "asks", requestId: request.id },
  }));
  if (!waiting.length) return failed;
  const newest = waiting.reduce((a, b) => (Number(b.id) > Number(a.id) ? b : a));
  const who = [...new Set(waiting.map((request) => request.requestedBy?.name).filter(Boolean))];
  // Keyed on the whole set waiting: keyed on the newest alone, deciding it
  // brought back a line the admin had dismissed for the older ones.
  const key = waiting.map((request) => Number(request.id)).sort((a, b) => a - b).join(",");
  return [...failed, {
    id: `requests-waiting:${key}`,
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

// ---- Deciding by swipe --------------------------------------------------------

/** How long a swiped decision waits for Undo before it is sent. */
export const UNDO_MS = 5000;

/**
 * What a released drag decides: right approves, left declines, a short or
 * slow drag decides nothing and the card springs back. A third of the card's
 * width is a decision; so is a flick (fast, if shorter).
 */
export function swipeDecision(dx, width, elapsedMs) {
  const distance = Math.abs(dx);
  const speed = distance / Math.max(1, elapsedMs);
  if (distance < 40) return null;
  if (distance < width / 3 && speed < 0.8) return null;
  return dx > 0 ? "approve" : "decline";
}

// Metron's ratings, as the badge colours say them: a parent reads green as
// fine for anyone, red as grown-up. "Not rated" is said, not hidden -- for a
// parent it is information too.
const RATING_TONES = { everyone: "green", cca: "green", teen: "violet", "teen plus": "amber", mature: "red", explicit: "red", adult: "red" };

export function ratingTone(rating) {
  return RATING_TONES[String(rating || "").trim().toLowerCase()] || "muted";
}
