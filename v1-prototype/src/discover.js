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

// The order the week's comics are worth looking at, by what this library
// already says. Every signal below is already on the wire: the server attaches
// following/inLibrary/owned/queued to each release row against the local
// catalog, so this is presentation, not a second source of truth.
//
// It is deliberately not called trending. Nothing here knows what anyone else
// is reading -- there is no popularity feed among the providers -- so the
// shelf says what it actually ranks: this week against your library.
//
// What that means in practice, measured against a real week: of 233 issues
// across the three weeks, 8 touched the library (all followed, and so already
// queued) and 49 were first issues. So the shelf is a followed-runs block and
// then the week's #1s, and the subtitle says so rather than implying that
// anything here is popular.
const PICK_WEIGHTS = {
  following: 40,      // a run you follow shipping is the strongest signal there is
  inLibrary: 20,      // a run you own, even unfollowed, is one you chose once
  firstIssue: 8,      // a #1 is the only issue you can start without catching up
  recurringWeek: 6,   // the same run shipping across weeks is a run mid-story
  publisherShare: 4,  // a publisher you already read, weakly -- see below
  latest: 6,          // this week beats
  upcoming: 4,        // next week beats
  previous: 0,        // last week
  owned: -25,         // you have it; it is not news
  queued: -10,        // already asked for; lowered rather than hidden
  coverless: -15,     // a card with no art reads as broken in a shelf
};

const FIRST_ISSUE = new Set(["1", "01", "001"]);

/** Ties break the way a person would order them: by title, then by number. */
export function pickSortKey(issue) {
  const number = Number.parseFloat(String(issue?.number ?? "").replace(/[^\d.]/g, ""));
  return [String(issue?.seriesTitle || "").toLowerCase(), Number.isFinite(number) ? number : 0];
}

/**
 * This week's comics, ranked against your library.
 *
 * Takes the whole releases payload because a run shipping in two of the three
 * weeks is itself a signal, and that can only be seen across shelves.
 *
 * The state matters as much as the order. One shelf can fail while the others
 * answer -- the endpoint fetches each week separately for exactly that reason
 * -- and ranking the survivors while calling them "this week" would be a
 * quiet lie, so a partial answer says it is partial.
 */
export function weeklyPicks(data, { limit = 12 } = {}) {
  const shelves = ["latest", "upcoming", "previous"];
  const present = shelves.filter((name) => data?.[name]);
  const failed = present.filter((name) => data[name].error);
  if (data?.available === false) return { state: "unavailable", issues: [], weeksUsed: 0, weeksTotal: present.length };
  if (present.length && failed.length === present.length) {
    return { state: "error", issues: [], error: data[failed[0]].error, weeksUsed: 0, weeksTotal: present.length };
  }

  const runs = new Map();
  const seen = new Map();
  for (const name of present) {
    for (const issue of data[name].issues || []) {
      const run = issue.providerSeriesId;
      if (run) runs.set(run, (runs.get(run) || new Set()).add(name));
      const key = issueKey(issue);
      // Metron moves store dates, so the same issue can land in two weeks.
      if (!seen.has(key)) seen.set(key, { issue, shelf: name });
    }
  }
  const publishers = new Map();
  let known = 0;
  for (const { issue } of seen.values()) {
    if (!issue.publisher) continue;
    known += 1;
    publishers.set(issue.publisher, (publishers.get(issue.publisher) || 0) + 1);
  }

  const scored = [...seen.values()]
    .filter(({ issue }) => !issue.owned)
    .map(({ issue, shelf }) => {
      const weeks = runs.get(issue.providerSeriesId)?.size || 1;
      const share = known && issue.publisher ? (publishers.get(issue.publisher) || 0) / known : 0;
      const score = (issue.following ? PICK_WEIGHTS.following : 0)
        + (issue.inLibrary ? PICK_WEIGHTS.inLibrary : 0)
        + (FIRST_ISSUE.has(String(issue.number || "").trim()) ? PICK_WEIGHTS.firstIssue : 0)
        + (weeks - 1) * PICK_WEIGHTS.recurringWeek
        + share * PICK_WEIGHTS.publisherShare
        + PICK_WEIGHTS[shelf]
        + (issue.queued ? PICK_WEIGHTS.queued : 0)
        + (issue.cover ? 0 : PICK_WEIGHTS.coverless);
      return { issue, score };
    })
    .sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      const [leftTitle, leftNumber] = pickSortKey(a.issue);
      const [rightTitle, rightNumber] = pickSortKey(b.issue);
      return leftTitle.localeCompare(rightTitle) || leftNumber - rightNumber;
    })
    .slice(0, limit)
    .map((entry) => entry.issue);

  const weeksUsed = present.length - failed.length;
  if (!scored.length) return { state: "empty", issues: [], weeksUsed, weeksTotal: present.length };
  return {
    state: failed.length ? "partial" : "ready",
    issues: scored, weeksUsed, weeksTotal: present.length,
  };
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

/**
 * Text as a search compares it: accents, case and punctuation folded, so
 * "rodriguez" finds Rodríguez and "brian k vaughan" finds Brian K. Vaughan.
 */
export const searchFold = (value) => String(value || "")
  .normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase()
  .replace(/[^\p{L}\p{N}]+/gu, " ").trim();

/** Whether a library run matches a search: its title, publisher or creators. */
export function libraryRunMatches(item, parts) {
  const needle = searchFold(parts?.title);
  if (!needle) return false;
  const text = searchFold([
    item?.searchText || item?.title, item?.publisher,
    ...(item?.creators || []).map((creator) => creator.name),
  ].filter(Boolean).join(" "));
  return text.includes(needle)
    && (!parts.year || `${item?.year || ""} ${item?.run || ""}`.includes(parts.year));
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
 * The ways the run drawer offers to take a run, in the order it stacks them.
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
    : { label: "Choose issues", detail: "Tick the issues you want to pull.", disabled: true };
}

/** "Vol. 18" for manga, "#18" for a comic: a manga volume is the book, not an issue of it. */
export const issueLabel = (number, format) =>
  `${format === "manga" ? "Vol. " : "#"}${number ?? ""}`;

/** Provider ids for the preview, from a merged search row or a shelf issue. */
export function runPreviewIds(item) {
  const ids = { ...(item?.providerIds || {}) };
  if (item?.provider && item?.providerSeriesId && !ids[item.provider]) {
    ids[item.provider] = item.providerSeriesId;
  }
  return Object.fromEntries(Object.entries(ids).filter(([, value]) => value));
}
