// What the shared screens need from whatever shows them: the reader, the
// Library's cards, shelves and drawers. The web app and Flipparr View each
// provide one (docs/OFFLINE_READING_PLAN.md, "The reader module" and
// "Phase 3b"):
//
// - `api(path, options)`: a request to the server, answering its JSON. The web
//   app's goes same-origin with the session cookie; the app's carries its token.
// - `canEditPanels`: whether this viewer is shown the panel editor (the
//   admin). The server refuses the edits otherwise; this only hides controls.
// - `canManage`, optional: false where the library is not managed from here
//   (Flipparr View: an admin reading on an iPad sees the reader's drawer, not
//   Files, Advanced, Edit or Pull). The web app leaves it out.
// - `issueExtras(issue)`, `issueBadge(issue)` and `issueMark(issue)`,
//   optional: the host's own items in an issue's menu, its badge where
//   nothing else is said, and its mark beside the number (Flipparr View's
//   downloads). The web app gives none.
// - `groupDownload(group)` and `issueDownload(issue)`, optional: Download
//   for a whole run, story arc or collection beside its bookmark, and a
//   download control on every issue, seen without a menu (Flipparr View,
//   Phase 6). Each answers `{label, busy, done, progress, onPress}` or null.
//   `group` is `{kind: "run" | "arc" | "collection", id, title, runIds?}`.
//   The web app gives none.
// - `readerOnly`, optional: a reader, not an acquisition tool (Flipparr
//   View; the owner, 2026-10-10). No owned counts or bars, and only the
//   issues there are to read: no Missing or Upcoming. The web gives none.
// - `readable({kind, id, fileId})`, optional: whether something can be read
//   here and now -- offline, only what is on the device (Flipparr View).
//   Shared screens ask it of what does not come from the catalog: story
//   arcs, reading-list entries, Keep Reading. Absent, everything is.
// - `notice`, optional: the host's word on the Comics page, under its header
//   (Flipparr View's "You're offline"). The web app gives none.
// - `shelves()`, optional: the host's own Recommended shelves (Flipparr
//   View's Downloaded). The web app gives none.
// - `pageSource(url)`, optional: a Promise of what an <img> should load for a
//   page, thumbnail or cover address. The web app gives none, and every image
//   uses the address itself, exactly as before; Flipparr View gives one,
//   because its web view cannot send the token with an image request
//   (src/reader-app/images.js).
import { createContext, useCallback, useContext, useRef, useState } from "react";
import { isAdmin } from "./profiles.js";
import { useViewer } from "./viewer.js";

export const Host = createContext(null);

export function useHost() {
  const host = useContext(Host);
  if (!host) throw new Error("Shown inside a Host");
  return host;
}

/**
 * `src(url)` for an <img>: the address itself where there is no host or it
 * has no `pageSource` (the web app; no state, no effect on rendering), else
 * the resolved address once it is ready and `undefined` until then.
 */
export function useImageSrc() {
  const pageSource = useContext(Host)?.pageSource || null;
  const [ready, setReady] = useState({});
  const asked = useRef(new Set());
  return useCallback((url) => {
    if (!pageSource || !url) return url;
    if (ready[url]) return ready[url];
    if (!asked.current.has(url)) {
      asked.current.add(url);
      pageSource(url).then(
        (src) => setReady((current) => ({ ...current, [url]: src })),
        // The address itself, which fails to load: the image's own failed
        // state (a reader page's retry, a cover's next candidate) takes it
        // from there.
        () => { asked.current.delete(url); setReady((current) => ({ ...current, [url]: url })); },
      );
    }
    return undefined;
  }, [pageSource, ready]);
}

/** The host's `readable` check, or one that lets everything through. */
export function useReadable() {
  return useContext(Host)?.readable || (() => true);
}

/** Whether the host is a reader only (`readerOnly`): ownership is not shown. */
export function useReaderOnly() {
  return Boolean(useContext(Host)?.readerOnly);
}

/**
 * Whether this viewer manages the library here: the admin, where the host
 * does not say otherwise. The server refuses what a reader may not do; this
 * only decides which controls are shown.
 */
export function useManages() {
  const viewer = useViewer();
  const host = useContext(Host);
  return isAdmin(viewer) && host?.canManage !== false;
}
