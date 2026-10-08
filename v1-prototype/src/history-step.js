// How a change of view is written into the browser's history: pushed as a new
// entry, written over the current one, or taken as Back.
//
// The reader (?read=) and a run's drawer (?series=) live in the address. Their
// entry remembers where it was opened from (`state.from`), and closing them
// must not leave that entry behind: a Back from the Comics page used to swipe
// straight into the comic just read (owner, 2026-10-08).
//   - over a dialog's own entry (`state.dialog`): written over it;
//   - back to exactly where this entry was opened from: Back;
//   - closing the reader or drawer anywhere else (a comic read from a run's
//     drawer, which closes the drawer): written over their entry;
//   - otherwise pushed, remembering `from` when it opens the reader or drawer.

const IN_ADDRESS = ["read", "series"];

/** `here` and `target` are path + search; `state` is history.state. */
export function historyStep(here, target, state) {
  if (target === here) return { kind: "none" };
  if (state?.dialog) return { kind: "replace", state: null };
  if (state?.from === target) return { kind: "back" };
  const now = new URLSearchParams(here.split("?")[1] || "");
  const next = new URLSearchParams(target.split("?")[1] || "");
  if (IN_ADDRESS.some((key) => now.has(key) && !next.has(key))) return { kind: "replace", state: null };
  const opens = IN_ADDRESS.some((key) => next.has(key) && !now.has(key));
  return { kind: "push", state: opens ? { from: here } : null };
}
