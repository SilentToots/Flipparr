// Small secrets the app keeps between launches -- the server's address, its
// session and shared-device tokens -- in the iOS Keychain, through the app's
// own native plugin (FlipparrNative, ios/App/App/FlipparrNative.swift). Never
// in localStorage, a file or a log: a WKWebView's web storage under a custom
// scheme has no documented eviction rules, and a token is a sign-in.
import { registerPlugin } from "@capacitor/core";

const Native = registerPlugin("FlipparrNative");

export const secureStore = {
  async get(key) {
    const { value } = await Native.keychainGet({ key });
    return value || "";
  },
  set(key, value) {
    return Native.keychainSet({ key, value: String(value) });
  },
  remove(key) {
    return Native.keychainRemove({ key });
  },
};

/** The Keychain names for one server's tokens. */
export const tokenKeys = (origin) => ({ session: `session@${origin}`, device: `device@${origin}` });
