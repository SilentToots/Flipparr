// How far a reader is through a cover, on it (Flipparr View; the owner's
// Kindle example, 2026-10-10): a "Read" sash across the top-right corner
// once finished, a percentage tag there while under way. It takes the
// corner the download check does not, so the two never sit on each other.
// A reader host only; the web keeps its centred mark and progress line.
import { useReaderOnly } from "../host.js";

/** `fraction`: 0..1 of the comic (or of a run's comics) read. */
export function ReadState({ finished = false, fraction = 0 }) {
  const readerOnly = useReaderOnly();
  if (!readerOnly) return null;
  if (finished) return <span className="read-state sash" role="img" aria-label="Read"><b aria-hidden="true">Read</b></span>;
  const share = Math.max(0, Math.min(1, Number(fraction) || 0));
  if (!share) return null;
  const percent = Math.max(1, Math.round(share * 100));
  const shown = Math.min(percent, 99);
  return <span className="read-state tag" role="img" aria-label={`${shown}% read`}>{shown}%</span>;
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
