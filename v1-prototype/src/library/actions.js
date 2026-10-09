// What a profile does to its own reading, shared by the web app and Flipparr
// View: mark read or unread, rate, keep a reading list, and open a comic from
// a run or a story arc. Each is this profile's own place, so none asks for
// confirmation; the drawers and grids re-read their state after.
import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "../api.js";
import { EMPTY_ENTRIES, withEntry } from "../reading-list-tab.js";

const json = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/**
 * The comic a Read button means. Callers know different things: the Continue
 * shelf and the issue rows know a file, a card in the grid may only know a run
 * that has never been opened. Resolves to a file id, or null when there is
 * nothing readable yet.
 */
export async function readableFile(target) {
  const fileId = typeof target === "string" ? target : target?.id;
  if (fileId) return String(fileId);
  // A run, or a story arc, resolves to the comic its Read button means.
  const path = target?.runId ? `/api/v1/series/${target.runId}/reading`
    : target?.listId ? `/api/v1/reading-lists/${target.listId}` : null;
  if (!path) return null;
  const data = await apiRequest(path);
  return data?.resume?.fileId ? String(data.resume.fileId) : null;
}

/**
 * `notify(message, tone)` says what happened (the toast). `viewerId` and
 * `ready` load this profile's reading list once it is known. `afterRating`
 * runs after a rating is saved: the web reloads its catalog there.
 */
export function useReadingActions({ notify, viewerId = null, ready = true, afterRating = null }) {
  const [readingVersion, setReadingVersion] = useState(0);
  const noteReadingChanged = useCallback(() => setReadingVersion((version) => version + 1), []);
  const [readingList, setReadingList] = useState(null);

  useEffect(() => {
    if (!ready) return undefined;
    let live = true;
    setReadingList(null);
    apiRequest("/api/v1/me/reading-list")
      .then((data) => { if (live) setReadingList(data); })
      .catch(() => { if (live) setReadingList(EMPTY_ENTRIES); });
    return () => { live = false; };
  }, [viewerId, ready]);

  async function toggleReadingList(kind, id, on) {
    const before = readingList;
    setReadingList((current) => withEntry(current, kind, id, on));
    try {
      const saved = await apiRequest(`/api/v1/me/reading-list/${kind}/${id}`, on
        ? { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }
        : { method: "DELETE" });
      setReadingList(saved);
      notify(on ? "Added to your reading list" : "Taken off your reading list");
    } catch (error) {
      setReadingList(before);
      notify(error.message, "error");
    }
  }

  async function markIssueRead(issue, read) {
    try {
      await apiRequest(`/api/v1/files/${issue.fileId}/progress`, json({ read }));
      noteReadingChanged();
    } catch (error) {
      notify(error.message, "error");
    }
  }
  // Every run of a collection read or unread, then one word about it.
  async function markRunsRead(runs, read) {
    for (const run of runs) {
      await apiRequest(`/api/v1/series/${run.id}/reading`, json({ read }));
    }
    noteReadingChanged();
    notify(`${runs.length} run${runs.length === 1 ? "" : "s"} marked as ${read ? "read" : "unread"}`);
  }
  async function markRunRead(series, read) {
    try {
      await apiRequest(`/api/v1/series/${series.id}/reading`, json({ read }));
      noteReadingChanged();
      notify(read ? `${series.title} marked as read` : `${series.title} marked as unread`);
    } catch (error) {
      notify(error.message, "error");
    }
  }

  /** Your rating for a run or one of its issues; null takes it back. */
  async function saveRating(path, rating, subject) {
    try {
      await apiRequest(path, json({ rating }));
      await afterRating?.();
      notify(rating ? `${subject} rated ${rating} star${rating === 1 ? "" : "s"}` : `Rating removed from ${subject}`);
    } catch (error) { notify(error.message, "error"); }
  }
  const rateSeries = (series, rating) => saveRating(`/api/v1/series/${series.id}/rating`, rating, series.title);
  const rateIssue = (issue, rating) => saveRating(`/api/v1/issues/${issue.id}/rating`, rating,
    `${issue.contextLabel || "Issue"} #${issue.number}`);

  return {
    // `readingList` is null until it has loaded: a tab can say it is loading.
    readingVersion, noteReadingChanged, readingList,
    toggleReadingList, markIssueRead, markRunRead, markRunsRead, rateSeries, rateIssue,
  };
}
