// Reading with no connection (Phase 4), as plain functions: what the reader
// is answered from the device when the server cannot be reached, how a saved
// place waits to be sent, and which place wins when it is. The app's
// transport (transport.js) uses these; tests/reader-app-offline-reading.test.mjs
// holds them to it.

/** The reader's /files/{id}/pages answer for a downloaded comic. */
export function pageListFromRow(row) {
  // `readUrl` exactly as the download stored it: the image source finds the
  // page on the device by that address (downloads.js localPage).
  const pages = (row.pageUrls || []).map((readUrl, index) => ({ index, url: readUrl, readUrl }));
  return { fileId: row.fileId, pageCount: pages.length, pages };
}

/** A page's panels from the comic's panels.json; none is read whole. */
export function panelsFor(saved, fileId, page) {
  const found = saved?.[page] ?? saved?.[String(page)];
  return found || { fileId: String(fileId), page: Number(page), segmented: false, panels: [] };
}

/** The requests the reader makes, by their path. */
export function readerRoute(path) {
  const clean = String(path).split("?")[0];
  let match = clean.match(/^\/api\/v1\/files\/(\d+)\/pages$/);
  if (match) return { kind: "pages", fileId: match[1] };
  match = clean.match(/^\/api\/v1\/files\/(\d+)\/pages\/(\d+)\/panels$/);
  if (match) return { kind: "panels", fileId: match[1], page: Number(match[2]) };
  match = clean.match(/^\/api\/v1\/files\/(\d+)\/progress$/);
  if (match) return { kind: "progress", fileId: match[1] };
  return null;
}

// The answers the Comics page and its drawers draw from, kept on the device
// per profile so the library can still be browsed offline. Only reading's:
// nothing an admin manages, nothing a provider is asked for.
const KEPT = [
  /^\/api\/v1\/catalog$/,
  /^\/api\/v1\/reading$/,
  /^\/api\/v1\/reading\/runs$/,
  /^\/api\/v1\/reading\/lists$/,
  /^\/api\/v1\/reading-lists$/,
  /^\/api\/v1\/reading-lists\/\d+$/,
  /^\/api\/v1\/me\/reading-list$/,
  /^\/api\/v1\/series\/\d+\/reading$/,
];
export function keepsAnswer(path) {
  return KEPT.some((pattern) => pattern.test(String(path)));
}

/**
 * A file name for a kept answer: the path, made safe, its slashes kept apart
 * from its hyphens -- /reading/lists and /reading-lists are different
 * answers, and once shared one file.
 */
export function answerName(path) {
  return `${String(path).replace(/^\/api\/v1\//, "").split("/").map((part) => part.replace(/[^a-z0-9-]+/gi, "_")).join("__")}.json`;
}

/**
 * A place saved, into the queue: one row per comic, the latest wins (a
 * `{read: true}` is simply the latest thing said about it). `at` is when it
 * was made, by the device's clock.
 */
export function queueSave(queue, fileId, body, at = new Date().toISOString()) {
  const id = String(fileId);
  return [...queue.filter((row) => row.fileId !== id), { fileId: id, body: { ...body }, at }];
}

/** The queue without one comic's row, once the server has it. */
export function queueDrop(queue, fileId, at) {
  return queue.filter((row) => !(row.fileId === String(fileId) && (at === undefined || row.at === at)));
}

/** The order a queue is sent in: oldest first, so each save lands as it was made. */
export function replayOrder(queue) {
  return [...queue].sort((a, b) => String(a.at).localeCompare(String(b.at)));
}

/**
 * The place to open at, from a row waiting to be sent and the server's
 * answer: whichever was made later. An unsent row exists only because the
 * server has not heard it, so it usually wins; a later save from the web
 * (another device, read since) wins over an older one waiting here.
 */
export function mergeProgress(local, server) {
  const fromLocal = local ? progressFromBody(local.fileId, local.body, local.at, server) : null;
  if (!fromLocal) return server ?? null;
  if (!server) return fromLocal;
  const serverAt = Date.parse(server.updatedAt || server.finishedAt || 0) || 0;
  const localAt = Date.parse(local.at || 0) || 0;
  return localAt >= serverAt ? fromLocal : server;
}

/** A /progress answer made from a queued save, for the reader. */
export function progressFromBody(fileId, body, at, known = null) {
  const pageCount = Number(known?.pageCount) || 0;
  if ("read" in body) {
    return { fileId: String(fileId), page: body.read && pageCount ? pageCount - 1 : 0, panel: 0, pageCount,
      finishedAt: body.read ? at : null, updatedAt: at, stale: false };
  }
  if (body.page === undefined || body.page === null) {
    return { fileId: String(fileId), page: 0, panel: 0, pageCount, finishedAt: null, updatedAt: at, stale: false };
  }
  return { fileId: String(fileId), page: Number(body.page), panel: Number(body.panel) || 0, pageCount,
    finishedAt: pageCount && Number(body.page) >= pageCount - 1 ? at : null, updatedAt: at, stale: false };
}
