// Comics' Reading list tab (since 2026-10-01): what this profile added to
// read -- runs, story arcs and collections -- and nothing else. What it is in
// the middle of is Recommended's Keep Reading; this is what it chose to come
// back to (owner, 2026-10-01).
//
// The server keeps the entries (`/api/v1/me/reading-list`); everything drawn
// here comes from the catalog and arcs this profile was sent, so an entry for
// something it may not see has nothing to draw and is left out.
//
// Pure: the tab and the drawers' toggles in App.jsx use it.

import { listCard } from "./reading-list.js";

export const EMPTY_ENTRIES = Object.freeze({ runs: [], arcs: [], collections: [] });
export const READING_LIST_SORTS = [{ value: "added", label: "Recently added" }, { value: "title", label: "Title A–Z" }];

const KEY = { run: "runs", arc: "arcs", collection: "collections" };

/** Whether something is on the list. `kind` is run, arc or collection. */
export function isOnReadingList(entries, kind, id) {
  return (entries?.[KEY[kind]] || []).some((entry) => String(entry.id) === String(id));
}

/** The list after one change, before the server answers. */
export function withEntry(entries, kind, id, on, at = new Date().toISOString()) {
  const base = { ...EMPTY_ENTRIES, ...(entries || {}) };
  const rest = (base[KEY[kind]] || []).filter((entry) => String(entry.id) !== String(id));
  return { ...base, [KEY[kind]]: on ? [{ id: String(id), at }, ...rest] : rest };
}

/**
 * The tab's cards, newest addition first. A run or arc read to its end steps
 * out -- its entry is kept, so new issues bring it back -- and a collection
 * stays until it is taken off.
 */
export function readingListItems(entries, { series = [], lists = [], collections = [], runReading = {}, listReading = {} } = {}) {
  const runs = new Map(series.map((run) => [String(run.id), run]));
  const arcs = new Map(lists.map((list) => [String(list.id), list]));
  const cards = new Map(collections.map((card) => [String(card.id), card]));
  const items = [];
  for (const entry of entries?.runs || []) {
    const run = runs.get(String(entry.id));
    if (!run || runReading?.[String(entry.id)]?.state === "finished") continue;
    items.push({ item: run, kind: "run", at: entry.at });
  }
  for (const entry of entries?.arcs || []) {
    const list = arcs.get(String(entry.id));
    if (!list || listReading?.[String(entry.id)]?.state === "finished") continue;
    items.push({ item: { ...listCard(list), id: `arc-${list.id}`, listId: String(list.id), kind: "arc" }, kind: "arc", at: entry.at });
  }
  for (const entry of entries?.collections || []) {
    const card = cards.get(String(entry.id));
    if (!card) continue;
    items.push({ item: card, kind: "collection", at: entry.at });
  }
  return items.sort((a, b) => String(b.at).localeCompare(String(a.at)));
}

/** In the tab's order: as added, newest first, or by title. */
export function sortReadingList(items, sort) {
  if (sort !== "title") return items;
  const title = (entry) => String(entry.item.title ?? entry.item.name ?? "");
  return [...items].sort((a, b) => title(a).localeCompare(title(b), undefined, { numeric: true, sensitivity: "base" }));
}
