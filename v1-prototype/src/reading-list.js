// A story arc saved to read across runs, as the client handles it: the card
// the grid draws for it, and -- when a comic of it ends -- which comic comes
// next in the arc's order and what was skipped to get there.
//
// The server decides the arc's Read button (`reading_target` over the comics
// that are here) and each profile's place; this is the other half, as
// `reading-target.js` is for a run's: pure, so it is tested away from the DOM.

/** The arc as a grid card: `sortLibrary` reads a title and an added date. */
export function listCard(list) {
  return { ...list, title: list.name, addedAt: list.createdAt };
}

/** When the arc ran, as a card says it: "2002", "2002–2003", or nothing known. */
export function arcYears(list) {
  const first = Number(list?.year) || 0;
  const last = Number(list?.yearEnd) || first;
  if (!first) return "";
  return last > first ? `${first}\u2013${last}` : String(first);
}

/** Whether an arc answers the Comics search: by its name, or a series in it. */
export function arcMatches(list, needle) {
  const query = String(needle || "").trim().toLowerCase();
  if (!query) return true;
  return String(list.name || "").toLowerCase().includes(query)
    || (list.seriesTitles || []).some((title) => String(title).toLowerCase().includes(query));
}

/**
 * The comic to offer after `afterFileId`, in the arc's order, and the issues
 * passed over to reach it. Reading is never blocked by a gap: an issue that
 * is not here (missing, or on the way) is skipped and named; a comic already
 * started or read is passed over silently, as `next_unread_file` does for a
 * run, and is not "skipped" in the sense the reader is told about.
 */
export function nextInList(items, afterFileId) {
  const list = Array.isArray(items) ? items : [];
  const at = list.findIndex((item) => item.fileId && String(item.fileId) === String(afterFileId));
  const skipped = [];
  for (const item of list.slice(at + 1)) {
    if (!item.fileId) { skipped.push(item); continue; }
    if (item.readable === false) { skipped.push(item); continue; }
    const started = (item.page && !item.stale) || item.finishedAt;
    if (started) continue;
    return { next: item, skipped };
  }
  return { next: null, skipped };
}

const name = (item) => `${item.seriesTitle} #${item.number}`;

/** "Superman #16 is missing", "… and Batman #152 are missing", "… and 3 more are on the way". */
export function skippedLine(skipped) {
  const items = Array.isArray(skipped) ? skipped : [];
  if (!items.length) return "";
  const onTheWay = items.every((item) => item.queued);
  const state = onTheWay ? "on the way" : "missing";
  if (items.length === 1) return `${name(items[0])} is ${state}`;
  if (items.length === 2) return `${name(items[0])} and ${name(items[1])} are ${state}`;
  return `${name(items[0])} and ${items.length - 1} more are ${state}`;
}

/**
 * The runs that exist only because of a story arc: not followed, and every
 * issue they own is an issue of some saved arc. An arc pull leaves a shelf
 * of one- and two-issue runs nobody asked to follow -- Wonder Woman #11-13,
 * Green Arrow #14-16 -- and those are the arc's, not runs of their own. A
 * run comes back as itself the moment it is followed or gains an issue no
 * arc claims. Returns a Map of run id -> the arc that stands for it (the
 * first, in the arcs' order, that holds one of its issues).
 */
export function arcOnlyRuns(series, lists) {
  // Only an arc that was saved or imported pulls runs in for itself; one made
  // by hand -- someone's own order of issues already here -- never folds a
  // run away from the grid.
  const arcs = (lists || []).filter((list) => list.source !== "manual" && (list.issueIds || []).length);
  const claimed = new Map();
  for (const list of arcs) for (const id of list.issueIds) if (!claimed.has(String(id))) claimed.set(String(id), list);
  const folded = new Map();
  for (const run of series || []) {
    if (run.isCollectionSeries || run.monitoringStatus === "monitored") continue;
    const owned = (run.issues || []).filter((issue) => issue.ownership && issue.ownership !== "unowned").map((issue) => String(issue.id));
    if (!owned.length || !owned.every((id) => claimed.has(id))) continue;
    folded.set(String(run.id), claimed.get(owned[0]));
  }
  return folded;
}

/**
 * The Comics grid with arc-only runs folded into their arcs: the runs kept,
 * and one card per arc that absorbed at least one run. An arc card carries
 * `kind: "arc"`, an id that cannot collide with a run's, and the arc's own
 * `listId` for the drawer and the reading map.
 */
export function foldArcRuns(series, lists) {
  const folded = arcOnlyRuns(series, lists);
  if (!folded.size) return { series: series || [], arcs: [] };
  const kept = (series || []).filter((run) => !folded.has(String(run.id)));
  const counts = new Map();
  for (const list of folded.values()) counts.set(String(list.id), (counts.get(String(list.id)) || 0) + 1);
  const arcs = (lists || []).filter((list) => counts.has(String(list.id))).map((list) => ({
    ...listCard(list), id: `arc-${list.id}`, listId: String(list.id), kind: "arc",
    foldedRunCount: counts.get(String(list.id)),
  }));
  return { series: kept, arcs };
}

// Arcs made by hand (since 2026-10-01): any profile makes its own, private
// until it shares it with the household. The server says whose each is
// (`mine`, `ownerName`) and whether this profile may change it (`editable`).

/** Whose an arc is, as its card and drawer say it: nothing for the household's. */
/**
 * Who made an arc, for its card: "you" for your own, the maker's name for
 * someone else's shared one, nothing for the household's (2026-10-02).
 */
export function arcMaker(list) {
  if (list?.mine) return "you";
  return list?.ownerName || "";
}

/**
 * A community reading list's small line: its publisher, the kind of list,
 * the guide it follows and its year -- "Marvel · Events · Official · 2019".
 * A tag that only repeats the publisher ("Marvel Comics") is left out.
 */
export function communityListLine(entry) {
  const publisher = String(entry?.publisher || "");
  const tags = (entry?.tags || []).filter((tag) => !publisher || !String(tag).toLowerCase().startsWith(publisher.toLowerCase()));
  return [publisher, entry?.group, ...tags, entry?.year].filter(Boolean).join(" \u00b7 ");
}

/** The orders an arc is read in, as its Arrange tab offers them. */
export const ARC_SORTS = [
  { id: "custom", label: "Your order" },
  { id: "release", label: "Release date" },
];

/** Who made a read group, in its card's words, and whether yours is shared. */
export const groupOwnerLine = (group) => arcOwnerLine(group);

/** Who made an arc, in its card's words, and whether yours is shared. */
export function arcOwnerLine(list) {
  const maker = arcMaker(list);
  if (!maker) return "";
  return `Made by ${maker}${list.mine && list.shared ? " \u00b7 shared" : ""}`;
}

/**
 * The arcs issues can be added to: the ones this profile may change, most
 * recently changed first, each saying whether it holds every one of them
 * already (`has`) or some (`hasSome`).
 */
export function arcsToAddTo(lists, issueIds) {
  return groupsToAddTo(lists, issueIds, (list) => list.issueIds);
}

/**
 * The read groups -- story arcs or collections -- something can be added to:
 * the ones this profile may change, latest first, each saying whether it
 * already holds all (`has`) or some (`hasSome`) of `ids`. `membersOf` gives
 * a group's members (an arc's issues, a collection's runs).
 */
export function groupsToAddTo(groups, ids, membersOf) {
  const wanted = (Array.isArray(ids) ? ids : [ids]).map((id) => String(id ?? ""));
  return (groups || []).filter((list) => list.editable)
    .map((list) => {
      const held = new Set((membersOf(list) || []).map(String));
      const count = wanted.filter((id) => held.has(id)).length;
      return { ...list, has: wanted.length > 0 && count === wanted.length, hasSome: count > 0 && count < wanted.length };
    })
    .sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")));
}

/** An issue number as a number to compare, or null: "23.1" is 23.1, "Annual 1" is not a number. */
export function issueNumberValue(number) {
  const text = String(number ?? "").trim();
  return /^-?\d+(\.\d+)?$/.test(text) ? Number(text) : null;
}

/** The ids of the issues numbered from..to, both ends included, in the order given. */
export function issuesInRange(issues, from, to) {
  const low = issueNumberValue(from);
  const high = issueNumberValue(to);
  if (low === null || high === null) return [];
  const [start, end] = low <= high ? [low, high] : [high, low];
  return (issues || []).filter((issue) => {
    const value = issueNumberValue(issue.number);
    return value !== null && value >= start && value <= end;
  }).map((issue) => String(issue.id));
}


/** An order with one id moved to the top or the bottom. */
export function moveToEdge(order, id, edge) {
  const rest = (order || []).filter((value) => value !== id);
  if (rest.length === (order || []).length) return order;
  return edge === "top" ? [id, ...rest] : [...rest, id];
}

/**
 * An arc's items ordered once by cover date, oldest first, for the order
 * editor's "Sort by release date". Items of the same date, and undated ones
 * (kept at the end), stay in the order they were in: this is a starting
 * point to fine-tune by hand, not a live sort.
 */
export function orderByReleaseDate(order, itemsById) {
  const dated = (id) => /^\d{4}-\d{2}(-\d{2})?$/.test(String(itemsById[id]?.coverDate || "")) ? String(itemsById[id].coverDate) : null;
  return (order || []).map((id, index) => ({ id, index, date: dated(id) }))
    .sort((a, b) => (a.date === null) - (b.date === null) || String(a.date || "").localeCompare(String(b.date || "")) || a.index - b.index)
    .map((entry) => entry.id);
}
