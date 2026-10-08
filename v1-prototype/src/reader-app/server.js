// Flipparr Reader's way to the household's server.
//
// The app runs from its own origin (capacitor://localhost), where the web
// app's cookie session cannot work, so it carries the server's signed tokens
// in headers instead (docs/OFFLINE_READING_PLAN.md, "Authentication for the
// app"): every request says `Flipparr-Client: app`, the session goes as
// `Authorization: Bearer`, a shared device's token as `Flipparr-Device`, and
// a token the server issues or ends on any request comes back in the
// `Flipparr-Session` / `Flipparr-Device` response headers (an empty value
// means it ended). The requests themselves go through the native layer
// (CapacitorHttp patches fetch), so CORS never applies.
//
// Pure apart from the `fetch` and token store it is given, so the rules are
// tested in node (tests/reader-app-server.test.mjs).

/** The reader API this build understands (`READER_API_VERSION` in app.py). */
export const READER_API_VERSION = 1;

/**
 * The address a person typed, as the origin to talk to: a bare name gets
 * https, a trailing slash or path is dropped. Throws a readable error for
 * anything that is not an http(s) address.
 */
export function serverOrigin(input) {
  let text = String(input || "").trim();
  if (!text) throw new Error("Enter your Flipparr server's address");
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) text = `https://${text}`;
  let url;
  try {
    url = new URL(text);
  } catch {
    throw new Error("That is not a web address");
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") throw new Error("Use an https:// or http:// address");
  return url.origin;
}

/** A request error that says why, the way the web app's apiRequest does. */
function requestError(message, extra = {}) {
  return Object.assign(new Error(message), extra);
}

/**
 * A client for one server. `tokens` holds `{session, device}` and is told of
 * every change through `tokens.save(next)` (the app keeps them in the
 * Keychain); `fetchImpl` is the platform's fetch.
 */
export function createClient({ origin, tokens, fetchImpl = globalThis.fetch }) {
  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}), "Flipparr-Client": "app" };
    if (tokens.session) headers.Authorization = `Bearer ${tokens.session}`;
    if (tokens.device) headers["Flipparr-Device"] = tokens.device;
    let response;
    try {
      response = await fetchImpl(origin + path, { ...options, headers });
    } catch (error) {
      throw requestError("Flipparr could not be reached", { network: true, cause: error });
    }
    const session = response.headers.get("Flipparr-Session");
    const device = response.headers.get("Flipparr-Device");
    if (session !== null || device !== null) {
      const next = {
        session: session !== null ? session : tokens.session,
        device: device !== null ? device : tokens.device,
      };
      tokens.session = next.session || "";
      tokens.device = next.device || "";
      await tokens.save({ session: tokens.session, device: tokens.device });
    }
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok) {
      throw requestError(payload.error || `Request failed (${response.status})`, {
        status: response.status, reason: payload.reason || "", payload,
      });
    }
    return payload;
  }
  return { origin, api };
}

/**
 * Whether an address is a Flipparr this build can read from: `/api/v1/app`
 * answers with its name and reader API version. Resolves to that answer.
 */
export async function checkServer(origin, fetchImpl = globalThis.fetch) {
  let response;
  try {
    response = await fetchImpl(`${origin}/api/v1/app`, { headers: { "Flipparr-Client": "app" } });
  } catch (error) {
    throw requestError("Nothing answered at that address", { network: true, cause: error });
  }
  let answer = null;
  try {
    answer = await response.json();
  } catch {
    answer = null;
  }
  if (!response.ok || !answer || answer.name !== "Flipparr") {
    throw requestError("That address is not a Flipparr server, or it is older than Flipparr Reader needs");
  }
  if (Number(answer.apiVersion) > READER_API_VERSION) {
    throw requestError("This server is newer than this app. Update Flipparr Reader to read from it.");
  }
  return answer;
}
