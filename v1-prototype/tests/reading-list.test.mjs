import test from "node:test";
import assert from "node:assert/strict";
import { listCard, arcMatches, nextInList, skippedLine } from "../src/reading-list.js";

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
