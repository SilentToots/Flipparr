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
 * A comic as the server packs it (2026-10-12): one file of its pages, which
 * the native side unpacks into the comic's folder as it lands; and every
 * page's kept panels in one answer, gathered into panels.json at the end.
 */
export function packagePath(profileId, fileId) {
  return `${comicFolder(profileId, fileId)}/package.zip`;
}
export function bulkPanelsPath(profileId, fileId) {
  return `${comicFolder(profileId, fileId)}/panels-all.json`;
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
  if (kind === "package") return `${profileId}:${fileId}:pkg`;
  if (kind === "allPanels") return `${profileId}:${fileId}:panels`;
  return `${profileId}:${fileId}:${kind === "panels" ? "p" : ""}${index}`;
}
export function parseTransferId(id) {
  const [profileId, fileId, part = ""] = String(id).split(":");
  if (part === "pkg") return { profileId, fileId, index: null, kind: "package" };
  if (part === "panels") return { profileId, fileId, index: null, kind: "allPanels" };
  const panels = part.startsWith("p");
  return { profileId, fileId, index: Number(panels ? part.slice(1) : part), kind: panels ? "panels" : "page" };
}

/** A download is complete once every page and every page's panels are in. */
export function downloadComplete(row) {
  return (row.pagesDone?.length || 0) >= row.pageCount && (row.panelsDone?.length || 0) >= row.pageCount;
}

/** A new row for a comic about to download, from its /offline manifest. */
export function downloadRow(manifest, { groupKey = null, at = new Date().toISOString() } = {}) {
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
    // The comic in one file, when the server packs it; else page by page.
    packageUrl: manifest.package?.url || null,
    panelsUrl: manifest.panelsUrl || null,
    received: 0,
    expected: Number(manifest.package?.bytes) || 0,
    pagesDone: [],
    panelsDone: [],
    bytes: 0,
    estimate: Number(manifest.bytesEstimate) || 0,
    state: "downloading",
    // When it was asked for: what one choice of several comics fetched
    // together is, for its progress (batchProgress).
    startedAt: at,
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
  if (row.packageUrl) {
    // One file: its bytes, as they arrive; the panels are a sliver at the end.
    const total = row.expected || row.estimate;
    const pages = (row.pagesDone?.length || 0) >= row.pageCount ? 1 : total ? Math.min(1, (row.received || 0) / total) : 0;
    const panels = (row.panelsDone?.length || 0) >= row.pageCount ? 1 : 0;
    return Math.min(1, pages * 0.97 + panels * 0.03);
  }
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
  // The sidebar's count says what is shown: the files there are to read.
  const files = series.reduce((sum, run) => sum + (run.fileDetails?.length || run.issues.length), 0);
  return { ...catalog, series, runCollections, families, stats: { ...(catalog.stats || {}), files } };
}

/**
 * The download rows a cover stands for: an issue's own, or a run's,
 * collection's or story arc's issues (`issueFiles`: issue id -> file id).
 */
/**
 * Downloads as Kindle shelves a series (the owner, 2026-10-11): the comics
 * kept from one run make one stacked card, and a run with only one comic
 * here is that comic's own card. Each entry is `{kind: "issue", row}` or
 * `{kind: "run", runId, title, rows, read, cover}` -- the run's rows in
 * issue order, how many are read, and the cover of the one read most lately
 * (else its first). Entries come most lately read or downloaded first, a
 * run by its latest comic.
 */
export function groupDownloads(rows) {
  const when = (row) => Date.parse(row.lastReadAt || row.downloadedAt || row.startedAt || 0) || 0;
  const byRun = new Map();
  const entries = [];
  for (const row of rows) {
    if (!row.seriesRunId) { entries.push({ kind: "issue", row }); continue; }
    const key = String(row.seriesRunId);
    if (!byRun.has(key)) byRun.set(key, []);
    byRun.get(key).push(row);
  }
  for (const [runId, kept] of byRun) {
    if (kept.length === 1) { entries.push({ kind: "issue", row: kept[0] }); continue; }
    const ordered = [...kept].sort((a, b) => issueOrder(a.issueNumber, b.issueNumber));
    const latest = [...kept].sort((a, b) => when(b) - when(a))[0];
    entries.push({
      kind: "run", runId, title: latest.seriesTitle, rows: ordered,
      read: kept.filter((row) => row.finished).length,
      cover: (latest.lastReadAt ? latest : ordered[0]).pageUrls?.[0] || null,
    });
  }
  const latestOf = (entry) => (entry.kind === "issue" ? when(entry.row) : Math.max(...entry.rows.map(when)));
  return entries.sort((a, b) => latestOf(b) - latestOf(a));
}

/** Issue numbers in reading order: 2 before 10, 23.1 after 23, a number before none. */
export function issueOrder(a, b) {
  const value = (number) => {
    const parsed = Number.parseFloat(String(number ?? ""));
    return Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY;
  };
  return value(a) - value(b) || String(a ?? "").localeCompare(String(b ?? ""));
}

/** The comic a run's kept issues carry on from: the first not finished, else the first. */
export function nextDownloaded(rows) {
  return rows.find((row) => !row.finished) || rows[0] || null;
}

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
 * How far a batch of downloads has got, 0..1, or null when none is coming:
 * the comics still coming and those asked for with them (since the first
 * still coming was asked for) that are done -- here, or failed. Counting
 * only those still coming, the ring would go back each time one finished.
 * A row from before `startedAt` counts only while it is coming.
 */
export function batchProgress(rows) {
  const coming = rows.filter((row) => row.state === "downloading");
  if (!coming.length) return null;
  const since = coming.map((row) => row.startedAt).filter(Boolean).sort()[0];
  const finished = since ? rows.filter((row) => row.state !== "downloading" && row.startedAt && row.startedAt >= since) : [];
  const done = finished.length + coming.reduce((sum, row) => sum + progressOf(row), 0);
  return done / (finished.length + coming.length);
}

/**
 * What a cover's corner says (CoverStatus): `downloading` with the progress
 * of what is still coming, else `done` once something of it is here, else
 * nothing. A run with some issues here and none coming is `done`: "on this
 * device", not "all of it".
 */
export function coverStatusFor(rows, target, options = {}) {
  const mine = rowsFor(rows, target, options);
  const progress = batchProgress(mine);
  if (progress !== null) return { state: "downloading", progress };
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
