// How the reader behaves, decided away from the JSX so it can be tested.
//
// Reading direction is the reason this file exists. A manga run reads right to
// left, which inverts what a tap on the left of the screen means, what the
// right arrow does, and which way a swipe turns -- and every one of those is a
// separate line in a component, which is how a reader ends up turning pages
// backwards in one gesture and forwards in another. Here it is one decision,
// made once, with a test for every combination.

export const READING_DIRECTIONS = { ltr: "ltr", rtl: "rtl" };

/** Right to left for manga, left to right for everything else; a run may say otherwise. */
export function readingDirection(medium, override) {
  if (override === READING_DIRECTIONS.rtl || override === READING_DIRECTIONS.ltr) return override;
  return medium === "manga" ? READING_DIRECTIONS.rtl : READING_DIRECTIONS.ltr;
}

/**
 * What a key press means, in reading order.
 *
 * The arrows follow the screen: the right arrow moves right, which is forward
 * in a comic and *backward* in manga. Space and the page keys follow the
 * story instead, because that is what a thumb on a page-down key means in
 * either. This split is what Tachiyomi and Panels do, and it is what hands
 * expect; do not "fix" it to one or the other without changing the tests
 * below, which exist to say it was deliberate.
 */
export function actionForKey(key, direction) {
  const forward = direction === READING_DIRECTIONS.rtl ? "ArrowLeft" : "ArrowRight";
  const back = direction === READING_DIRECTIONS.rtl ? "ArrowRight" : "ArrowLeft";
  if (key === forward) return "next";
  if (key === back) return "previous";
  if ([" ", "Spacebar", "PageDown", "Enter"].includes(key)) return "next";
  if (["PageUp", "Backspace"].includes(key)) return "previous";
  if (key === "Home") return "first";
  if (key === "End") return "last";
  return null;
}

/** Where a tap lands: the outer thirds turn pages, the middle shows the chrome. */
export function tapAction(x, width, direction) {
  if (!width) return "chrome";
  const edge = width * 0.3;
  if (x > width - edge) return direction === READING_DIRECTIONS.rtl ? "previous" : "next";
  if (x < edge) return direction === READING_DIRECTIONS.rtl ? "next" : "previous";
  return "chrome";
}

/**
 * Which way a swipe turns.
 *
 * Dragging the page to the left is the same gesture as tapping the right
 * edge: forward in a comic, back in manga. It was wired the other way round
 * once -- a leftward swipe read as a tap on the *left* -- and turned every
 * phone swipe backwards.
 */
export function swipeAction(dx, direction) {
  if (!dx) return null;
  return tapAction(dx < 0 ? 1 : 0, 1, direction);
}

/** The page an action lands on, never outside the comic. */
export function pageForAction(index, count, action) {
  if (!count) return 0;
  const last = count - 1;
  const moved = action === "next" ? index + 1
    : action === "previous" ? index - 1
    : action === "first" ? 0
    : action === "last" ? last
    : index;
  return Math.min(last, Math.max(0, moved));
}

/**
 * The pages worth holding in the DOM around the one being read.
 *
 * Two ahead, one behind: paging forward is instant, paging back one page is
 * too, and the window stays small on purpose. Each page is a request that, on
 * a CBR, opens the archive in a subprocess, and this server spawns a thread
 * per connection -- so "preload the whole issue" would fork dozens of them.
 */
export function pageWindow(index, count, { ahead = 2, behind = 1 } = {}) {
  const pages = [];
  for (let page = index - behind; page <= index + ahead; page += 1) {
    if (page >= 0 && page < count) pages.push(page);
  }
  return pages;
}

/** A double-page spread: the same rule the backdrop picker uses (app.py, _page_is_spread). */
export const SPREAD_RATIO = 1.15;
export const isSpread = (width, height) => Boolean(height) && width > height * SPREAD_RATIO;

export const MIN_ZOOM = 1;
export const MAX_ZOOM = 4;

/** Zoom stays between showing the page and reading the letters. */
export const clampZoom = (scale) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Number(scale) || MIN_ZOOM));

/**
 * Pan, bounded so a zoomed page cannot be pushed off the screen.
 *
 * The bound is the page's own size, not the window's: a portrait page in a
 * landscape window is narrower than the surface it sits on, and bounding by
 * the surface let it be dragged well into the black on either side. An axis
 * on which the scaled page still fits cannot be panned at all, so the page
 * stays centred on it. At 1x nothing can be panned: the offsets collapse to
 * zero rather than leaving the page slightly off-centre after zooming out.
 */
export function clampPan({ x = 0, y = 0 } = {}, scale, viewport = { width: 0, height: 0 }, page = viewport) {
  const zoom = clampZoom(scale);
  const limitX = Math.max(0, ((page.width || 0) * zoom - (viewport.width || 0)) / 2);
  const limitY = Math.max(0, ((page.height || 0) * zoom - (viewport.height || 0)) / 2);
  return {
    x: Math.min(limitX, Math.max(-limitX, x)),
    y: Math.min(limitY, Math.max(-limitY, y)),
  };
}

/**
 * Where the page sits after zooming about a point, so that what was under
 * the finger stays under it. `point` is measured from the viewport's centre,
 * which is where the page's centre sits at pan zero. Clamp the answer.
 */
export function zoomAt(point, from, to, pan = { x: 0, y: 0 }) {
  const ratio = clampZoom(to) / clampZoom(from);
  return {
    x: point.x - (point.x - pan.x) * ratio,
    y: point.y - (point.y - pan.y) * ratio,
  };
}

/** "3 pages left", the way a reader counts what is in front of them. */
export function pagesLeft(index, count) {
  const left = Math.max(0, count - index - 1);
  return left === 0 ? "Last page" : `${left} page${left === 1 ? "" : "s"} left`;
}

/**
 * Reading at night, as a filter over the page.
 *
 * Dimming the page rather than the screen is the point: the rest of the
 * reader is already black, and a phone's own brightness is a trip to
 * Control Centre. Warmth takes the blue out, which is what a Kindle's warm
 * light does and what makes a lit page bearable in a dark room.
 */
export function pageFilter({ dim = 1, warm = 0 } = {}) {
  const brightness = Math.min(1, Math.max(0.35, Number(dim) || 1));
  const warmth = Math.min(1, Math.max(0, Number(warm) || 0));
  const parts = [];
  if (brightness < 1) parts.push(`brightness(${brightness.toFixed(2)})`);
  if (warmth > 0) parts.push(`sepia(${(warmth * 0.55).toFixed(2)})`);
  return parts.length ? parts.join(" ") : "none";
}
