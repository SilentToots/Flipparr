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
 * What the button says, about the run rather than about one comic.
 *
 * "Continue Series" and "#7 · page 12 of 24" say two different things, and
 * only the first is the action. The issue is the detail under it.
 *
 * Restart is offered once every readable issue is finished, and it means the
 * run from its first issue -- not the last one read again, which is what the
 * button used to do and what its label would now be lying about.
 */
export function readingLabel(target) {
  switch (target?.state) {
    case READING_STATES.continue:
    case READING_STATES.next: return "Continue Series";
    case READING_STATES.finished: return "Restart Series";
    case READING_STATES.none:
    case undefined: return "";
    default: return "Start Series";
  }
}

/**
 * Just which comic opens: "#7", "Vol. 2", or nothing.
 *
 * The button is one line -- two lines of text stop reading as a button -- and
 * at 320px one line holds the action and the issue but not the page. The page
 * is still on the cover as a hairline, and in the button's accessible name.
 */
export function readingIssue(target, medium) {
  if (!target || target.state === READING_STATES.none) return "";
  // A volume is named as a volume whatever the run's medium is: what opens is
  // the collection, and nothing records where an issue begins inside it.
  return target.volumeLabel
    || (target.issueNumber ? issueLabel(target.issueNumber, medium) : "");
}

/** Which comic that opens, and where in it: "#7 · page 12 of 24". */
export function readingDetail(target, medium) {
  if (!target || target.state === READING_STATES.none) return "";
  const named = readingIssue(target, medium);
  if (target.state === READING_STATES.continue && target.pageCount) {
    const place = `page ${target.page + 1} of ${target.pageCount}`;
    return named ? `${named} · ${place}` : place;
  }
  return named;
}

/** How far through a comic is, 0-1, for the hairline drawn across a cover. */
export function readingFraction(target) {
  if (target?.state !== READING_STATES.continue || !target.pageCount) return 0;
  return Math.min(1, (target.page + 1) / target.pageCount);
}

/** The whole thing, as a screen reader should hear it. */
export function readingAriaLabel(target, medium, title) {
  const label = readingLabel(target);
  if (!label) return "";
  const detail = readingDetail(target, medium);
  return `${label}, ${title}${detail ? `, ${detail}` : ""}`;
}
