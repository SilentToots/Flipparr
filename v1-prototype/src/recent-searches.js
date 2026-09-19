// What the Search page shows under its field before a search: the searches
// run and the things opened from their results, newest first, as the Apple TV
// app's "Recently Searched". Kept in the viewer's own browser -- it is a convenience, and
// losing it loses nothing.

export const RECENT_SEARCHES_KEY = "flipparr.search.recent";
export const RECENT_SEARCHES_LIMIT = 8;

// A library run is kept by id and looked up again when shown, so a run since
// removed drops out and a renamed one shows its new name. A catalog run is
// not in the library, so it is kept whole: its drawer opens from it.
export function recentEntry(kind, item) {
  if (kind === "query") {
    const text = String(item || "").trim();
    return { key: `query:${text.toLowerCase()}`, kind, text };
  }
  if (kind === "series") return { key: `series:${item.id}`, kind, id: item.id };
  return {
    key: `run:${item.provider}-${item.providerSeriesId}`, kind: "run",
    item: {
      provider: item.provider, providerSeriesId: item.providerSeriesId,
      title: item.title, cover: item.cover, publisher: item.publisher,
      yearLabel: item.yearLabel, yearBegan: item.yearBegan, issueCount: item.issueCount,
      status: item.status, medium: item.medium,
    },
  };
}

export function rememberRecent(list, entry, limit = RECENT_SEARCHES_LIMIT) {
  return [entry, ...(list || []).filter((item) => item.key !== entry.key)].slice(0, limit);
}

export function readRecent(storage) {
  try {
    const parsed = JSON.parse(storage?.getItem(RECENT_SEARCHES_KEY) || "[]");
    return Array.isArray(parsed)
      ? parsed.filter((item) => item && typeof item.key === "string" && ["series", "run", "query"].includes(item.kind))
      : [];
  } catch {
    // Private windows and blocked site data throw on access.
    return [];
  }
}

export function writeRecent(storage, list) {
  try {
    storage?.setItem(RECENT_SEARCHES_KEY, JSON.stringify(list));
  } catch {
    /* the list is lost, the page is not */
  }
}
