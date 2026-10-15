// Flipparr View's way to the household's server.
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

/**
 * The Flipparr at an address as typed: `{origin, answer}`. Typed without a
 * scheme, https is tried first and http only when nothing answers over
 * https -- a server on the home network usually has no certificate (the
 * owner, 2026-10-09, had to type http:// for the NAS). A server that
 * answers over https is never dropped to http. A scheme typed is used as is.
 */
export const HTTPS_TRY_MS = 4000;

export async function findServer(input, fetchImpl = globalThis.fetch, { httpsWait = HTTPS_TRY_MS } = {}) {
  const typed = /^[a-z][a-z0-9+.-]*:\/\//i.test(String(input || "").trim());
  const origin = serverOrigin(input);
  try {
    // A server that speaks only http does not always refuse https: one
    // waits on the handshake (Waitress does), so the try has a limit and
    // counts as nothing answering when it runs out (simulator, 2026-10-10).
    const tried = typed ? checkServer(origin, fetchImpl) : Promise.race([
      checkServer(origin, fetchImpl),
      new Promise((_, reject) => setTimeout(() => reject(requestError("Nothing answered at that address", { network: true })), httpsWait)),
    ]);
    return { origin, answer: await tried };
  } catch (error) {
    if (typed || !error.network) throw error;
    const plain = origin.replace(/^https:/, "http:");
    try {
      // Said back to the caller: the address is kept with http, and the
      // password and token will cross the home network in clear from now
      // on. The connect screen shows it (ReaderApp), rather than deciding
      // in silence -- a slow handshake on a busy NAS was enough before.
      return { origin: plain, answer: await checkServer(plain, fetchImpl), unencrypted: true, httpsTimedOut: Boolean(error.timedOutHttps) };
    } catch (second) {
      throw second.network ? error : second;
    }
  }
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
  /** What every request to this server carries; never sent anywhere else. */
  function headers() {
    const sent = { "Flipparr-Client": "app" };
    if (tokens.session) sent.Authorization = `Bearer ${tokens.session}`;
    if (tokens.device) sent["Flipparr-Device"] = tokens.device;
    return sent;
  }
  async function api(path, options = {}) {
    const sent = { ...(options.headers || {}), ...headers() };
    let response;
    try {
      response = await fetchImpl(origin + path, { ...options, headers: sent });
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
  /**
   * Carry another session from now on: a profile chosen offline, whose own
   * token the app kept (offline-profiles.js). The server checks it when it
   * is back.
   */
  async function useSession(session) {
    tokens.session = session || "";
    await tokens.save({ session: tokens.session, device: tokens.device });
  }
  return { origin, api, headers, useSession };
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
    throw requestError("That address is not a Flipparr server, or it is older than Flipparr View needs");
  }
  if (Number(answer.apiVersion) > READER_API_VERSION) {
    throw requestError("This server is newer than this app. Update Flipparr View to read from it.");
  }
  return answer;
}
