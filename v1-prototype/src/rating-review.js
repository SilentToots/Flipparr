// Rating many runs at once (Settings > Profiles > Ratings > Set ratings).
//
// Metron rates about three runs in four; the rest -- most of BOOM!, Dark
// Horse, Oni, manga, and older DC -- have nothing, and a profile allowed
// unrated comics sees all of them. So the admin works through a list: what
// has no rating, or what one profile can see right now, narrowed by publisher
// or a title, and rates a selection in one go.
//
// Pure: the sheet in App.jsx draws it. Visibility mirrors the server's
// `content_rating.allows`; the server is the boundary, this only describes it.

import { RATINGS, RATING_LABELS } from "./profiles.js";

const RANK = Object.fromEntries(RATINGS.map((rating, index) => [rating, index]));

/** Whether a profile may see a run with this rating (null: unrated). */
export function profileSees(profile, rating) {
  if (!profile || profile.role === "admin" || !(profile.maxRating in RANK)) return true;
  if (!(rating in RANK)) return Boolean(profile.allowUnrated);
  return RANK[rating] <= RANK[profile.maxRating];
}

/** The profiles a rating decides anything for: readers with a limit. */
export function limitedProfiles(profiles) {
  return (profiles || []).filter((profile) => profile.role !== "admin" && profile.maxRating in RANK && !profile.disabled);
}

/**
 * One publisher however it is spelled: "Image" and "Image Comics", "BOOM!
 * Studios" and "Boom! Studios" are the same house.
 */
export function publisherKey(name) {
  return String(name || "")
    .toLowerCase()
    .replace(/[^a-z0-9 ]+/g, " ")
    .replace(/\b(comics?|publishing|studios?|press|books|entertainment|usa|inc|llc|ltd)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Where a run's rating stands, in a few words for its row. */
export function ratingNote(row) {
  if (!row.rating) return "No rating";
  const label = RATING_LABELS[row.rating] || row.rating;
  if (row.override) return `${label} · set by you`;
  return `${label} · ${row.source === "cover" ? "from the cover" : "from Metron"}`;
}

/** The catalog's runs as rows to rate. */
export function ratingRows(series) {
  return (series || []).map((run) => {
    // The catalog says "Publisher unknown" for none; it is not a house to filter by.
    const publisher = /^publisher unknown$/i.test(String(run.publisher || "").trim()) ? "" : run.publisher || "";
    return {
    id: String(run.id),
    title: run.title || "Untitled",
    year: /^\d{4}$/.test(String(run.year || "")) ? String(run.year) : "",
    publisher,
    publisherKey: publisherKey(publisher),
    rating: run.ageRating || null,
    override: run.ageRatingOverride || null,
    found: run.ageRatingFound || null,
    foundSource: run.ageRatingFound ? run.ageRatingFoundSource || null : null,
    source: run.ageRatingOverride ? "admin" : run.ageRatingFoundSource || null,
    cover: run.cover || null,
    coverCandidates: run.coverCandidates || [],
    };
  }).sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" }) || a.year.localeCompare(b.year));
}

/** Publishers with how many runs each, the commonest first, one entry per house. */
export function publisherChoices(rows) {
  const houses = new Map();
  for (const row of rows) {
    if (!row.publisherKey) continue;
    const house = houses.get(row.publisherKey) || { key: row.publisherKey, names: new Map(), count: 0 };
    house.count += 1;
    house.names.set(row.publisher, (house.names.get(row.publisher) || 0) + 1);
    houses.set(row.publisherKey, house);
  }
  return [...houses.values()]
    .map((house) => ({
      key: house.key, count: house.count,
      // The spelling most of its runs use.
      label: [...house.names.entries()].sort((a, b) => b[1] - a[1] || a[0].length - b[0].length)[0][0],
    }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

/**
 * The rows a view shows. `show` is "unrated", "all", or a profile's id --
 * what that profile can see right now.
 */
export function filterRatingRows(rows, { show = "unrated", profiles = [], publisher = "", query = "" } = {}) {
  const profile = show !== "unrated" && show !== "all"
    ? (profiles || []).find((item) => String(item.id) === String(show)) : null;
  const words = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
  return rows.filter((row) => {
    if (show === "unrated" && row.rating) return false;
    if (profile && !profileSees(profile, row.rating)) return false;
    if (publisher && row.publisherKey !== publisher) return false;
    const haystack = `${row.title} ${row.year} ${row.publisher}`.toLowerCase();
    return words.every((word) => haystack.includes(word));
  });
}

/**
 * What rating a selection did, said back: how many runs, and who no longer
 * sees them or now does. `rating` null sends them back to what was found.
 */
export function ratingOutcome(rows, ids, rating, profiles) {
  const chosen = rows.filter((row) => ids.includes(row.id));
  const count = chosen.length;
  const runs = `${count} run${count === 1 ? "" : "s"}`;
  const head = rating ? `Rated ${runs} ${RATING_LABELS[rating]}.` : `${runs} went back to the rating found for ${count === 1 ? "it" : "them"}.`;
  const changes = [];
  for (const profile of limitedProfiles(profiles)) {
    let hidden = 0;
    let shown = 0;
    for (const row of chosen) {
      const after = rating || row.found;
      const before = profileSees(profile, row.rating);
      const now = profileSees(profile, after);
      if (before && !now) hidden += 1;
      if (!before && now) shown += 1;
    }
    if (hidden) changes.push(`${profile.name} no longer sees ${hidden === count ? (count === 1 ? "it" : "them") : hidden}.`);
    if (shown) changes.push(`${profile.name} now sees ${shown === count ? (count === 1 ? "it" : "them") : shown}.`);
  }
  return [head, ...changes].join(" ");
}
