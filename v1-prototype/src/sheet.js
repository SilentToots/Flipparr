// A phone sheet under a finger: whether a touch that has moved is a pull on
// the sheet, the page's own scroll or swipe, or too early to say. Pure, so
// the decision is tested away from the DOM.
//
// A pull is claimed only once the finger has clearly moved, and only when it
// heads down (or up, on the grabber, towards a taller detent) with everything
// under it scrolled to its top. Deciding on the first pixel used to claim
// sideways swipes -- a carousel's -- before they were sideways, and the
// browser, once told no, would not scroll for the rest of the gesture.
export const SHEET_PULL_SLOP = 6;

/**
 * "pull" to take the gesture, "pass" to leave it to the page, "wait" to see
 * more of it. `atTop`: every scroller between the finger and the sheet is at
 * its top. `onGrabber`: the touch began on the grabber, which pulls even when
 * the sheet does not pull from anywhere (`pullAnywhere`) and may pull up.
 */
export function sheetPullDecision({ dx, dy, atTop, onGrabber, pullAnywhere }) {
  if (!onGrabber && !pullAnywhere) return "pass";
  if (Math.abs(dx) + Math.abs(dy) < SHEET_PULL_SLOP) return "wait";
  if (Math.abs(dx) >= Math.abs(dy)) return "pass";
  if (dy < 0 && !onGrabber) return "pass";
  if (dy > 0 && !onGrabber && !atTop) return "pass";
  return "pull";
}
