// What the shared screens need from whatever shows them: the reader, the
// Library's cards, shelves and drawers. The web app and Flipparr View each
// provide one (docs/OFFLINE_READING_PLAN.md, "The reader module" and
// "Phase 3b"):
//
// - `api(path, options)`: a request to the server, answering its JSON. The web
//   app's goes same-origin with the session cookie; the app's carries its token.
// - `canEditPanels`: whether this viewer is shown the panel editor (the
//   admin). The server refuses the edits otherwise; this only hides controls.
// - `pageSource(url)`, optional: a Promise of what an <img> should load for a
//   page, thumbnail or cover address. The web app gives none, and every image
//   uses the address itself, exactly as before; Flipparr View gives one,
//   because its web view cannot send the token with an image request
//   (src/reader-app/images.js).
import { createContext, useCallback, useContext, useRef, useState } from "react";

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
