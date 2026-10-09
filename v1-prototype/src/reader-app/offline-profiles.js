// "Who's reading?" with no connection (Phase 5). The picker's profiles are
// kept on the device (profiles.json) whenever the server lists them; each
// profile signed in on this iPad keeps its own session token in the Keychain
// and the last word on who it is (u<id>/status.json), so switching to it
// offline only changes which token the app carries. The server checks that
// token when it is back: a profile whose session has ended since asks again
// there. Plain rules; tests/reader-app-offline-profiles.test.mjs.

/**
 * The profiles to keep, from the server's list: a PIN's hash is kept only
 * while the profile is still locked with a PIN of the same length.
 */
export function mergeProfiles(kept = [], fresh = []) {
  const before = new Map(kept.map((profile) => [String(profile.id), profile]));
  return fresh.map((profile) => {
    const old = before.get(String(profile.id));
    const stillPin = old?.pinHash && profile.lock === "pin" && Number(profile.pinLength) === Number(old.pinLength);
    return stillPin ? { ...profile, pinHash: old.pinHash } : { ...profile };
  });
}

/** The kept profiles with a PIN's hash for one of them. */
export function withPinHash(profiles = [], profileId, pinHash) {
  return profiles.map((profile) => (String(profile.id) === String(profileId) ? { ...profile, pinHash } : profile));
}

/**
 * What choosing a profile offline can do: "open" it, ask its "pin", or
 * refuse, saying why. `signedIn` is whether this iPad keeps that profile's
 * session.
 */
export function offlineChoice(profile, { signedIn }) {
  const name = profile?.name || "That profile";
  if (!signedIn) return { refused: `${name} hasn’t been used on this iPad yet. Connect to Flipparr to switch to it.` };
  if (profile.lock === "password") return { refused: `${name}’s password needs Flipparr. Connect to switch to it.` };
  if (profile.lock === "pin") {
    return profile.pinHash ? { ask: "pin" }
      : { refused: `${name}’s PIN hasn’t been used on this iPad yet. Connect to Flipparr to switch to it.` };
  }
  return { open: true };
}
