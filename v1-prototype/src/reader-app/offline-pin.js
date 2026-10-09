// A profile's PIN checked on the device, so a shared iPad can switch to a
// PIN-locked profile with no connection (Phase 5). After a PIN switch the
// server accepted, the app keeps a hash of that PIN for the profile;
// offline, a PIN typed on the picker is hashed the same way and compared.
//
// PBKDF2-SHA256, 200 000 iterations, 256 bits, through WebCrypto (W3C Web
// Cryptography API, `SubtleCrypto.deriveBits`), salted per install
// (`pinSalt`, a random 16 bytes kept in the Keychain). The iteration count
// follows the OWASP Password Storage Cheat Sheet's floor for PBKDF2-SHA256.
//
// Said plainly: this is weaker than the server's check. It is local and
// unthrottled, so anyone holding the unlocked iPad and its files could try
// every 4-6 digit PIN; the server's scrypt check (app.py
// `check_profile_switch`) is throttled. Accepted for a household's shared
// iPad, where the PIN keeps a child out of a parent's profile rather than
// guarding against a determined attacker. A password-locked profile is
// never unlocked offline, and the hash goes when the profile's lock stops
// being a PIN (offline-profiles.js).
const ITERATIONS = 200000;
const BITS = 256;

const hex = (bytes) => Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
const unhex = (text) => new Uint8Array(String(text).match(/../g)?.map((pair) => parseInt(pair, 16)) || []);

/** A new install's salt: 16 random bytes, as hex. */
export function newPinSalt(cryptoImpl = globalThis.crypto) {
  return hex(cryptoImpl.getRandomValues(new Uint8Array(16)));
}

/** The hash kept for a PIN: PBKDF2-SHA256 with the install's salt, as hex. */
export async function hashPin(pin, salt, cryptoImpl = globalThis.crypto) {
  const key = await cryptoImpl.subtle.importKey("raw", new TextEncoder().encode(String(pin)), "PBKDF2", false, ["deriveBits"]);
  const bits = await cryptoImpl.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt: unhex(salt), iterations: ITERATIONS }, key, BITS);
  return hex(bits);
}

/** Whether a PIN is the one whose hash was kept; compared without stopping early. */
export async function verifyPin(pin, salt, kept, cryptoImpl = globalThis.crypto) {
  if (!kept || !salt) return false;
  const typed = await hashPin(pin, salt, cryptoImpl);
  if (typed.length !== kept.length) return false;
  let difference = 0;
  for (let index = 0; index < typed.length; index += 1) difference |= typed.charCodeAt(index) ^ kept.charCodeAt(index);
  return difference === 0;
}
