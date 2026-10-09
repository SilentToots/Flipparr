// Requests to the Flipparr server, shared by the web app and Flipparr View.
//
// The web app talks to its own origin with the session cookie (the default
// transport below). Flipparr View runs from capacitor://localhost, where no
// cookie can work, so at start it installs its own transport, which carries
// the profile's token in headers (src/reader-app/server.js). Shared screens
// call `apiRequest` either way and never know which.

/** The web app's transport: same origin, the session cookie, JSON back. */
async function sameOrigin(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error || `Request failed (${response.status})`);
    // Carry the status alongside the message. Callers need to tell "you are
    // signed out" apart from "the backend is down", and the message alone
    // cannot do that -- the server sends readable prose, not a status code.
    error.status = response.status;
    // And the whole answer: a refusal can name what already exists.
    error.payload = payload;
    // Why, when the server says: "profile_required" asks for the picker.
    error.reason = payload.reason || "";
    throw error;
  }
  return payload;
}

let transport = sameOrigin;

/**
 * Flipparr View's way to the server: `(path, options) => Promise<payload>`,
 * throwing an Error with `status`, `reason` and `payload` as the web's does.
 */
export function setApiTransport(next) {
  transport = next || sameOrigin;
  forgetAnswers();
}

// What each GET last answered, for a view coming back: a tab switch unmounts
// the view it leaves, and a view mounting with nothing showed its skeleton
// while it asked again for what it had a moment ago -- the Comics grid, the
// release shelves, the settings toggles. Seeded from here, it draws at once
// with the last answer and refreshes behind it.
const LAST_ANSWERS = new Map();
export function lastAnswer(path) { return LAST_ANSWERS.get(path) ?? null; }
export function rememberAnswer(path, payload) { LAST_ANSWERS.set(path, payload); }
/** Another profile, another library: nothing answered for the last one stays. */
export function forgetAnswers() { LAST_ANSWERS.clear(); }

export async function apiRequest(path, options) {
  const payload = await transport(path, options);
  if (!options?.method || options.method === "GET") LAST_ANSWERS.set(path, payload);
  return payload;
}
