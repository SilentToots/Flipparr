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
export function clampPan({ x = 0, y = 0 } = {}, scale, viewport = { width: 0, height: 0 }, page = viewport, loose = false) {
  const zoom = clampZoom(scale);
  const scaled = { width: (page.width || 0) * zoom, height: (page.height || 0) * zoom };
  // Loose is panel view's bound: a panel is centred wherever it sits on the
  // page, with black around the page's edge if that is what centring takes
  // -- Kindle's framing. The page can go no further than half off screen.
  const limitX = loose
    ? Math.max(scaled.width, viewport.width || 0) / 2
    : Math.max(0, (scaled.width - (viewport.width || 0)) / 2);
  const limitY = loose
    ? Math.max(scaled.height, viewport.height || 0) / 2
    : Math.max(0, (scaled.height - (viewport.height || 0)) / 2);
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

// ---- Panel view -------------------------------------------------------------
//
// Reading a page one panel at a time, the way Kindle's Panel View and
// Comixology's Guided View do. The server says where the panels are, in
// reading order; these decide where the page has to sit to show one, and
// where the next one is. A page the server could not read gets four
// quadrants, Kindle's own fallback, so there is never a dead page.

/** The margin left around a panel, so its border is not the screen's edge. */
export const PANEL_MARGIN = 0.04;

/**
 * Where the page sits to show one panel: the zoom that fits it with a margin,
 * and the pan that centres it. `rect` is normalised, `page` the page's 1x
 * layout size, `viewport` the surface. A panel wider than the fitted page
 * simply reads at 1x -- the clamp never zooms out past the whole page -- and
 * the panel is centred regardless of where it sits on the page, with black
 * past the page's edge if need be: on a phone the page fits by width, and a
 * bound at the page's edge left a low panel sitting low on the screen.
 */
export function panelFocus(rect, viewport, page) {
  const width = Math.max(1, rect.w * page.width);
  const height = Math.max(1, rect.h * page.height);
  const room = 1 - 2 * PANEL_MARGIN;
  const zoom = clampZoom(Math.min((viewport.width * room) / width, (viewport.height * room) / height));
  // The page is centred at pan zero, so a panel right of the page's centre
  // needs the page moved left by that much, scaled.
  const centreX = (rect.x + rect.w / 2 - 0.5) * page.width * zoom;
  const centreY = (rect.y + rect.h / 2 - 0.5) * page.height * zoom;
  const pan = clampPan({ x: -centreX, y: -centreY }, zoom, viewport, page, true);
  // `-0` is a value Object.is tells apart; a pan of nothing is 0.
  return { zoom, pan: { x: pan.x || 0, y: pan.y || 0 } };
}

/**
 * The next place in panel view, or null past either end of the comic.
 *
 * `counts` is how many panels each page has. Stepping off the last panel of
 * a page lands on the first of the next; stepping back off the first lands
 * on the last of the previous. Null past the end is what lets the finish
 * drawer fire exactly as it does when paging.
 */
export function panelStep(place, action, counts) {
  const pages = counts.length;
  const last = (page) => Math.max(0, (counts[page] ?? 1) - 1);
  const { page, panel } = place;
  if (action === "next") {
    if (panel < last(page)) return { page, panel: panel + 1 };
    return page + 1 < pages ? { page: page + 1, panel: 0 } : null;
  }
  if (action === "previous") {
    if (panel > 0) return { page, panel: panel - 1 };
    return page > 0 ? { page: page - 1, panel: last(page - 1) } : null;
  }
  if (action === "first") return { page: 0, panel: 0 };
  if (action === "last") return { page: pages - 1, panel: last(pages - 1) };
  return place;
}

/** Kindle's Virtual Panels: the page in four, in reading order. */
export function quadrantPanels(direction) {
  const [first, second] = direction === READING_DIRECTIONS.rtl ? [0.5, 0] : [0, 0.5];
  return [
    { x: first, y: 0, w: 0.5, h: 0.5 }, { x: second, y: 0, w: 0.5, h: 0.5 },
    { x: first, y: 0.5, w: 0.5, h: 0.5 }, { x: second, y: 0.5, w: 0.5, h: 0.5 },
  ];
}

/**
 * Where the page's mask leaves the panel at full strength while the rest
 * dims (styles.css `.reader-page.scrim`). The window is the panel with a
 * little room around it, so a border is never shaved. Sizes are percentages
 * of the page; the position is the percentage CSS wants, which it measures
 * against the room left over rather than the page -- a window as wide as the
 * page has nowhere to go.
 */
export function panelMask(rect, pad = PANEL_MARGIN / 4) {
  const x0 = Math.max(0, rect.x - pad);
  const y0 = Math.max(0, rect.y - pad);
  const w = Math.min(1, rect.x + rect.w + pad) - x0;
  const h = Math.min(1, rect.y + rect.h + pad) - y0;
  const at = (offset, size) => (size >= 1 ? 0 : (offset / (1 - size)) * 100);
  return { w: w * 100, h: h * 100, x: at(x0, w), y: at(y0, h) };
}

const READER_PREFS_KEY = "flipparr.reader";

/** How the reader was left in this browser; guarded like the library's memory. */
export function loadReaderPrefs(storage = globalThis.localStorage) {
  let raw = null;
  try { raw = storage?.getItem(READER_PREFS_KEY) ?? null; } catch { raw = null; }
  let saved = {};
  try { saved = raw ? JSON.parse(raw) : {}; } catch { saved = {}; }
  if (!saved || typeof saved !== "object") saved = {};
  return { panelMode: saved.panelMode === true, panelScrim: saved.panelScrim !== false };
}

export function saveReaderPrefs(prefs, storage = globalThis.localStorage) {
  try {
    storage?.setItem(READER_PREFS_KEY, JSON.stringify({ panelMode: Boolean(prefs.panelMode), panelScrim: prefs.panelScrim !== false }));
  } catch {
    // The reader still works; it just forgets.
  }
}
