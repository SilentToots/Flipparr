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
