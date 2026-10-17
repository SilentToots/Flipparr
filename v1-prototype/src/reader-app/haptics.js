// A light haptic tap from the app's native side (FlipparrNative.tap): pull
// to refresh's, as the pull passes the point of refreshing, as iOS's own
// refresh control does. Out of the app there is no native side; nothing.
import { registerPlugin } from "@capacitor/core";

const Native = registerPlugin("FlipparrNative");

export function tapHaptic(native = Native) {
  Promise.resolve().then(() => native.tap()).catch(() => {});
}
