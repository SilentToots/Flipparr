// The rules of downloading for offline reading, as plain functions: what a
// download's row holds, where its files go, how much room it takes, and what
// makes way when the device's cap is reached (docs/OFFLINE_READING_PLAN.md,
// "The device store"). The engine (downloads.js) and the screens use these;
// tests/reader-app-offline.test.mjs holds them to it.

export const DEFAULT_CAP_BYTES = 2 * 1024 * 1024 * 1024;
export const CAP_CHOICES = [1, 2, 4, 8].map((gigabytes) => gigabytes * 1024 * 1024 * 1024);

/** Where a profile's downloaded comic keeps its pages, under Library/flipparr. */
export function comicFolder(profileId, fileId) {
  return `u${profileId}/files/${fileId}`;
}
export function pagePath(profileId, fileId, index) {
  return `${comicFolder(profileId, fileId)}/${index}.jpg`;
}
export function panelsPath(profileId, fileId) {
  return `${comicFolder(profileId, fileId)}/panels.json`;
}
/**
 * One page's panels, fetched by the background session beside its page
 * (so a comic finishes while the device is locked) and gathered into
 * panels.json when the comic is complete.
 */
export function panelPartPath(profileId, fileId, index) {
  return `${comicFolder(profileId, fileId)}/panels-${index}.json`;
}
/**
 * The address of one page's panels for a download: only what the server
 * already keeps -- a person's fix, the vision model's reading -- with nothing
 * worked out for it (the owner, 2026-10-09). A page with none is read whole.
 */
export function panelsUrl(fileId, index) {
  return `/api/v1/files/${fileId}/pages/${index}/panels?stored=only`;
}

/**
 * A transfer's id: profile, comic and page -- "p" before a page's panels --
 * so one comic's can be cancelled together.
 */
export function transferId(profileId, fileId, index, kind = "page") {
  return `${profileId}:${fileId}:${kind === "panels" ? "p" : ""}${index}`;
}
export function parseTransferId(id) {
  const [profileId, fileId, part = ""] = String(id).split(":");
  const panels = part.startsWith("p");
  return { profileId, fileId, index: Number(panels ? part.slice(1) : part), kind: panels ? "panels" : "page" };
}

/** A download is complete once every page and every page's panels are in. */
export function downloadComplete(row) {
  return (row.pagesDone?.length || 0) >= row.pageCount && (row.panelsDone?.length || 0) >= row.pageCount;
}

/** A new row for a comic about to download, from its /offline manifest. */
export function downloadRow(manifest, { groupKey = null } = {}) {
  const run = manifest.run || null;
  return {
    fileId: String(manifest.fileId),
    seriesRunId: run ? String(run.id) : null,
    seriesTitle: run?.title || manifest.filename,
    issueNumber: manifest.issueNumber ?? null,
    filename: manifest.filename,
    medium: run?.medium || "comic",
    readingDirection: manifest.readingDirection || "ltr",
    pageCount: Number(manifest.pageCount) || 0,
    fileSignature: manifest.fileSignature,
    pageUrls: (manifest.pages || []).map((page) => page.readUrl),
    pagesDone: [],
    panelsDone: [],
    bytes: 0,
    estimate: Number(manifest.bytesEstimate) || 0,
    state: "downloading",
    downloadedAt: null,
    lastReadAt: null,
    finished: false,
    groupKey,
    error: null,
  };
}

/** What a download has done so far, as a fraction for a progress bar: pages, then their panels. */
export function progressOf(row) {
  if (!row.pageCount) return 0;
  const done = (row.pagesDone?.length || 0) + (row.panelsDone?.length || 0);
  return Math.min(1, done / (2 * row.pageCount));
}

/** Room taken: what is on the device, and what a download under way will take. */
export function usageBytes(rows) {
  return rows.reduce((sum, row) => sum + (row.state === "ready" ? row.bytes : Math.max(row.bytes, row.estimate || 0)), 0);
}

/**
 * What may make way for a new download, first to go first: finished comics
 * only, the one read longest ago first -- never one in progress, queued or
 * downloading, and never the one open in the reader.
 */
export function evictionOrder(rows, { reading = null } = {}) {
  const at = (row) => Date.parse(row.lastReadAt || row.downloadedAt || 0) || 0;
  return rows
    .filter((row) => row.state === "ready" && row.finished && row.fileId !== String(reading))
    .sort((a, b) => at(a) - at(b));
}

/**
 * Whether a download of `estimate` bytes fits under `capBytes` beside
 * `rows`, and what goes to make room: {fits, evict: [fileId], shortfall}.
 */
export function planDownload(estimate, { capBytes = DEFAULT_CAP_BYTES, rows = [], reading = null } = {}) {
  let free = capBytes - usageBytes(rows);
  const evict = [];
  for (const row of evictionOrder(rows, { reading })) {
    if (free >= estimate) break;
    evict.push(row.fileId);
    free += row.bytes;
  }
  if (free >= estimate) return { fits: true, evict, shortfall: 0 };
  return { fits: false, evict: [], shortfall: estimate - free };
}

/**
 * The next `n` comics to download from a run, arc or collection, in the
 * order given (the order the drawer shows): readable, not finished (one
 * part-read counts), and not already on the device or on its way.
 * `files`: [{fileId, finishedAt, readable}].
 */
export function nextUnread(files, n, { onDevice = new Set() } = {}) {
  const have = new Set([...onDevice].map(String));
  const seen = new Set();
  const picked = [];
  for (const file of files || []) {
    const id = file?.fileId == null ? "" : String(file.fileId);
    if (!id || seen.has(id) || have.has(id) || file.readable === false || file.finishedAt) continue;
    seen.add(id);
    picked.push(id);
    if (picked.length >= n) break;
  }
  return picked;
}

/**
 * Whether a batch fits under the cap, all at once: `estimates` are
 * [{fileId, bytes}] in order. Finished comics make way, read longest ago
 * first, as for one download. {fits, total, evict: [fileId], shortfall,
 * fitting}: `fitting` is how many from the front fit when not all do.
 */
export function planDownloads(estimates, { capBytes = DEFAULT_CAP_BYTES, rows = [], reading = null } = {}) {
  const total = estimates.reduce((sum, item) => sum + (Number(item.bytes) || 0), 0);
  const whole = planDownload(total, { capBytes, rows, reading });
  if (whole.fits) return { fits: true, total, evict: whole.evict, shortfall: 0, fitting: estimates.length };
  let fitting = 0;
  let running = 0;
  for (const item of estimates) {
    running += Number(item.bytes) || 0;
    if (!planDownload(running, { capBytes, rows, reading }).fits) break;
    fitting += 1;
  }
  return { fits: false, total, evict: [], shortfall: whole.shortfall, fitting };
}

/**
 * The library as Flipparr View shows it (the owner, 2026-10-10): a reader,
 * so only issues there are to read -- never Missing or Upcoming -- and,
 * offline (`onDevice`, the downloaded file ids), only what is on this
 * device. Runs, collections and families with nothing left go too. Pure;
 * the catalog itself is not changed.
 */
export function readerCatalog(catalog, { onDevice = null } = {}) {
  if (!catalog || !Array.isArray(catalog.series)) return catalog;
  const here = onDevice ? new Set([...onDevice].map(String)) : null;
  const readable = (fileId) => fileId != null && fileId !== "" && (!here || here.has(String(fileId)));
  const series = [];
  for (const run of catalog.series) {
    const issues = (run.issues || []).filter((issue) => readable(issue.fileId));
    const files = (run.fileDetails || []).filter((file) => readable(file.id));
    if (!files.length && !issues.length) continue;
    series.push({ ...run, issues, fileDetails: files, ...(here ? { owned: issues.length || files.length } : {}) });
  }
  const kept = new Map(series.map((run) => [String(run.id), run]));
  const runCollections = (catalog.runCollections || [])
    .map((collection) => ({ ...collection, runIds: (collection.runIds || []).filter((id) => kept.has(String(id))) }))
    .filter((collection) => collection.runIds.length);
  const families = (catalog.families || [])
    .map((family) => {
      const runs = (family.runs || []).filter((run) => kept.has(String(run.id))).map((run) => kept.get(String(run.id)));
      return { ...family, runs, runIds: runs.map((run) => run.id), runCount: runs.length };
    })
    .filter((family) => family.runs.length);
  return { ...catalog, series, runCollections, families };
}

/**
 * The download rows a cover stands for: an issue's own, or a run's,
 * collection's or story arc's issues (`issueFiles`: issue id -> file id).
 */
export function rowsFor(rows, target, { issueFiles = new Map() } = {}) {
  if (!target) return [];
  if (target.kind === "issue") return rows.filter((row) => row.fileId === String(target.fileId));
  if (target.kind === "run") return rows.filter((row) => String(row.seriesRunId) === String(target.id));
  if (target.kind === "collection") {
    const runs = new Set((target.runIds || []).map(String));
    return rows.filter((row) => runs.has(String(row.seriesRunId)));
  }
  const files = new Set((target.issueIds || []).map((id) => issueFiles.get(String(id))).filter(Boolean));
  return rows.filter((row) => files.has(row.fileId) || row.groupKey === `arc:${target.id}`);
}

/**
 * What a cover's corner says (CoverStatus): `downloading` with the progress
 * of what is still coming, else `done` once something of it is here, else
 * nothing. A run with some issues here and none coming is `done`: "on this
 * device", not "all of it".
 */
export function coverStatusFor(rows, target, options = {}) {
  const mine = rowsFor(rows, target, options);
  const coming = mine.filter((row) => row.state === "downloading");
  if (coming.length) {
    return { state: "downloading", progress: coming.reduce((sum, row) => sum + progressOf(row), 0) / coming.length };
  }
  const here = mine.filter((row) => row.state === "ready" || row.state === "stale").length;
  if (!here) return null;
  return { state: "done", label: target.kind === "issue" ? "On this device" : `${here} on this device` };
}

/** Bytes as a person reads them: 950 KB, 12.4 MB, 1.8 GB. */
export function formatBytes(bytes) {
  const value = Math.max(0, Number(bytes) || 0);
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(value < 10 * 1024 * 1024 ? 1 : 0)} MB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}
