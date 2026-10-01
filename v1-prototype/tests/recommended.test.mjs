import test from "node:test";
import assert from "node:assert/strict";
import {
  keepReading, recentlyReleased, recentlyAddedIssues, recentlyAddedRuns, formatDay, localDay, ownedIssues,
} from "../src/recommended.js";

const series = [
  {
    id: 1, title: "Saga", year: "2012", cover: "/saga.jpg", firstAddedAt: "2026-01-02T00:00:00Z",
    issues: [
      { id: 11, number: "1", fileId: "101", publicationDate: "2012-03-14", addedAt: "2026-01-02T00:00:00Z", fileCover: "/s1.jpg" },
      { id: 12, number: "2", fileId: "102", publicationDate: "2012-04-11", addedAt: "2026-09-29T10:00:00Z" },
      { id: 13, number: "3", fileId: null, publicationDate: "2012-05-09" },
    ],
  },
  {
    id: 2, title: "Absolute Batman", year: "2024", firstAddedAt: "2026-09-30T08:00:00Z",
    issues: [
      { id: 21, number: "14", fileId: "201", publicationDate: "2026-09-24", addedAt: "2026-09-30T08:00:00Z" },
      { id: 22, number: "15", fileId: "202", publicationDate: "2026-10-29", addedAt: "2026-09-30T09:00:00Z" },
    ],
  },
  { id: 3, title: "A family", isCollectionSeries: true, firstAddedAt: "2026-10-01T00:00:00Z", issues: [] },
];

test("only owned issues are shelved", () => {
  assert.deepEqual(ownedIssues(series).map(({ issue }) => issue.id), [11, 12, 21, 22]);
});

test("keep reading says the page, or that an issue is up next", () => {
  const cards = keepReading([
    { fileId: "102", seriesRunId: "1", seriesTitle: "Saga", issueNumber: "2", page: 11, pageCount: 24, resume: "continue" },
    { fileId: "201", seriesRunId: "2", seriesTitle: "Absolute Batman", issueNumber: "14", page: 0, pageCount: 0, resume: "next" },
    { fileId: "999", seriesRunId: "9", seriesTitle: "Gone", issueNumber: "1", page: 0, pageCount: 0, resume: "next" },
  ], series);
  assert.deepEqual(cards.map((card) => [card.title, card.note, card.fileId]), [
    ["Saga #2", "Page 12 of 24", "102"],
    ["Absolute Batman #14", "Up next", "201"],
    ["Gone #1", "Up next", "999"],
  ]);
  assert.equal(cards[0].progress, 0.5);
  assert.equal(cards[0].cover, "/saga.jpg", "an issue with no cover of its own shows its run's");
});

test("recently released is newest first and never from the future", () => {
  assert.deepEqual(recentlyReleased(series, "2026-10-01").map((card) => card.title),
    ["Absolute Batman #14", "Saga #2", "Saga #1"]);
  assert.equal(recentlyReleased(series, "2026-10-29")[0].title, "Absolute Batman #15", "out on the day");
});

test("recently added issues and runs follow when they arrived", () => {
  assert.deepEqual(recentlyAddedIssues(series).map((card) => card.title),
    ["Absolute Batman #15", "Absolute Batman #14", "Saga #2", "Saga #1"]);
  assert.deepEqual(recentlyAddedRuns(series).map((card) => [card.title, card.note]),
    [["Absolute Batman", "2024"], ["Saga", "2012"]], "a family's card is not a run");
  assert.equal(recentlyAddedRuns(series, 1).length, 1);
});

test("dates read as a person writes them", () => {
  const now = new Date(2026, 9, 1);
  assert.equal(formatDay("2026-09-30T08:00:00Z", now), "Sep 30");
  assert.equal(formatDay("2012-03-14", now), "Mar 14, 2012");
  assert.equal(formatDay("", now), "");
  assert.equal(localDay(new Date(2026, 0, 5)), "2026-01-05");
});
