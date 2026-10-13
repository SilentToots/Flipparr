// What a profile changes about its own reading while Flipparr View cannot
// reach the server (Phase 5): a rating, a run or story arc marked read or
// unread, a reading-list entry added or taken off -- and the admin's fix to
// a page's panels (the owner, 2026-10-12), sent as made, last one standing. Each waits on the device
// as one row per thing changed -- the latest wins -- with when it was made,
// and is sent on reconnecting with that `at`, so the server keeps whichever
// is newer (catalog_store `set_rating`, `set_reading_list_entry`,
// app.py `mark_run_reading`). Until it is sent, every answer the screens
// draw from shows it (`withChanges`), online or off. Places in a comic, and
// one comic marked read, are the progress queue's (offline-reading.js).
//
// Plain functions; tests/reader-app-offline-changes.test.mjs holds them.
import { withEntry } from "../reading-list-tab.js";

const RATING = /^\/api\/v1\/(issues|series)\/(\d+)\/rating$/;
const BOOKMARK = /^\/api\/v1\/me\/reading-list\/(run|arc|collection)\/(\d+)$/;
const RUN_READ = /^\/api\/v1\/series\/(\d+)\/reading$/;
const ARC_READ = /^\/api\/v1\/reading-lists\/(\d+)\/reading$/;
const PANELS = /^\/api\/v1\/files\/(\d+)\/pages\/(\d+)\/panels$/;

/**
 * A request that can wait on the device, as a change: `{key, path, method,
 * body}`; null for anything else. `key` names the thing changed, so a later
 * change to it replaces a waiting one (adding and taking off share one).
 */
export function changeFor(path, method = "GET", body = {}) {
  const clean = String(path).split("?")[0];
  const verb = method.toUpperCase();
  let match = clean.match(RATING);
  if (match && verb === "POST") {
    const kind = match[1] === "issues" ? "issue" : "series";
    return { key: `rating:${kind}:${match[2]}`, path: clean, method: "POST", body: { rating: body.rating ?? null } };
  }
  match = clean.match(BOOKMARK);
  if (match && (verb === "POST" || verb === "DELETE")) {
    return { key: `list:${match[1]}:${match[2]}`, path: clean, method: verb, body: {} };
  }
  match = clean.match(RUN_READ);
  if (match && verb === "POST" && typeof body.read === "boolean") {
    return { key: `run-read:${match[1]}`, path: clean, method: "POST", body: { read: body.read } };
  }
  match = clean.match(ARC_READ);
  if (match && verb === "POST" && typeof body.read === "boolean") {
    return { key: `arc-read:${match[1]}`, path: clean, method: "POST", body: { read: body.read } };
  }
  // A page's panels fixed by hand. Reading a page again (DELETE) needs the
  // server's finder, so it is not one of these.
  match = clean.match(PANELS);
  if (match && verb === "PATCH" && Array.isArray(body.panels)) {
    return { key: `panels:${match[1]}:${match[2]}`, path: clean, method: "PATCH", body: { panels: body.panels } };
  }
  return null;
}

/**
 * A page's panels as the server answers once it keeps a person's fix
 * (app.py `file_page_panels`): in the order drawn, the reader's ids, and no
 * rectangles for the page read whole.
 */
export function manualReading(fileId, page, panels, readingDirection = undefined) {
  return {
    fileId: String(fileId), page: Number(page), source: "manual", segmented: true,
    ...(readingDirection ? { readingDirection } : {}),
    panels: panels.map((panel, position) => ({ id: `${page}-${position}`, ...panel, order: position })),
  };
}

/** The queue with a change in it: one row per thing, the latest replacing what waited. */
export function queueChange(queue, change, at = new Date().toISOString()) {
  return [...queue.filter((row) => row.key !== change.key), { ...change, at }];
}

/** The queue without a change once the server has it -- only if it is still the row that was sent. */
export function dropChange(queue, key, at) {
  const next = queue.filter((row) => !(row.key === key && (at === undefined || row.at === at)));
  return next.length === queue.length ? queue : next; // unchanged: nothing to write
}

/** How a waiting change is sent: oldest first, each with its time. */
export function replayRequest(row) {
  if (row.method === "DELETE") {
    return { path: `${row.path}?at=${encodeURIComponent(row.at)}`, options: { method: "DELETE" } };
  }
  return {
    path: row.path,
    options: { method: row.method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...row.body, at: row.at }) },
  };
}

const parts = (row) => row.key.split(":");

function patchCatalog(catalog, ratings) {
  if (!ratings.length || !Array.isArray(catalog?.series)) return catalog;
  const runs = new Map();
  const issues = new Map();
  for (const row of ratings) {
    const [, kind, id] = parts(row);
    (kind === "series" ? runs : issues).set(id, row.body.rating ?? null);
  }
  return {
    ...catalog,
    series: catalog.series.map((series) => {
      const ownRating = runs.has(String(series.id));
      const touchesIssues = (series.issues || []).some((issue) => issues.has(String(issue.id)));
      if (!ownRating && !touchesIssues) return series;
      return {
        ...series,
        ...(ownRating ? { yourRating: runs.get(String(series.id)) } : {}),
        ...(touchesIssues ? {
          issues: series.issues.map((issue) => (issues.has(String(issue.id))
            ? { ...issue, yourRating: issues.get(String(issue.id)) } : issue)),
        } : {}),
      };
    }),
  };
}

function patchReadingList(entries, bookmarks) {
  return bookmarks.reduce((current, row) => {
    const [, kind, id] = parts(row);
    return withEntry(current, kind, id, row.method === "POST", row.at);
  }, entries);
}

// One comic's place, read to the end or forgotten.
function markedFile(file, read, at) {
  const pageCount = Number(file.pageCount) || 0;
  return read
    ? { ...file, page: Math.max(0, pageCount - 1), finishedAt: file.finishedAt || at }
    : { ...file, page: 0, finishedAt: null };
}

function patchRunReading(answer, runId, marks) {
  const row = marks.get(`run-read:${runId}`);
  if (!row || !answer) return answer;
  const mark = (files) => (files || []).map((file) => (file.readable === false ? file : markedFile(file, row.body.read, row.at)));
  return { ...answer, issues: mark(answer.issues), volumes: mark(answer.volumes) };
}

function patchArc(answer, listId, marks) {
  if (!answer || !Array.isArray(answer.items)) return answer;
  const arc = marks.get(`arc-read:${listId}`);
  return {
    ...answer,
    items: answer.items.map((item) => {
      const row = arc || marks.get(`run-read:${item.runId}`);
      return row && item.fileId && item.readable !== false ? markedFile(item, row.body.read, row.at) : item;
    }),
  };
}

// The cards' summaries: a run or arc marked read has read every comic it
// has; marked unread, it has no place at all.
function patchSummaries(answer, field, prefix, marks) {
  const table = answer?.[field];
  if (!table || typeof table !== "object") return answer;
  const next = { ...table };
  let changed = false;
  for (const [key, row] of marks) {
    if (!key.startsWith(prefix)) continue;
    const id = key.slice(prefix.length);
    changed = true;
    if (!row.body.read) { delete next[id]; continue; }
    const entry = next[id];
    if (entry) next[id] = { ...entry, state: "finished", read: entry.total, lastReadAt: row.at };
  }
  return changed ? { ...answer, [field]: next } : answer;
}

/**
 * An answer as it will be once what waits has been sent: the server's (or
 * the device's kept copy) with the waiting changes laid over it. Answers no
 * change touches come back as they were.
 */
export function withChanges(path, answer, queue) {
  if (!queue?.length || answer == null) return answer;
  const clean = String(path).split("?")[0];
  const ratings = queue.filter((row) => row.key.startsWith("rating:"));
  const bookmarks = queue.filter((row) => row.key.startsWith("list:"));
  const marks = new Map(queue.filter((row) => /^(run|arc)-read:/.test(row.key)).map((row) => [row.key, row]));
  if (clean === "/api/v1/catalog") return patchCatalog(answer, ratings);
  if (clean === "/api/v1/me/reading-list") return bookmarks.length ? patchReadingList(answer, bookmarks) : answer;
  let match = clean.match(PANELS);
  if (match) {
    const fix = queue.find((row) => row.key === `panels:${match[1]}:${match[2]}`);
    return fix ? manualReading(match[1], match[2], fix.body.panels, answer.readingDirection) : answer;
  }
  if (!marks.size) return answer;
  match = clean.match(/^\/api\/v1\/series\/(\d+)\/reading$/);
  if (match) return patchRunReading(answer, match[1], marks);
  match = clean.match(/^\/api\/v1\/reading-lists\/(\d+)$/);
  if (match) return patchArc(answer, match[1], marks);
  if (clean === "/api/v1/reading/runs") return patchSummaries(answer, "runs", "run-read:", marks);
  if (clean === "/api/v1/reading/lists") return patchSummaries(answer, "lists", "arc-read:", marks);
  return answer;
}

/**
 * What a change answers while it waits, built from the device's kept copy:
 * the screens use these answers as they use the server's.
 */
export function answerWhileWaiting(change, kept, queue) {
  const [kind, subject, id] = parts(change);
  if (kind === "rating") return { id, yourRating: change.body.rating ?? null };
  if (kind === "list") return withChanges("/api/v1/me/reading-list", kept || { runs: [], arcs: [], collections: [] }, queue);
  if (kind === "run-read") return withChanges(`/api/v1/series/${subject}/reading`, kept || {}, queue);
  if (kind === "arc-read") return withChanges(`/api/v1/reading-lists/${subject}`, kept || {}, queue);
  if (kind === "panels") return manualReading(subject, id, change.body.panels, kept?.readingDirection);
  return {};
}

/**
 * An answer with the places waiting in the progress queue (offline-reading.js)
 * laid over its comics: a comic marked read or unread, or left on a page,
 * shows so in its run's drawer and its arcs before the server has heard.
 */
export function withPlaces(path, answer, places) {
  if (!places?.length || answer == null) return answer;
  const clean = String(path).split("?")[0];
  const byFile = new Map(places.map((row) => [String(row.fileId), row]));
  const place = (file, id) => {
    const row = byFile.get(String(id));
    if (!row || file.readable === false) return file;
    if ("read" in row.body) return markedFile(file, Boolean(row.body.read), row.at);
    if (row.body.page === undefined || row.body.page === null) return { ...file, page: 0, finishedAt: null };
    const pageCount = Number(file.pageCount) || 0;
    const page = Number(row.body.page);
    return { ...file, page, finishedAt: pageCount && page >= pageCount - 1 ? file.finishedAt || row.at : null };
  };
  if (/^\/api\/v1\/series\/\d+\/reading$/.test(clean)) {
    return { ...answer, issues: (answer.issues || []).map((file) => place(file, file.id)),
      volumes: (answer.volumes || []).map((file) => place(file, file.id)) };
  }
  if (/^\/api\/v1\/reading-lists\/\d+$/.test(clean) && Array.isArray(answer.items)) {
    return { ...answer, items: answer.items.map((item) => (item.fileId ? place(item, item.fileId) : item)) };
  }
  return answer;
}

/** The kept answer a change's stand-in answer is built from. */
export function keptPathFor(change) {
  const [kind, subject] = parts(change);
  if (kind === "list") return "/api/v1/me/reading-list";
  if (kind === "run-read") return `/api/v1/series/${subject}/reading`;
  if (kind === "arc-read") return `/api/v1/reading-lists/${subject}`;
  return null;
}
