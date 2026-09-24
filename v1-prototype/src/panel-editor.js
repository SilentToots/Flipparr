// The geometry of correcting a page's panels by hand, in the page's own
// normalised space (0-1 on each axis), with no DOM: what a pointer landed on,
// what a drag does to a rectangle, adding, removing and ordering. The editor
// (App.jsx, PanelEditor) is a thin layer over this, and this is what is tested.
//
// Conventions follow Kindle Create's Guided View editor: drag the middle of a
// rectangle to move it, a corner to resize both ways, a side to move that
// edge; a numbered circle shows each panel's place in the reading order.

/** A panel narrower or shorter than this cannot be read, and the server refuses it. */
export const PANEL_MIN_SIDE = 0.02;
/** The size a new panel starts at, as a share of the page's side. */
export const NEW_PANEL_SIDE = 0.3;

export const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const round = (value) => Math.round(value * 10000) / 10000;

/** A rectangle kept on the page, no thinner than a sliver, rounded for storage. */
export function normalise(rect) {
  const w = clamp(rect.w, PANEL_MIN_SIDE, 1);
  const h = clamp(rect.h, PANEL_MIN_SIDE, 1);
  const x = clamp(rect.x, 0, 1 - w);
  const y = clamp(rect.y, 0, 1 - h);
  return { x: round(x), y: round(y), w: round(w), h: round(h) };
}

/** Where a handle sits on a rectangle, as a point. */
export function handlePoint(rect, handle) {
  const midX = rect.x + rect.w / 2;
  const midY = rect.y + rect.h / 2;
  const right = rect.x + rect.w;
  const bottom = rect.y + rect.h;
  return {
    nw: { x: rect.x, y: rect.y }, n: { x: midX, y: rect.y }, ne: { x: right, y: rect.y },
    e: { x: right, y: midY }, se: { x: right, y: bottom }, s: { x: midX, y: bottom },
    sw: { x: rect.x, y: bottom }, w: { x: rect.x, y: midY },
  }[handle];
}

/**
 * What a pointer at `point` lands on. `reach` is how far a handle reaches, in
 * page units on each axis (a touch target of 44px, divided by the page's
 * rendered size), and only the selected panel's handles are live -- on a
 * page of small panels every corner would otherwise be a handle of something.
 * Panels are tried front to back, the selected one first, so a small panel
 * drawn over a large one can still be picked up.
 */
export function hitTest(panels, point, reach, selected = -1) {
  if (selected >= 0 && selected < panels.length) {
    const rect = panels[selected];
    for (const handle of HANDLES) {
      const at = handlePoint(rect, handle);
      if (Math.abs(point.x - at.x) <= reach.x && Math.abs(point.y - at.y) <= reach.y) {
        return { index: selected, part: handle };
      }
    }
  }
  const order = [...panels.keys()].sort((a, b) => {
    if (a === selected) return -1;
    if (b === selected) return 1;
    return panels[a].w * panels[a].h - panels[b].w * panels[b].h;
  });
  for (const index of order) {
    const rect = panels[index];
    if (point.x >= rect.x && point.x <= rect.x + rect.w && point.y >= rect.y && point.y <= rect.y + rect.h) {
      return { index, part: "body" };
    }
  }
  return null;
}

/**
 * The rectangle a drag makes of `start`: moved whole from its body, or with
 * the dragged handle's edges following the pointer. An edge dragged past its
 * opposite stops at the minimum size rather than turning the panel inside
 * out, and nothing leaves the page.
 */
export function dragRect(start, part, dx, dy) {
  if (part === "body") return normalise({ ...start, x: start.x + dx, y: start.y + dy });
  let { x, y, w, h } = start;
  const right = x + w;
  const bottom = y + h;
  if (part.includes("w")) { const nx = clamp(x + dx, 0, right - PANEL_MIN_SIDE); w = right - nx; x = nx; }
  if (part.includes("e")) { w = clamp(right + dx, x + PANEL_MIN_SIDE, 1) - x; }
  if (part.includes("n")) { const ny = clamp(y + dy, 0, bottom - PANEL_MIN_SIDE); h = bottom - ny; y = ny; }
  if (part.includes("s")) { h = clamp(bottom + dy, y + PANEL_MIN_SIDE, 1) - y; }
  return normalise({ x, y, w, h });
}

/** The rectangle drawn between two points, in either direction. */
export function drawnRect(from, to) {
  const x = Math.min(from.x, to.x);
  const y = Math.min(from.y, to.y);
  return normalise({ x, y, w: Math.abs(to.x - from.x), h: Math.abs(to.y - from.y) });
}

/** Whether a drawn rectangle is a panel and not a slip of the finger. */
export function isDrawn(rect) {
  return rect.w >= PANEL_MIN_SIDE * 2 && rect.h >= PANEL_MIN_SIDE * 2;
}

/** A new panel in the page's middle, or nudged off any that already sits there. */
export function newPanel(panels) {
  let rect = normalise({ x: (1 - NEW_PANEL_SIDE) / 2, y: (1 - NEW_PANEL_SIDE) / 2, w: NEW_PANEL_SIDE, h: NEW_PANEL_SIDE });
  for (let tries = 0; tries < 8 && panels.some((p) => p.x === rect.x && p.y === rect.y); tries += 1) {
    rect = normalise({ ...rect, x: rect.x + 0.04, y: rect.y + 0.04 });
  }
  return rect;
}

/** The panel moved by the keyboard: arrows nudge, with Shift they resize. */
export function nudge(rect, key, step, resize = false) {
  const dx = key === "ArrowLeft" ? -step : key === "ArrowRight" ? step : 0;
  const dy = key === "ArrowUp" ? -step : key === "ArrowDown" ? step : 0;
  if (!dx && !dy) return rect;
  return resize ? dragRect(rect, "se", dx, dy) : dragRect(rect, "body", dx, dy);
}

/** The panels with one taken out, and where the selection lands after. */
export function removeAt(panels, index) {
  const next = panels.filter((_, at) => at !== index);
  return { panels: next, selected: next.length ? Math.min(index, next.length - 1) : -1 };
}

/**
 * Ordering by tapping: each tap gives the next number to the panel tapped,
 * once per panel; tapping one already numbered is ignored. `sequence` is
 * the indexes tapped so far. Done when every panel has a number, at which
 * point the panels come back rearranged into that order.
 */
export function tapOrder(sequence, index, count) {
  if (index < 0 || index >= count || sequence.includes(index)) return { sequence, done: false };
  const next = [...sequence, index];
  return { sequence: next, done: next.length === count };
}

export function applyOrder(panels, sequence) {
  return sequence.map((index) => panels[index]);
}

/** The list the server takes: rectangles only, in reading order, rounded. */
export function toPayload(panels) {
  return panels.map((rect) => normalise(rect));
}

/**
 * Panels as the reader holds them (in reading order, with ids), as the
 * editor holds them (plain rectangles). A page with no reading yet, or the
 * quadrant fallback, starts the editor empty rather than with four squares
 * to delete.
 */
export function fromReading(reading) {
  if (!reading?.segmented || !Array.isArray(reading.panels)) return [];
  return reading.panels.map(({ x, y, w, h }) => normalise({ x, y, w, h }));
}
