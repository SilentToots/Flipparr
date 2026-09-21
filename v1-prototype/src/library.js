// How the Comics grid is ordered and narrowed, and how it remembers.
//
// Kindle's library has one grid and a sort called Recent, and that is the
// whole answer to "where was I": the runs you are reading and the runs that
// just gained comics rise to the top of the shelf you already have, rather
// than a second shelf over it. The reading half of "recent" comes from the
// run map the grid already fetches for its cover overlays; the other half is
// the catalog's own `addedAt`, the day a run's newest comic arrived.

import { byRating } from "./ratings.js";

export const SORT_OPTIONS = [
  { value: "recent", label: "Recent" },
  { value: "title", label: "Title A–Z" },
  { value: "added", label: "Recently added" },
  { value: "rated", label: "Highest rated" },
];

export const LIBRARY_DEFAULTS = Object.freeze({
  sort: "recent", view: "grid", scope: "runs", followingOnly: false, inProgressOnly: false,
});

// Collections have a name where runs have a title; both fall back to the
// title rather than to an arbitrary order that would look like a broken sort.
const byTitle = (a, b) => String(a.title ?? a.name ?? "")
  .localeCompare(String(b.title ?? b.name ?? ""), undefined, { numeric: true, sensitivity: "base" });

/**
 * When a run was last touched: read, or gained a comic, whichever is newer.
 *
 * Both are ISO timestamps from the server and compare as strings, the way the
 * "Recently added" sort already compares `addedAt`. A run with neither -- a
 * followed run nothing has arrived for -- is the empty string, and sinks.
 */
export function recencyOf(item, place) {
  const read = String(place?.lastReadAt ?? "");
  const added = String(item?.addedAt ?? "");
  return read > added ? read : added;
}

/** The library in the chosen order. `reading` is the grid's per-run map. */
export function sortLibrary(items, sort, reading = {}) {
  const sorted = [...items];
  if (sort === "recent") {
    const when = (item) => recencyOf(item, reading?.[String(item.id)]);
    return sorted.sort((a, b) => when(b).localeCompare(when(a)) || byTitle(a, b));
  }
  if (sort === "added") {
    return sorted.sort((a, b) => String(b.addedAt ?? "").localeCompare(String(a.addedAt ?? "")) || byTitle(a, b));
  }
  // Your rating first, an issue average standing in for a run you have not
  // rated, and everything unrated below in title order rather than at random.
  if (sort === "rated") {
    return sorted.sort((a, b) => byRating(a, b) || byTitle(a, b));
  }
  return sorted.sort(byTitle);
}

/**
 * A run you are currently reading: started, with issues still unread.
 *
 * "next" counts -- finishing #3 with #4 on the shelf is being in the middle
 * of a run -- and a run read to its end does not, however recently.
 */
export function inProgress(place) {
  return place?.state === "continue" || place?.state === "next";
}

const PREFS_KEY = "flipparr.library";
const SORT_IDS = new Set(SORT_OPTIONS.map((option) => option.value));

/**
 * How the grid was left, in this browser.
 *
 * Every read is guarded and every value is checked against what the page can
 * show: a sort that no longer exists, a hand-edited key or a private window
 * that throws all come back as the defaults rather than a grid that will not
 * render.
 */
export function loadLibraryPrefs(storage = globalThis.localStorage) {
  let raw = null;
  try { raw = storage?.getItem(PREFS_KEY) ?? null; } catch { raw = null; }
  let saved = {};
  try { saved = raw ? JSON.parse(raw) : {}; } catch { saved = {}; }
  if (!saved || typeof saved !== "object") saved = {};
  return {
    sort: SORT_IDS.has(saved.sort) ? saved.sort : LIBRARY_DEFAULTS.sort,
    view: saved.view === "list" ? "list" : LIBRARY_DEFAULTS.view,
    scope: saved.scope === "collections" ? "collections" : LIBRARY_DEFAULTS.scope,
    followingOnly: saved.followingOnly === true,
    inProgressOnly: saved.inProgressOnly === true,
  };
}

/** Remember the grid. Failing to -- quota, a private window -- costs nothing but the memory. */
export function saveLibraryPrefs(prefs, storage = globalThis.localStorage) {
  try {
    storage?.setItem(PREFS_KEY, JSON.stringify({
      sort: prefs.sort, view: prefs.view, scope: prefs.scope, followingOnly: Boolean(prefs.followingOnly),
      inProgressOnly: Boolean(prefs.inProgressOnly),
    }));
  } catch {
    // Nothing to do: the grid still works, it just forgets.
  }
}
