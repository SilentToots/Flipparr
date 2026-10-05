// Settings -> System: the labels and summaries for /api/v1/system/status.
// Pure, so the wording is tested without a browser.

const WORKER_STATES = {
  running: { label: "Running", tone: "green" },
  quiet: { label: "No recent activity", tone: "amber" },
  restarting: { label: "Restarting", tone: "amber" },
  stopped: { label: "Stopped", tone: "red" },
  off: { label: "Not started", tone: "muted" },
};

/** A worker's state as a badge: label and tone. */
export function workerBadge(state) {
  return WORKER_STATES[state] || { label: "Unknown", tone: "muted" };
}

const SCAN_STATES = {
  queued: { label: "Waiting to start", tone: "violet" },
  scanning: { label: "Scanning", tone: "violet" },
  complete: { label: "Complete", tone: "green" },
  completed: { label: "Complete", tone: "green" },
  failed: { label: "Failed", tone: "red" },
};

/** A library scan's state as a badge. */
export function scanBadge(status) {
  return SCAN_STATES[status] || { label: status ? String(status)[0].toUpperCase() + String(status).slice(1) : "Unknown", tone: "muted" };
}

/** What to tell someone about a worker that is not simply running. */
export function workerAdvice(worker) {
  if (worker?.state === "stopped") return "It stopped after restarting too many times in an hour. Restart Flipparr once the problem below is fixed.";
  if (worker?.state === "restarting") return "It stopped and is being started again.";
  if (worker?.state === "quiet") return "It has not reported in for longer than usual. Restarting Flipparr will start it again.";
  return "";
}

/** "acquisition_import_cycle_failed" -> "Acquisition import cycle failed". */
export function problemTitle(event) {
  const words = String(event || "").replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "Problem";
}

/** Bytes as people read them: "812 MB", "1.4 GB". */
export function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1000 && unit < units.length - 1) {
    size /= 1000;
    unit += 1;
  }
  return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

const SOURCE_NAMES = { sabnzbd: "Usenet", qbittorrent: "Torrent", direct_site: "Direct", manual: "Upload" };

/**
 * What the background work has in hand, as rows of label and value, plus
 * where to go to act on it. Empty groups are left out.
 */
export function waitingWork(work) {
  const rows = [];
  const downloads = (work?.downloads || []).reduce((total, item) => total + Number(item.count || 0), 0);
  if (downloads) {
    const bySource = {};
    for (const item of work.downloads) bySource[item.source] = (bySource[item.source] || 0) + Number(item.count || 0);
    const detail = Object.entries(bySource).map(([source, count]) => `${count} ${SOURCE_NAMES[source] || source}`).join(", ");
    rows.push({ id: "downloads", label: "Downloads in progress", value: `${downloads} (${detail})`, go: "requests" });
  }
  const wanted = work?.wanted?.byStatus || {};
  // "waiting" is an issue not published yet: watched, not searched for.
  const searching = Number(wanted.queued || 0) + Number(wanted.searching || 0);
  if (searching) rows.push({ id: "wanted", label: "Wanted issues being looked for", value: String(searching), go: "requests" });
  if (Number(wanted.waiting || 0)) rows.push({ id: "upcoming", label: "Wanted issues not out yet", value: String(wanted.waiting), go: "requests" });
  if (Number(wanted.failed || 0)) rows.push({ id: "failed", label: "Wanted issues that failed", value: String(wanted.failed), go: "requests", tone: "red" });
  const metadata = work?.metadata?.byStatus || {};
  const metadataWaiting = Number(metadata.queued || 0) + Number(metadata.waiting || 0) + Number(metadata.running || 0);
  if (metadataWaiting) rows.push({ id: "metadata", label: "Runs waiting for metadata", value: String(metadataWaiting) });
  if (Number(metadata.review || 0) + Number(metadata.failed || 0)) {
    rows.push({ id: "metadata-review", label: "Runs whose metadata needs a look", value: String(Number(metadata.review || 0) + Number(metadata.failed || 0)), go: "health", tone: "amber" });
  }
  return rows;
}

/** Metadata sources asked to wait, with until when. */
export function coolingProviders(work, now = new Date()) {
  return (work?.providers || [])
    .filter((item) => item.waitUntil && new Date(item.waitUntil) > now)
    .map((item) => ({ provider: item.provider, until: item.waitUntil, failures: item.consecutiveFailures, lastError: item.lastError }));
}

/** "just now", "5 minutes ago", "3 hours ago", "yesterday", "on 3 Oct". */
export function agoPhrase(iso, now = new Date()) {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const minutes = Math.max(0, Math.floor((now.getTime() - then.getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  if (hours < 48) return "yesterday";
  return `on ${then.toLocaleDateString("en-GB", { day: "numeric", month: "short" })}`;
}
