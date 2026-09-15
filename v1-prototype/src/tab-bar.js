// Whether the phone's tab bar is tucked away, decided one scroll frame at a
// time. Kept apart from the component so the iOS cases can be tested without
// a phone.

export const TAB_BAR_SCROLL_SLACK = 6;
export const TAB_BAR_TOP_ZONE = 64;
export const TAB_BAR_BOTTOM_ZONE = 48;

// Scrolling down tucks the bar; scrolling back up, or being near the top,
// brings it back.
//
// iOS reports positions past either end of the page while it rubber-bands,
// then settles back. Read raw, the settle at the bottom is an upward scroll,
// so every bounce opened the bar and the next one closed it. The position is
// held to the page's real range, and near the bottom an upward move is taken
// for the settle it almost always is -- the reader has to come further up the
// page before the bar returns.
export function nextTabBarState({ scrollY, maxScroll, lastY, collapsed }) {
  const max = Math.max(0, maxScroll);
  const y = Math.min(Math.max(scrollY, 0), max);
  if (y < TAB_BAR_TOP_ZONE) return { collapsed: false, lastY: y };
  const delta = y - lastY;
  if (Math.abs(delta) <= TAB_BAR_SCROLL_SLACK) return { collapsed, lastY };
  if (delta > 0) return { collapsed: true, lastY: y };
  if (y >= max - TAB_BAR_BOTTOM_ZONE) return { collapsed, lastY: y };
  return { collapsed: false, lastY: y };
}
