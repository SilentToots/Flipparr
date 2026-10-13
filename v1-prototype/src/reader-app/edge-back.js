// Back in Flipparr View: iOS's swipe in from the left edge closes the drawer
// or sheet in front, as Back does on the web (where it goes through the
// address; the app has none). The native side recognises the swipe only
// while this says a closable layer is in front, so the reader's left edge
// -- its previous-page zone -- and the library are left alone.
import { registerPlugin } from "@capacitor/core";
import { backClosable, closeFrontForBack, onFrontDialogChange } from "../dialog-stack.js";

const Native = registerPlugin("FlipparrNative");

/** Start listening; returns the way to stop. */
export function startEdgeBack(native = Native) {
  let enabled = null;
  const tell = (front) => {
    const next = backClosable(front || undefined);
    if (next === enabled) return;
    enabled = next;
    Promise.resolve().then(() => native.setEdgeBack({ enabled: next })).catch(() => {});
  };
  const stopFront = onFrontDialogChange(tell);
  tell(null);
  let listener = null;
  Promise.resolve()
    .then(() => native.addListener("edgeBack", () => { closeFrontForBack(); }))
    .then((handle) => { listener = handle; }, () => {});
  return () => {
    stopFront();
    listener?.remove();
  };
}
