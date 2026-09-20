// How a reading target reads on screen.
//
// The decision itself -- which comic Read opens, and which of the six states a
// run is in -- is made by the server (`reading_target` in app.py), because the
// Comics grid needs one target per run and cannot be sent every run's file
// list to work it out. This module is the other half: what that answer says on
// a button, under it, and as a hairline across a cover, so the card, the cover
// overlay and the drawer's band all word it identically.

import { issueLabel } from "./discover.js";

export const READING_STATES = {
  unstarted: "unstarted",
  continue: "continue",
  next: "next",
  finished: "finished",
  volumeOnly: "volume-only",
  none: "none",
};

/**
 * What the button says.
 *
 * The number comes from the file the server picked, never from the run, and a
 * volume is named as a volume whatever the run's medium is: what opens is the
 * collection, not the issue somewhere inside it. Nothing records where an
 * issue begins in an omnibus, so a button offering "#7" that opens page 1 of
 * 1,100 would be a lie.
 */
export function readingLabel(target, medium) {
  if (!target || target.state === READING_STATES.none) return "";
  const number = target.issueNumber;
  const named = number ? ` ${issueLabel(number, medium)}` : "";
  switch (target.state) {
    case READING_STATES.continue: return `Continue${named}`;
    case READING_STATES.finished: return `Read again${named}`;
    case READING_STATES.volumeOnly: return `Read ${target.volumeLabel || "volume"}`;
    default: return `Read${named}`;
  }
}

/** "page 12 of 24", or nothing when a comic has not been opened. */
export function readingDetail(target) {
  if (target?.state !== READING_STATES.continue || !target.pageCount) return "";
  return `page ${target.page + 1} of ${target.pageCount}`;
}

/** How far through a comic is, 0-1, for the hairline drawn across a cover. */
export function readingFraction(target) {
  if (target?.state !== READING_STATES.continue || !target.pageCount) return 0;
  return Math.min(1, (target.page + 1) / target.pageCount);
}

/** The whole label, as a screen reader should hear it. */
export function readingAriaLabel(target, medium, title) {
  const label = readingLabel(target, medium);
  if (!label) return "";
  const detail = readingDetail(target);
  return `${label} of ${title}${detail ? `, ${detail}` : ""}`;
}
