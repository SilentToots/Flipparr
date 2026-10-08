// What the reader needs from whatever shows it. The web app and Flipparr
// Reader each provide one (docs/OFFLINE_READING_PLAN.md, "The reader
// module"):
//
// - `api(path, options)`: a request to the server, answering its JSON. The web
//   app's goes same-origin with the session cookie; the app's carries its token.
// - `canEditPanels`: whether this viewer is shown the panel editor (the
//   admin). The server refuses the edits otherwise; this only hides controls.
import { createContext, useContext } from "react";

export const ReaderHost = createContext(null);

export function useReaderHost() {
  const host = useContext(ReaderHost);
  if (!host) throw new Error("The reader is shown inside a ReaderHost");
  return host;
}
