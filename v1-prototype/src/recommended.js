// Comics' Recommended tab: shelves of what to pick up, after Komga's home.
//
// Keep Reading is what the server already says to carry on with
// (`GET /api/v1/reading`: a part-read issue, else a run's next one). The
// other three are read off the catalog the page already has, which the
// server has trimmed to what this profile may see -- so none of these can
// show a reader a run above their limit.
//
// Pure: the tab in App.jsx draws them.

export const SHELF_SIZE = 20;

const day = (value) => String(value || "").slice(0, 10);

/** Every owned issue in the catalog, with the run it belongs to. */
export function ownedIssues(series) {
  const issues = [];
  for (const run of series || []) {
    for (const issue of run.issues || []) {
      if (!issue.fileId) continue;
      issues.push({ issue, run });
    }
  }
  return issues;
}

/** An issue as a shelf card. */
export function issueCard(issue, run, extra = {}) {
  const number = issue?.number ?? extra.issueNumber;
  return {
    key: `issue-${issue?.id ?? extra.fileId}`,
    kind: "issue",
    fileId: String(issue?.fileId ?? extra.fileId ?? ""),
    runId: run ? String(run.id) : extra.runId ? String(extra.runId) : "",
    title: `${run?.title || extra.runTitle || "Untitled"}${number ? ` #${number}` : ""}`,
    cover: issue?.fileCover || issue?.cover || run?.cover || null,
    ...extra,
  };
}

/**
 * Keep Reading: the server's carry-on list, newest first as it sends it, as
 * cards. A part-read issue says its page; a run's next issue says so.
 */
export function keepReading(items, series, limit = SHELF_SIZE) {
  const byFile = new Map();
  for (const { issue, run } of ownedIssues(series)) byFile.set(String(issue.fileId), { issue, run });
  return (items || []).slice(0, limit).map((item) => {
    const found = byFile.get(String(item.fileId));
    const started = item.resume === "continue" && item.pageCount > 0;
    return issueCard(found?.issue, found?.run, {
      fileId: String(item.fileId), runId: item.seriesRunId, runTitle: item.seriesTitle,
      issueNumber: item.issueNumber, resume: true,
      progress: started ? Math.min(1, (Number(item.page) + 1) / Number(item.pageCount)) : 0,
      note: started ? `Page ${Number(item.page) + 1} of ${item.pageCount}` : "Up next",
    });
  });
}

/** Recently Released: owned issues by publication date, none from the future. */
export function recentlyReleased(series, today, limit = SHELF_SIZE) {
  const cutoff = day(today);
  return ownedIssues(series)
    .filter(({ issue }) => /^\d{4}-\d{2}-\d{2}/.test(String(issue.publicationDate || "")) && day(issue.publicationDate) <= cutoff)
    .sort((a, b) => day(b.issue.publicationDate).localeCompare(day(a.issue.publicationDate))
      || String(a.run.title).localeCompare(String(b.run.title)))
    .slice(0, limit)
    .map(({ issue, run }) => issueCard(issue, run, { note: formatDay(issue.publicationDate) }));
}

/** Recently Added Issues: owned issues by the day their file arrived. */
export function recentlyAddedIssues(series, limit = SHELF_SIZE) {
  return ownedIssues(series)
    .filter(({ issue }) => issue.addedAt)
    .sort((a, b) => String(b.issue.addedAt).localeCompare(String(a.issue.addedAt)))
    .slice(0, limit)
    .map(({ issue, run }) => issueCard(issue, run, { note: `Added ${formatDay(issue.addedAt)}` }));
}

/** Recently Added Runs: runs by the day their first comic arrived. */
export function recentlyAddedRuns(series, limit = SHELF_SIZE) {
  return (series || [])
    .filter((run) => run.firstAddedAt && !run.isCollectionSeries)
    .sort((a, b) => String(b.firstAddedAt).localeCompare(String(a.firstAddedAt)))
    .slice(0, limit)
    .map((run) => ({
      key: `run-${run.id}`, kind: "run", runId: String(run.id), run,
      title: run.title, cover: run.cover || null,
      note: /^\d{4}$/.test(String(run.year || "")) ? String(run.year) : "",
    }));
}

/** "Sep 30", or "Sep 30, 2024" outside this year. */
export function formatDay(value, now = new Date()) {
  const text = day(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return "";
  const [year, month, date] = text.split("-").map(Number);
  const shown = new Date(Date.UTC(year, month - 1, date));
  const label = shown.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  return year === now.getFullYear() ? label : `${label}, ${year}`;
}

/** Today as YYYY-MM-DD in the viewer's own time zone. */
export function localDay(now = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}
