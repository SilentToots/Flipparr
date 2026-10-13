// The one stack every dialog, drawer and sheet joins (dialogs.jsx), kept as
// plain JS so it can be tested on its own. Dialogs stack: Fix match opens on
// top of the series drawer. Escape and the focus trap apply only to the one
// in front -- listener order alone cannot do this, since every dialog
// listens on document and capture order favours the one underneath.
export const openDialogs = [];
// Whether a dialog is the one in front. Keys belong to the front layer only:
// Escape closes one layer at a time, and the reader's page turns must not
// reach it while a drawer is open over it.
export const isTopDialog = (node) => Boolean(node) && openDialogs[openDialogs.length - 1] === node;
// How each open dialog closes itself (its exit animation included).
export const dialogClosers = new WeakMap();

// Told when the dialog in front changes, for a host whose Back is not the
// address (Flipparr View's edge swipe, src/reader-app/edge-back.js).
const frontListeners = new Set();
export function onFrontDialogChange(listener) {
  frontListeners.add(listener);
  return () => frontListeners.delete(listener);
}
export function frontChanged() {
  frontListeners.forEach((listener) => listener(openDialogs[openDialogs.length - 1] || null));
}

/**
 * The layer in front that Back may close in a host without an address: any
 * drawer, sheet or dialog -- the run drawer too, which on the web closes by
 * its address -- but never the reader, whose left edge turns pages.
 */
export function backClosable(node = openDialogs[openDialogs.length - 1]) {
  return Boolean(node) && !node.classList.contains("reader") && dialogClosers.has(node);
}
export function closeFrontForBack() {
  const node = openDialogs[openDialogs.length - 1];
  if (!backClosable(node)) return false;
  dialogClosers.get(node)();
  return true;
}
