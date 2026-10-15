// How far a reader is through a cover, on it, on every host (the owner,
// 2026-10-10 and 2026-10-11; docs/STATUS_MARKS_PLAN.md): a "Read" sash across
// the top-right corner once finished, a percentage tag there while under
// way. The corner is reading's alone -- the app's download mark is bottom
// right, a collection's bottom left, a Read button top left -- and the check
// is never used here, so it keeps one meaning: read.

/** The mark in words -- "Read", "42% read", or "" for nothing yet -- for a label that must carry it itself. */
export function readStateWords({ finished = false, fraction = 0 } = {}) {
  if (finished) return "Read";
  const share = Math.max(0, Math.min(1, Number(fraction) || 0));
  if (!share) return "";
  return `${Math.min(Math.max(1, Math.round(share * 100)), 99)}% read`;
}

/** `fraction`: 0..1 of the comic (or of a run's comics) read. */
export function ReadState({ finished = false, fraction = 0 }) {
  const words = readStateWords({ finished, fraction });
  if (!words) return null;
  // Inside a button with a label of its own this role="img" is never spoken:
  // the button's label carries the words (readStateWords); this is for the eye.
  if (finished) return <span className="read-state sash" role="img" aria-label={words}><b aria-hidden="true">Read</b></span>;
  return <span className="read-state tag" role="img" aria-label={words}>{words.replace(" read", "")}</span>;
}

/**
 * A run's (or arc's) place as a fraction of it: the comics read, and the
 * share of the one under way, of all it has. One comic opened in a run of
 * twelve is a sliver, not nothing.
 */
export function placeFraction(place) {
  if (!place) return 0;
  if (place.state === "finished") return 1;
  const opened = place.state === "continue" && place.pageCount ? (Number(place.page) + 1) / place.pageCount : 0;
  const total = Number(place.total) || 0;
  return total ? Math.min(1, ((Number(place.read) || 0) + opened) / total) : opened;
}
