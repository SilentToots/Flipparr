import test from "node:test";
import assert from "node:assert/strict";
import { listCard, arcMatches, nextInList, skippedLine, arcOnlyRuns, foldArcRuns } from "../src/reading-list.js";

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
