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
