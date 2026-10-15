// Coming back to Flipparr -- a tab shown again, the app brought to the
// front -- fetches the library again, as the server has it now (the owner,
// 2026-10-11: issues the server downloaded overnight stayed hidden, since
// iOS keeps Flipparr View suspended for days and a tab can sit open as
// long). Shared by the web app and Flipparr View.
import { useEffect, useRef } from "react";

/**
 * At most once a minute, so flicking between apps or tabs does not fetch
 * the whole catalog each time.
 */
export const REFRESH_ON_RETURN_MS = 60000;

/** Whether a library last fetched at `lastAt` (ms; 0 for never) is due again at `now`. */
export function dueForRefresh(lastAt, now) {
  return !lastAt || now - lastAt >= REFRESH_ON_RETURN_MS;
}

/** Whether a visibility change -- to `state` at `now`, the library last fetched at `lastAt` -- is a return that refreshes. */
export function refreshesOnReturn(state, lastAt, now) {
  return state === "visible" && dueForRefresh(lastAt, now);
}

/**
 * Calls `refresh` when the page is shown again and the library, last
 * fetched at `loadedAt.current`, is due. `enabled` false (Flipparr View
 * offline) leaves it to the host's own way back.
 */
export function useRefreshOnReturn(loadedAt, refresh, enabled = true) {
  const latest = useRef(refresh);
  latest.current = refresh;
  useEffect(() => {
    if (!enabled) return undefined;
    const onVisible = () => {
      if (!refreshesOnReturn(document.visibilityState, loadedAt.current, Date.now())) return;
      latest.current();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [loadedAt, enabled]);
}
