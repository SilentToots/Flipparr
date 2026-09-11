// What Discover shows, decided away from the JSX so it can be tested.
//
// The screen has two states and one question in each: on the landing, what a
// shelf should say while it is still fetching; in search, which results are
// already yours. Both were previously tangled into render, where the only way
// to check them was to look at the screen -- and the Pull List had already
// taught us what that costs on a route whose data the developer's library does
// not happen to contain.

/** Where a run sits once the reader has acted on it, so a card cannot lie. */
export const PULL_STATES = { idle: "idle", pending: "pending", queued: "queued", owned: "owned" };

/**
 * What an issue's Pull button should say.
 *
 * `pulled` is what this session did; the rest comes from the server. A card
 * that stays on "Pull Issue" after a successful pull invites a second click,
 * and a second click is a second acquisition request.
 */
export function pullState(issue, pulled = {}) {
  const local = pulled[issueKey(issue)];
  if (local) return local;
  if (issue?.owned) return PULL_STATES.owned;
  if (issue?.queued || issue?.following) return PULL_STATES.queued;
  return PULL_STATES.idle;
}

/** Stable across a refetch: provider ids are, list positions are not. */
export const issueKey = (issue) =>
  `${issue?.providerSeriesId || "?"}:${issue?.providerIssueId || issue?.number || "?"}`;

// Short enough to stay on one line in a 120px card. A label that wraps makes
// that card taller than its neighbours, and taller than the skeleton that
// stood in for it a moment earlier -- so the row shifts under the reader at
// the moment they are reaching for it. The toast says the longer sentence.
export const PULL_LABELS = {
  idle: "Pull Issue",
  pending: "Pulling…",
  queued: "On Pull List",
  owned: "In library",
};

/**
 * A shelf's state, from what the endpoint returned.
 *
 * "loading" and "empty" are different answers and used to look identical: an
 * empty row. So is "Metron is not connected", which is not a failure of this
 * week at all.
 */
export function shelfState(shelf, available, loading) {
  if (loading) return "loading";
  if (available === false) return "unavailable";
  if (shelf?.error) return "error";
  return (shelf?.issues || []).length ? "ready" : "empty";
}

/**
 * The runs worth offering, and how many were left out because you have them.
 *
 * Library matches are drawn from the local catalog instead, because those are
 * the ones the reader can open. The count is still needed here: "no new runs
 * match" and "all of them are already yours" are different answers, and the
 * second one used to be reported as the first.
 */
export function splitSearchResults(results = []) {
  const runs = Array.isArray(results) ? results : [];
  const fresh = runs.filter((item) => !item.inLibrary);
  return { fresh, ownedCount: runs.length - fresh.length };
}

/**
 * Whether the library half of a search has an answer yet.
 *
 * Before the catalog arrives the match list is empty, and an empty list read
 * as "Nothing in your library matches" -- a false answer, shown on every cold
 * load of a search link until the library turned up. Offline, the demo
 * library is the answer, so that is never "loading".
 */
export function libraryMatchState(catalog, backendStatus, matches = []) {
  if (!catalog && backendStatus !== "offline") return "loading";
  return (matches || []).length ? "ready" : "empty";
}

/** "2 Library Matches" — the count is bold, the label is not. */
export const countLabel = (count, singular, plural = `${singular}es`) =>
  `${count} ${count === 1 ? singular : plural}`;

/**
 * Which providers are still out, for the line under a search in progress.
 *
 * They settle at different times and results merge in as each returns, so a
 * single spinner would be wrong twice: it would hide that some answers are
 * already on screen, and hide which source is the slow one.
 */
export function providerProgress(state) {
  const checked = state?.providersChecked || [];
  const answered = new Set(state?.providersAnswered || []);
  const failed = new Map((state?.fallbacks || []).map((item) => [item.provider, item.error]));
  return checked.map((name) => ({
    name,
    status: failed.has(name) ? "failed" : answered.has(name) ? "answered" : "searching",
    error: failed.get(name) || null,
  }));
}

// ---------------------------------------------------------------------------
// The run drawer
// ---------------------------------------------------------------------------

/** An issue that can still be asked for: not here already, not asked for already. */
export const selectableIssue = (issue) => Boolean(issue) && !issue.owned && !issue.queued;

/** What "Pull all released" would take: out now, and not already yours or asked for. */
export const releasedToPull = (issues = []) =>
  (issues || []).filter((issue) => selectableIssue(issue) && issue.releaseState === "released");

/** A run that has ended, taken whole: every issue not already yours or asked for. */
export const completeRunToPull = (issues = []) => (issues || []).filter(selectableIssue);

/**
 * The choices the run drawer offers.
 *
 * Following a run that has ended watches for issues that will never come, and
 * "Pull all released" is the same thing as the whole run once nothing is left
 * to release. So an ended run offers one way to take all of it, and choosing.
 */
export function runModes(publicationStatus) {
  return publicationStatus === "completed"
    ? [["complete", "Pull complete run"], ["choose", "Choose issues"]]
    : [["follow", "Follow run"], ["released", "Pull all released"], ["choose", "Choose issues"]];
}

/**
 * The run drawer's footer: what the button does, said before it is done.
 *
 * The three modes end in different places -- following keeps watching, the
 * other two do not -- so the line under the button says which, rather than
 * leaving it to be discovered next month.
 */
export function runPullSummary(mode, issues = [], selected = [], { following = false } = {}) {
  const list = issues || [];
  // Following a run you already follow joins the request you have; offering
  // it as an action says the opposite of what is true.
  if (mode === "follow" && following) {
    return { label: "Already following", detail: "New issues are added as they ship.", disabled: true };
  }
  if (mode === "follow") {
    const now = list.filter(selectableIssue).length;
    return {
      label: "Follow run",
      detail: now
        ? `${now} issue${now === 1 ? "" : "s"} now, and new ones as they ship.`
        : "New issues as they ship.",
      disabled: false,
    };
  }
  if (mode === "complete") {
    const count = completeRunToPull(list).length;
    const all = count === list.length;
    return count
      ? { label: all ? "Pull complete run" : `Pull ${count} missing issue${count === 1 ? "" : "s"}`,
          detail: all
            ? `All ${count} issue${count === 1 ? "" : "s"}, once. The run has ended, so there is nothing to follow.`
            : "The rest are already yours or on your Pull List. The run has ended, so nothing is followed.",
          disabled: false }
      : { label: "Nothing left to pull",
          detail: "Every issue is already yours or on your Pull List.", disabled: true };
  }
  if (mode === "released") {
    const count = releasedToPull(list).length;
    return count
      ? { label: `Pull ${count} released issue${count === 1 ? "" : "s"}`,
          detail: "Once. The run is not followed.", disabled: false }
      : { label: "Nothing left to pull",
          detail: "Every released issue is already yours or on your Pull List.", disabled: true };
  }
  const count = (selected || []).length;
  return count
    ? { label: `Pull ${count} issue${count === 1 ? "" : "s"}`,
        detail: "Only these. Issues not out yet are pulled when they ship.", disabled: false }
    : { label: "Choose issues", detail: "Tick the issues you want below.", disabled: true };
}

/** Provider ids for the preview, from a merged search row or a shelf issue. */
export function runPreviewIds(item) {
  const ids = { ...(item?.providerIds || {}) };
  if (item?.provider && item?.providerSeriesId && !ids[item.provider]) {
    ids[item.provider] = item.providerSeriesId;
  }
  return Object.fromEntries(Object.entries(ids).filter(([, value]) => value));
}
