// Collections (since 2026-10-01): the household's own groups of runs, after
// Plex's. The catalog sends each as its name and its runs' ids in order; the
// server has already left out runs this profile may not see. Everything here
// is drawn from the runs the page holds, so a cover or a count can only be a
// visible run's.
//
// Pure: the Collections tab, the Library grid and the drawer draw them.

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
      runs,
      runCount: runs.length,
      cover: covers[0] || null,
      coverCandidates: [...new Set(covers)],
      years: years.length ? (Math.min(...years) === Math.max(...years) ? String(years[0]) : `${Math.min(...years)}–${Math.max(...years)}`) : "",
    };
  }).filter((card) => card.runCount > 0 || card.runIds?.length === 0);
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
