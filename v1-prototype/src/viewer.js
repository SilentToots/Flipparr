import { createContext, useContext } from "react";

// Reader profiles. The profile reading this page, as the server resolved it;
// null before it answers, and for the admin the whole app is theirs anyway.
// The server refuses what a reader may not do -- this only keeps a reader
// from being shown controls that would be refused.
export const ViewerContext = createContext(null);

export function useViewer() {
  return useContext(ViewerContext);
}
