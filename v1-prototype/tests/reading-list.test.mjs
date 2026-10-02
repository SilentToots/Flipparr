import test from "node:test";
import assert from "node:assert/strict";
import { listCard, arcMatches, arcYears, nextInList, skippedLine, arcOnlyRuns, foldArcRuns, arcOwnerLine, arcMaker, arcsToAddTo, moveToEdge, orderByReleaseDate, issueNumberValue, issuesInRange } from "../src/reading-list.js";

test("an arc's years read as one year or a span, and nothing when unknown", () => {
  assert.equal(arcYears({ year: 2002, yearEnd: 2003 }), "2002\u20132003");
  assert.equal(arcYears({ year: 2024, yearEnd: 2024 }), "2024");
  assert.equal(arcYears({ year: 2024 }), "2024");
  assert.equal(arcYears({ year: null, yearEnd: null }), "");
  assert.equal(arcYears(undefined), "");
});

const item = (n, over = {}) => ({ id: String(n), seriesTitle: "Batman", number: String(n), fileId: `f${n}`, readable: true, page: 0, finishedAt: null, ...over });

test("an arc is a card the library can sort", () => {
  const card = listCard({ id: "3", name: "Hush", createdAt: "2026-09-28T00:00:00+00:00", seriesTitles: ["Batman"] });
  assert.equal(card.title, "Hush");
  assert.equal(card.addedAt, "2026-09-28T00:00:00+00:00");
  assert.ok(arcMatches(card, "hush"));
  assert.ok(arcMatches(card, "batman"), "a series in it answers too");
  assert.ok(!arcMatches(card, "superman"));
  assert.ok(arcMatches(card, ""), "no query keeps everything");
});

test("next in the arc is the first unread comic that is here, and the gaps are named", () => {
  const items = [item(608), item(609, { fileId: null }), item(610), item(611)];
  assert.deepEqual(nextInList(items, "f608"), { next: items[2], skipped: [items[1]] });
  assert.deepEqual(nextInList(items, "f610"), { next: items[3], skipped: [] });
  assert.deepEqual(nextInList(items, "f611"), { next: null, skipped: [] }, "the end of the arc");
});

test("a comic already started or read is passed over, not reported", () => {
  const items = [item(1), item(2, { finishedAt: "2026-09-01" }), item(3, { page: 4 }), item(4), item(5, { page: 4, stale: true })];
  assert.equal(nextInList(items, "f1").next, items[3]);
  assert.deepEqual(nextInList(items, "f1").skipped, []);
  assert.equal(nextInList(items, "f4").next, items[4], "a place against a file replaced since is no place");
  const unreadable = [item(1), item(2, { readable: false }), item(3)];
  assert.deepEqual(nextInList(unreadable, "f1"), { next: unreadable[2], skipped: [unreadable[1]] });
  assert.deepEqual(nextInList([], "f1"), { next: null, skipped: [] });
});

test("what was skipped is said in one line", () => {
  assert.equal(skippedLine([]), "");
  assert.equal(skippedLine([item(609, { fileId: null })]), "Batman #609 is missing");
  assert.equal(skippedLine([item(609, { fileId: null }), { seriesTitle: "Superman", number: "16" }]), "Batman #609 and Superman #16 are missing");
  assert.equal(skippedLine([item(1), item(2), item(3), item(4)]), "Batman #1 and 3 more are missing");
  assert.equal(skippedLine([item(1, { queued: true }), item(2, { queued: true })]), "Batman #1 and Batman #2 are on the way");
  assert.equal(skippedLine([item(1, { queued: true }), item(2)]), "Batman #1 and Batman #2 are missing", "one still missing keeps the plain word");
});

const run = (id, title, issues, over = {}) => ({ id: String(id), title, monitoringStatus: "cataloged", issues: issues.map(([iid, own]) => ({ id: String(iid), ownership: own })), ...over });

test("a run kept only for an arc is folded into the arc; a followed run, or one with issues of its own, is not", () => {
  const power = { id: "3", name: "Absolute Power", issueIds: ["101", "102", "201", "301"], createdAt: "2026-09-01" };
  const hush = { id: "4", name: "Hush", issueIds: ["401"], createdAt: "2026-09-02" };
  const series = [
    run(1, "Wonder Woman", [["101", "direct"], ["102", "direct"], ["103", "unowned"]]),
    run(2, "Green Arrow", [["201", "direct"]]),
    run(3, "Batman", [["301", "direct"], ["302", "direct"]]),
    run(4, "Superman", [["401", "direct"]], { monitoringStatus: "monitored" }),
    run(5, "Saga", [["501", "direct"]]),
    run(6, "Empty", []),
    run(7, "Coll", [["101", "direct"]], { isCollectionSeries: true }),
  ];
  const folded = arcOnlyRuns(series, [power, hush]);
  assert.deepEqual([...folded.keys()], ["1", "2"], "Batman owns #302 outside any arc; Superman is followed; Saga is nobody's; Empty owns nothing");
  assert.equal(folded.get("1").name, "Absolute Power");
  const grid = foldArcRuns(series, [power, hush]);
  assert.deepEqual(grid.series.map((item) => item.title), ["Batman", "Superman", "Saga", "Empty", "Coll"]);
  assert.deepEqual(grid.arcs.map((item) => [item.id, item.listId, item.kind, item.title, item.foldedRunCount]), [["arc-3", "3", "arc", "Absolute Power", 2]], "Hush absorbed nothing, so it is not on the shelf");
  assert.deepEqual(foldArcRuns(series, []), { series, arcs: [] });
});

test("an arc says whose it is, and nothing when it is the household's", () => {
  assert.equal(arcOwnerLine({ mine: true, shared: false }), "Made by you");
  assert.equal(arcOwnerLine({ mine: true, shared: true }), "Made by you \u00b7 shared");
  assert.equal(arcOwnerLine({ mine: false, ownerName: "Harrison", shared: true }), "Made by Harrison");
  assert.equal(arcOwnerLine({ mine: false, ownerName: null }), "");
});

test("an issue is added only to arcs this profile may change, the latest first", () => {
  const lists = [
    { id: "1", name: "Hush", editable: false, issueIds: ["5"], updatedAt: "2026-10-01T09:00:00" },
    { id: "2", name: "Mine", editable: true, issueIds: ["5", "6"], updatedAt: "2026-09-01T09:00:00" },
    { id: "3", name: "Newer", editable: true, issueIds: [], updatedAt: "2026-10-01T10:00:00" },
  ];
  assert.deepEqual(arcsToAddTo(lists, 5).map((list) => [list.name, list.has]), [["Newer", false], ["Mine", true]]);
  assert.deepEqual(arcsToAddTo(null, 5), []);
});

test("an issue moves to either end, and an unknown one leaves the order alone", () => {
  assert.deepEqual(moveToEdge(["a", "b", "c"], "c", "top"), ["c", "a", "b"]);
  assert.deepEqual(moveToEdge(["a", "b", "c"], "a", "bottom"), ["b", "c", "a"]);
  const order = ["a", "b"];
  assert.equal(moveToEdge(order, "z", "top"), order);
});

test("sorting by release date is oldest first, stable, with undated issues last", () => {
  const items = { a: { coverDate: "2003-01-01" }, b: { coverDate: null }, c: { coverDate: "2002-12-01" }, d: { coverDate: "2003-01-01" }, e: {} };
  assert.deepEqual(orderByReleaseDate(["a", "b", "c", "d", "e"], items), ["c", "a", "d", "b", "e"]);
});

test("an arc made by hand never folds a run into itself", () => {
  const run = { id: "9", monitoringStatus: "unmonitored", issues: [{ id: "90", ownership: "direct" }] };
  assert.equal(arcOnlyRuns([run], [{ id: "1", source: "manual", issueIds: ["90"] }]).size, 0);
  assert.equal(arcOnlyRuns([run], [{ id: "1", source: "metron", issueIds: ["90"] }]).size, 1);
});

test("several issues are in an arc when all of them are, and partly when some are", () => {
  const lists = [{ id: "2", editable: true, issueIds: ["5", "6"] }];
  assert.deepEqual(arcsToAddTo(lists, ["5", "6"]).map((list) => [list.has, list.hasSome]), [[true, false]]);
  assert.deepEqual(arcsToAddTo(lists, ["5", "7"]).map((list) => [list.has, list.hasSome]), [[false, true]]);
  assert.deepEqual(arcsToAddTo(lists, []).map((list) => [list.has, list.hasSome]), [[false, false]]);
});

test("a range of issue numbers takes both ends, decimals, and either order", () => {
  const issues = ["0", "1", "2", "2.1", "3", "Annual 1", "10"].map((number, index) => ({ id: index, number }));
  assert.deepEqual(issuesInRange(issues, "1", "3"), ["1", "2", "3", "4"]);
  assert.deepEqual(issuesInRange(issues, 3, 1), ["1", "2", "3", "4"]);
  assert.deepEqual(issuesInRange(issues, "", "3"), []);
  assert.equal(issueNumberValue("23.1"), 23.1);
  assert.equal(issueNumberValue("Annual 1"), null);
});

test("an arc's card names its maker only when it is a profile's", () => {
  assert.equal(arcMaker({ mine: true, shared: true }), "you");
  assert.equal(arcMaker({ mine: false, ownerName: "Sam" }), "Sam");
  assert.equal(arcMaker({ mine: false }), "");
});
