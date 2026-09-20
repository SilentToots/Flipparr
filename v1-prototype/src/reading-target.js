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
 * The verb: what pressing the button does to the comic it names.
 *
 * "Continue" only for a comic actually part-read. Finishing one issue and
 * moving to the next is beginning that next issue, not continuing it -- the
 * button used to say "Continue Series" there, which was about the run when
 * the thing on the button is an issue.
 *
 * Restart is offered once every readable issue is finished, and it opens the
 * run's first issue from its first page.
 */
export function readingVerb(target) {
  switch (target?.state) {
    case READING_STATES.continue: return "Continue";
    case READING_STATES.finished: return "Restart";
    case READING_STATES.none:
    case undefined: return "";
    default: return "Begin";
  }
}

/**
 * Just which comic opens: "#7", "Vol. 2", or nothing.
 *
 * At 320px one line holds the verb and the issue but not the page. The page
 * is still on the cover as a hairline, and in the button's accessible name.
 */
export function readingIssue(target, medium) {
  if (!target || target.state === READING_STATES.none) return "";
  // A volume is named as a volume whatever the run's medium is: what opens is
  // the collection, and nothing records where an issue begins inside it.
  return target.volumeLabel
    || (target.issueNumber ? issueLabel(target.issueNumber, medium) : "");
}

/** The comic as a noun: "Issue #7", or "Vol. 2", which already names itself. */
export function readingNoun(target, medium) {
  const named = readingIssue(target, medium);
  return named.startsWith("#") ? `Issue ${named}` : named;
}

/**
 * What the button says: "Continue Issue #7", "Begin Vol. 2", "Restart Issue #1".
 *
 * About the comic that opens, not the run it belongs to. A comic with no
 * number gets the verb alone rather than a number invented for it.
 */
export function readingLabel(target, medium) {
  return [readingVerb(target), readingNoun(target, medium)].filter(Boolean).join(" ");
}

/** Where in the comic, for one part-read: "page 12 of 24", else nothing. */
export function readingPlace(target) {
  if (target?.state !== READING_STATES.continue || !target.pageCount) return "";
  return `page ${target.page + 1} of ${target.pageCount}`;
}

/** Which comic that opens, and where in it: "#7 · page 12 of 24". */
export function readingDetail(target, medium) {
  if (!target || target.state === READING_STATES.none) return "";
  const named = readingIssue(target, medium);
  const place = readingPlace(target);
  if (place) return named ? `${named} · ${place}` : place;
  return named;
}

/** How far through a comic is, 0-1, for the hairline drawn across a cover. */
export function readingFraction(target) {
  if (target?.state !== READING_STATES.continue || !target.pageCount) return 0;
  return Math.min(1, (target.page + 1) / target.pageCount);
}

/** The whole thing, as a screen reader should hear it. */
export function readingAriaLabel(target, medium, title) {
  const label = readingLabel(target, medium);
  if (!label) return "";
  const place = readingPlace(target);
  return `${label}, ${title}${place ? `, ${place}` : ""}`;
}
