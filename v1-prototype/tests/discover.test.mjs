import test from "node:test";
import assert from "node:assert/strict";
import {
  pullState, issueKey, PULL_STATES, shelfState, splitSearchResults, countLabel,
  providerProgress,
} from "../src/discover.js";

test("a pulled card stops offering to pull", () => {
  // A card that still says Pull Issue after a successful pull invites a second
  // click, and a second click is a second acquisition request.
  const issue = { providerSeriesId: "9", providerIssueId: "1" };
  assert.equal(pullState(issue), PULL_STATES.idle);
  assert.equal(pullState(issue, { [issueKey(issue)]: PULL_STATES.pending }), PULL_STATES.pending);
  assert.equal(pullState(issue, { [issueKey(issue)]: PULL_STATES.queued }), PULL_STATES.queued);
});

test("what the server already knows outranks nothing at all", () => {
  assert.equal(pullState({ owned: true }), PULL_STATES.owned);
  assert.equal(pullState({ following: true }), PULL_STATES.queued);
  assert.equal(pullState({ queued: true }), PULL_STATES.queued);
  assert.equal(pullState(null), PULL_STATES.idle);
});

test("this session's answer outranks a refetch that has not caught up", () => {
  // The catalog poll is not instant, so the row can come back still saying
  // nothing has been asked for. The click is the more recent fact.
  const issue = { providerSeriesId: "9", providerIssueId: "1" };
  assert.equal(
    pullState(issue, { [issueKey(issue)]: PULL_STATES.queued }),
    PULL_STATES.queued,
  );
});

test("an issue is keyed by its provider ids, not its place in the list", () => {
  assert.equal(issueKey({ providerSeriesId: "9", providerIssueId: "1" }), "9:1");
  assert.equal(issueKey({ providerSeriesId: "9", number: "27" }), "9:27");
  assert.equal(issueKey({}), "?:?");
});

test("a shelf tells loading, empty and unavailable apart", () => {
  // All three used to render as an empty row, and only one of them is about
  // this week at all.
  assert.equal(shelfState(null, true, true), "loading");
  assert.equal(shelfState({ issues: [] }, false, false), "unavailable");
  assert.equal(shelfState({ issues: [], error: "Metron is busy" }, true, false), "error");
  assert.equal(shelfState({ issues: [] }, true, false), "empty");
  assert.equal(shelfState({ issues: [{}] }, true, false), "ready");
});

test("unavailable outranks an error, because it is not this week's fault", () => {
  assert.equal(shelfState({ error: "no token" }, false, false), "unavailable");
});

test("search offers what is new and counts what you already have", () => {
  // "No new runs match" and "all of them are already yours" are different
  // answers, and the second was reported as the first.
  const { fresh, ownedCount } = splitSearchResults([
    { title: "Batman", inLibrary: true },
    { title: "Batman Beyond", inLibrary: false },
    { title: "Absolute Batman", inLibrary: true },
  ]);
  assert.deepEqual(fresh.map((item) => item.title), ["Batman Beyond"]);
  assert.equal(ownedCount, 2);
  assert.deepEqual(splitSearchResults(), { fresh: [], ownedCount: 0 });
  assert.deepEqual(splitSearchResults(null), { fresh: [], ownedCount: 0 });
});

test("the count reads as a sentence at one and at many", () => {
  assert.equal(countLabel(1, "Library Match"), "1 Library Match");
  assert.equal(countLabel(2, "Library Match"), "2 Library Matches");
  assert.equal(countLabel(0, "New Match"), "0 New Matches");
  assert.equal(countLabel(1, "New Match"), "1 New Match");
});

test("each provider reports for itself while the others are still out", () => {
  // They settle at different times and results merge in as each returns, so
  // one spinner would hide both which answers are already on screen and which
  // source is the slow one.
  const progress = providerProgress({
    providersChecked: ["Metron", "Comic Vine", "Grand Comics Database"],
    providersAnswered: ["Metron"],
    fallbacks: [{ provider: "Comic Vine", error: "rate limited" }],
  });
  assert.deepEqual(progress.map((item) => [item.name, item.status]), [
    ["Metron", "answered"],
    ["Comic Vine", "failed"],
    ["Grand Comics Database", "searching"],
  ]);
  assert.equal(progress[1].error, "rate limited");
  assert.deepEqual(providerProgress(null), []);
});
