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

/** A transfer's id: profile, comic and page, so one comic's can be cancelled together. */
export function transferId(profileId, fileId, index) {
  return `${profileId}:${fileId}:${index}`;
}
export function parseTransferId(id) {
  const [profileId, fileId, index] = String(id).split(":");
  return { profileId, fileId, index: Number(index) };
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

/** What a download has done so far, as a fraction for a progress bar. */
export function progressOf(row) {
  return row.pageCount ? Math.min(1, (row.pagesDone?.length || 0) / row.pageCount) : 0;
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

/** Bytes as a person reads them: 950 KB, 12.4 MB, 1.8 GB. */
export function formatBytes(bytes) {
  const value = Math.max(0, Number(bytes) || 0);
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(value < 10 * 1024 * 1024 ? 1 : 0)} MB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}
