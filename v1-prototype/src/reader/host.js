// What the reader needs from whatever shows it. The web app and Flipparr
// Reader each provide one (docs/OFFLINE_READING_PLAN.md, "The reader
// module"):
//
// - `api(path, options)`: a request to the server, answering its JSON. The web
//   app's goes same-origin with the session cookie; the app's carries its token.
// - `canEditPanels`: whether this viewer is shown the panel editor (the
//   admin). The server refuses the edits otherwise; this only hides controls.
// - `pageSource(url)`, optional: a Promise of what an <img> should load for a
//   page or thumbnail address. The web app gives none, and the reader uses the
//   address itself, exactly as before; Flipparr Reader gives one, because its
//   web view cannot send the token with an image request
//   (src/reader-app/images.js).
import { createContext, useCallback, useContext, useRef, useState } from "react";

export const ReaderHost = createContext(null);

export function useReaderHost() {
  const host = useContext(ReaderHost);
  if (!host) throw new Error("The reader is shown inside a ReaderHost");
  return host;
}

/**
 * `src(url)` for an <img>: the address itself where the host has no
 * `pageSource` (the web app; no state, no effect on rendering), else the
 * resolved address once it is ready and `undefined` until then.
 */
export function usePageSrc() {
  const { pageSource } = useReaderHost();
  const [ready, setReady] = useState({});
  const asked = useRef(new Set());
  return useCallback((url) => {
    if (!pageSource || !url) return url;
    if (ready[url]) return ready[url];
    if (!asked.current.has(url)) {
      asked.current.add(url);
      pageSource(url).then(
        (src) => setReady((current) => ({ ...current, [url]: src })),
        // The address itself, which fails to load: the reader's own
        // failed-page state and its retry take it from there.
        () => { asked.current.delete(url); setReady((current) => ({ ...current, [url]: url })); },
      );
    }
    return undefined;
  }, [pageSource, ready]);
}
