import test from "node:test";
import assert from "node:assert/strict";
import {
  pullState, issueKey, PULL_STATES, shelfState, splitSearchResults, countLabel,
  providerProgress, libraryMatchState,
  selectableIssue, releasedToPull, runPullSummary, runPreviewIds,
  runModes, completeRunToPull, issueLabel,
  searchFold, libraryRunMatches,
} from "../src/discover.js";

test("a manga volume reads as a volume, a comic issue as an issue", () => {
  assert.equal(issueLabel("18", "manga"), "Vol. 18");
  assert.equal(issueLabel("18", "comic"), "#18");
  assert.equal(issueLabel("18"), "#18", "anything not known to be manga is a comic");
});

test("a run that has ended is taken whole or chosen, never followed", () => {
  // Following an ended run watches for issues that will never come.
  assert.deepEqual(runModes("completed").map(([id]) => id), ["complete", "choose"]);
  for (const status of ["ongoing", "unknown", undefined]) {
    assert.deepEqual(runModes(status).map(([id]) => id), ["follow", "released", "choose"], String(status));
  }
});

test("pulling a complete run takes every issue you do not have", () => {
  const ended = [
    { number: "1", releaseState: "released", owned: true },
    { number: "2", releaseState: "released", queued: true },
    { number: "3", releaseState: "released" },
    { number: "4", releaseState: "released" },
  ];
  assert.deepEqual(completeRunToPull(ended).map((i) => i.number), ["3", "4"]);
  const summary = runPullSummary("complete", ended);
  assert.equal(summary.label, "Pull 2 missing issues");
  assert.match(summary.detail, /nothing is followed/);
  assert.equal(summary.disabled, false);
});

test("a complete run you have none of says so plainly", () => {
  const fresh = [{ number: "1" }, { number: "2" }];
  const summary = runPullSummary("complete", fresh);
  assert.equal(summary.label, "Pull complete run");
  assert.match(summary.detail, /All 2 issues, once/);
  const done = runPullSummary("complete", [{ number: "1", owned: true }]);
  assert.equal(done.label, "Nothing left to pull");
  assert.equal(done.disabled, true);
});

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

test("the library half says it is reading, not that nothing matched", () => {
  // Before the catalog arrives the match list is empty, and that used to
  // render as "Nothing in your library matches" -- false, on every cold load.
  assert.equal(libraryMatchState(null, "loading", []), "loading");
  assert.equal(libraryMatchState(null, "online", []), "loading");
  assert.equal(libraryMatchState({ series: [] }, "online", []), "empty");
  assert.equal(libraryMatchState({ series: [{}] }, "online", [{ id: 1 }]), "ready");
});

test("offline, the demo library is the answer rather than a wait", () => {
  assert.equal(libraryMatchState(null, "offline", []), "empty");
  assert.equal(libraryMatchState(null, "offline", [{ id: 1 }]), "ready");
});

const run = [
  { number: "1", releaseState: "released", owned: true },
  { number: "2", releaseState: "released", queued: true },
  { number: "3", releaseState: "released" },
  { number: "4", releaseState: "released" },
  { number: "5", releaseState: "upcoming" },
];

test("an issue already here or already asked for cannot be chosen again", () => {
  assert.deepEqual(run.filter(selectableIssue).map((i) => i.number), ["3", "4", "5"]);
  assert.equal(selectableIssue(null), false);
});

test("all released means out now and not already yours", () => {
  assert.deepEqual(releasedToPull(run).map((i) => i.number), ["3", "4"]);
  assert.deepEqual(releasedToPull(null), []);
});

test("the footer says what the button will do before it is done", () => {
  // The three modes end in different places; following keeps watching.
  assert.equal(runPullSummary("follow", run).label, "Follow run");
  assert.match(runPullSummary("follow", run).detail, /3 issues now, and new ones as they ship/);
  assert.equal(runPullSummary("released", run).label, "Pull 2 released issues");
  assert.match(runPullSummary("released", run).detail, /not followed/);
  assert.equal(runPullSummary("choose", run, ["5"]).label, "Pull 1 issue");
  assert.equal(runPullSummary("choose", run, []).disabled, true);
});

test("nothing left to take is said, not offered", () => {
  const all = [{ number: "1", releaseState: "released", owned: true }];
  const summary = runPullSummary("released", all);
  assert.equal(summary.disabled, true);
  assert.equal(summary.label, "Nothing left to pull");
});

test("preview ids come from a merged row or a single provider", () => {
  assert.deepEqual(runPreviewIds({ providerIds: { metron: "9", gcd: "55" } }), { metron: "9", gcd: "55" });
  assert.deepEqual(runPreviewIds({ provider: "metron", providerSeriesId: "9" }), { metron: "9" });
  assert.deepEqual(runPreviewIds({ providerIds: { metron: "9", comic_vine: null } }), { metron: "9" });
  assert.deepEqual(runPreviewIds(null), {});
});

test("a run you already follow is not offered to you again", () => {
  const summary = runPullSummary("follow", run, [], { following: true });
  assert.equal(summary.label, "Already following");
  assert.equal(summary.disabled, true);
  // Not following: the offer stands.
  assert.equal(runPullSummary("follow", run, [], { following: false }).disabled, false);
});

test("a search folds accents, case and punctuation", () => {
  assert.equal(searchFold("Brian K. Vaughan"), "brian k vaughan");
  assert.equal(searchFold("Rodríguez"), "rodriguez");
  assert.equal(searchFold(null), "");
});

test("a library run matches by title, publisher or one of its creators", () => {
  const run = { title: "Saga", publisher: "Image Comics", year: "2012",
    creators: [{ name: "Brian K. Vaughan" }, { name: "Fiona Staples" }] };
  assert.ok(libraryRunMatches(run, { title: "saga", year: "" }));
  assert.ok(libraryRunMatches(run, { title: "image", year: "2012" }));
  assert.ok(libraryRunMatches(run, { title: "brian k vaughan", year: "" }));
  assert.ok(libraryRunMatches(run, { title: "Vaughan", year: "2012" }));
  assert.ok(!libraryRunMatches(run, { title: "Vaughan", year: "2015" }));
  assert.ok(!libraryRunMatches(run, { title: "Tynion", year: "" }));
  assert.ok(!libraryRunMatches({ title: "Saga" }, { title: "", year: "" }));
});
