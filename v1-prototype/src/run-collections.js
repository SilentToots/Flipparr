// Collections (since 2026-10-01): the household's own groups of runs, after
// Plex's. The catalog sends each as its name and its runs' ids in order; the
// server has already left out runs this profile may not see. Everything here
// is drawn from the runs the page holds, so a cover or a count can only be a
// visible run's.
//
// Pure: the Collections tab, the Library grid and the drawer draw them.

import { sharedPublisher } from "./run-details.js";
import { sortLibrary } from "./library.js";

export const COLLECTION_SORTS = [
  { id: "custom", label: "Your order" },
  { id: "title", label: "Title" },
  { id: "year", label: "Year" },
];

const byTitle = (a, b) => String(a.title ?? a.name ?? "")
  .localeCompare(String(b.title ?? b.name ?? ""), undefined, { numeric: true, sensitivity: "base" });
const yearOf = (run) => (/^\d{4}$/.test(String(run?.year ?? "")) ? Number(run.year) : 9999);

/** A collection's runs, in the order it is set to show them. */
export function orderedRuns(collection, seriesById) {
  const runs = (collection?.runIds || []).map((id) => seriesById.get(String(id))).filter(Boolean);
  if (collection?.sortMode === "title") return [...runs].sort(byTitle);
  if (collection?.sortMode === "year") return [...runs].sort((a, b) => yearOf(a) - yearOf(b) || byTitle(a, b));
  return runs;
}

/** Each collection with its runs, cover and count, ready to draw. */
export function collectionCards(collections, series) {
  const seriesById = new Map((series || []).map((run) => [String(run.id), run]));
  return (collections || []).map((collection) => {
    const runs = orderedRuns(collection, seriesById);
    const chosen = collection.coverSeriesId ? seriesById.get(String(collection.coverSeriesId)) : null;
    // A picture the admin uploaded leads; then the chosen run's, then the runs'.
    const covers = [...(collection.coverImage ? [collection.coverImage] : []), ...[chosen, ...runs].filter(Boolean)
      .flatMap((run) => run.coverCandidates?.length ? run.coverCandidates : run.cover ? [run.cover] : [])];
    const years = runs.map(yearOf).filter((year) => year !== 9999);
    return {
      ...collection,
      kind: "collection",
      key: `collection-${collection.id}`,
      title: collection.name,
      addedAt: collection.createdAt,
      runs,
      runCount: runs.length,
      publisher: sharedPublisher(runs),
      // Issues across its runs, and how many of them are here.
      issueCount: runs.reduce((sum, run) => sum + (Number(run.total) || 0), 0),
      owned: runs.reduce((sum, run) => sum + (Number(run.owned) || 0), 0),
      cover: covers[0] || null,
      coverCandidates: [...new Set(covers)],
      years: years.length ? (Math.min(...years) === Math.max(...years) ? String(years[0]) : `${Math.min(...years)}–${Math.max(...years)}`) : "",
    };
  }).filter((card) => card.runCount > 0 || card.runIds?.length === 0);
}

/**
 * Where a collection stands for one profile, from its runs' places
 * (`runReading`, keyed by run): the run to read -- the one read last unless
 * it is finished, else the first unfinished in the collection's order, else
 * the first again -- and how many runs are started and finished. The drawer's
 * Read and its card's mark both come from here.
 */
export function collectionReadTarget(card, runReading) {
  const runs = card?.runs || [];
  const places = runs.map((run) => ({ run, place: runReading?.[String(run.id)] })).filter((entry) => entry.place);
  const latest = [...places].sort((a, b) => String(b.place.lastReadAt || "").localeCompare(String(a.place.lastReadAt || "")))[0] || null;
  const run = (latest && latest.place.state !== "finished" ? latest.run : null)
    || runs.find((item) => runReading?.[String(item.id)]?.state !== "finished") || runs[0] || null;
  const finished = places.filter((entry) => entry.place.state === "finished").length;
  return {
    run, finished,
    started: places.filter((entry) => entry.place.state !== "finished").length,
    allRead: runs.length > 0 && finished === runs.length,
    anyRead: places.length > 0,
  };
}

/**
 * Each collection's latest place among its runs, keyed by the collection, so
 * the Collections tab sorts by "recently read" as the Story arcs tab does.
 */
export function collectionPlaces(cards, runReading) {
  return Object.fromEntries((cards || []).map((card) => {
    const places = (card.runs || []).map((run) => runReading?.[String(run.id)]).filter(Boolean);
    const latest = places.sort((a, b) => String(b.lastReadAt || "").localeCompare(String(a.lastReadAt || "")))[0];
    return [String(card.id), latest];
  }).filter(([, place]) => place));
}

/**
 * The Library grid: collection cards among the runs (and folded arcs), all
 * in the library's one sort -- A-Z puts "Locke & Key" under L, not first
 * (the owner, 2026-10-03). A collection's id can equal a run's, so each sorts
 * under its card key, its latest run's place standing for "recently read".
 */
export function libraryGridOrder(series, collections, sort, reading = {}, runReading = {}) {
  if (!collections?.length) return sortLibrary(series || [], sort, reading);
  const places = { ...reading };
  for (const [id, place] of Object.entries(collectionPlaces(collections, runReading))) places[`collection-${id}`] = place;
  const keyed = [...collections.map((card) => ({ ...card, id: card.key || `collection-${card.id}`, card })),
    ...(series || []).map((item) => ({ ...item, card: item }))];
  return sortLibrary(keyed, sort, places).map((entry) => entry.card);
}

/** A card's size: "13 Runs | 29 Issues", one of each in the singular. */
export function countsLine(runCount, issueCount) {
  const n = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
  return `${n(Number(runCount) || 0, "Run")} | ${n(Number(issueCount) || 0, "Issue")}`;
}

/** Collections in the Collections tab's order: by name, or newest first. */
export function sortCollections(cards, sort) {
  const sorted = [...cards];
  if (sort === "added") {
    return sorted.sort((a, b) => String(b.createdAt ?? "").localeCompare(String(a.createdAt ?? "")) || byTitle(a, b));
  }
  return sorted.sort(byTitle);
}

/** The collections a run is in, by name. */
export function collectionsFor(runId, collections) {
  const id = String(runId);
  return (collections || []).filter((collection) => (collection.runIds || []).map(String).includes(id))
    .sort(byTitle);
}

/** Whether a collection answers a search: its name, or a run in it. */
export function collectionMatches(card, query) {
  const words = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const haystack = [card.name, card.summary, ...(card.runs || []).map((run) => run.title)].join(" ").toLowerCase();
  return words.every((word) => haystack.includes(word));
}

/** The order after one run is moved, for the PATCH. */
export function moveRun(runIds, id, toIndex) {
  const list = (runIds || []).map(String).filter((entry) => entry !== String(id));
  const at = Math.max(0, Math.min(list.length, Number(toIndex) || 0));
  return [...list.slice(0, at), String(id), ...list.slice(at)];
}
