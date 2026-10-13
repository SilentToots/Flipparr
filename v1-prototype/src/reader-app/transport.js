// Flipparr View's way to the server for every shared screen (api.js's
// transport), with reading offline built in (Phase 4).
//
// Online, a request goes to the server as before, and the answers the
// library draws from are kept on the device (offline-reading.js says which).
// When the server cannot be reached, the transport says so once (`online`
// turns false, and requests stop waiting on the network) and answers from
// the device instead: a downloaded comic's page list and panels, the places
// waiting to be sent, the library as it last was. Anything it cannot answer
// fails as unreachable, which every screen already handles.
//
// A saved place always goes into the profile's queue first, with when it was
// made; sent live, it carries no `at` (the server's time is the save's, so a
// device whose clock is slow cannot be skipped behind a web save), and the
// row is dropped once the server has it. What could not be sent is replayed
// oldest first, with its `at`, on reconnecting, launching and coming back to
// the foreground; the server keeps a replayed save only if nothing newer is
// stored, so a flight's reading never undoes a later one.
//
// Ratings, run and arc marks and reading-list entries work the same way
// (Phase 5, offline-changes.js): offline, or when the server stops
// answering, one waits on the device and is answered from it; every answer
// the screens draw from shows it until it is sent, after the places, with
// its `at`.
//
// Pure apart from what it is given -- the client (server.js), the device
// store and the profile's downloads -- so tests/reader-app-transport.test.mjs
// runs it in node.
import {
  keepsAnswer, mergeProgress, pageListFromRow, panelsFor, progressFromBody, queueDrop, queueSave, readerRoute,
  replayOrder,
} from "./offline-reading.js";
import {
  answerWhileWaiting, changeFor, dropChange, keptPathFor, queueChange, replayRequest, withChanges, withPlaces,
} from "./offline-changes.js";

/** How long a request waits before the server counts as unreachable. */
export const REQUEST_TIMEOUT_MS = 15000;
/** How often an offline app asks whether the server is back. */
export const PROBE_EVERY_MS = 30000;
/**
 * How long a page's panels are waited for. The server may ask the vision
 * model, which took 19 and 23 s on production (2026-10-12, the owner's
 * iPhone); at the usual 15 s the page fell back to whole and the app took
 * the server for gone.
 */
export const PANELS_TIMEOUT_MS = 60000;

function unreachable(cause, timedOut = false) {
  return Object.assign(new Error("Flipparr could not be reached"), { network: true, timedOut, cause });
}

export function createAppTransport({ client, store, onLost = () => {}, now = () => new Date().toISOString(),
  timeoutMs = REQUEST_TIMEOUT_MS, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let online = true;
  let profileId = null;
  let downloads = null;
  let replaying = null;
  let queueChain = Promise.resolve();
  let changeChain = Promise.resolve();
  const listeners = new Set();

  function setOnline(next) {
    if (online === next) return;
    online = next;
    listeners.forEach((listener) => listener(online));
    if (online) replay();
  }

  /** A request to the server, given up on after `timeoutMs` (or the request's own). */
  function send(path, { timeoutMs: wait = timeoutMs, ...options } = {}) {
    let timer;
    const started = Date.now();
    const late = new Promise((_, reject) => { timer = setTimer(() => reject(unreachable(new Error(`timed out after ${wait} ms`), true)), wait); });
    return Promise.race([client.api(path, options), late])
      .catch((error) => {
        // Why the app thinks it is offline, in the device's console: the
        // path only, never a token or a body.
        if (error.network) console.warn(`[Flipparr] unreachable: ${String(path).split("?")[0]} after ${Date.now() - started} ms (${error.cause?.message || error.message})`);
        throw error;
      })
      .finally(() => clearTimer(timer));
  }

  /** Whether the server answers its one public route, without saying so to anyone. */
  async function reachable() {
    try {
      await send("/api/v1/app", { headers: {} });
      return true;
    } catch {
      return false;
    }
  }

  /**
   * A request that failed for want of the server. One that only ran out of
   * time may have met a busy server, not a missing one: the app goes offline
   * only if the server then does not answer at all (the owner, 2026-10-12:
   * a slow panel reading hid Fix panels and closed the open editor).
   */
  async function lost(error) {
    if (error.timedOut && await reachable()) return;
    setOnline(false);
  }

  /** The profile's queue, changed one at a time. */
  function withQueue(change) {
    const run = queueChain.then(async () => {
      const queue = await store.progressQueue(profileId);
      const next = await change(queue);
      if (next !== queue) await store.saveProgressQueue(profileId, next);
      return next;
    });
    queueChain = run.catch(() => {});
    return run;
  }

  /** The profile's waiting changes, changed one at a time. */
  function withChangeQueue(change) {
    const run = changeChain.then(async () => {
      const queue = await store.changeQueue(profileId);
      const next = await change(queue);
      if (next !== queue) await store.saveChangeQueue(profileId, next);
      return next;
    });
    changeChain = run.catch(() => {});
    return run;
  }

  /** An answer with the profile's waiting changes, then its waiting places, laid over it. */
  async function shown(path, answer) {
    if (profileId === null || answer == null) return answer;
    const changed = store.changeQueue ? withChanges(path, answer, await store.changeQueue(profileId)) : answer;
    return withPlaces(path, changed, await store.progressQueue(profileId));
  }

  /** A change waiting on the device, and what it answers meanwhile. */
  async function wait(change) {
    const queue = await withChangeQueue((current) => queueChange(current, change, now()));
    const keptPath = keptPathFor(change);
    const kept = keptPath ? await store.answer(profileId, keptPath) : null;
    return answerWhileWaiting(change, kept, queue);
  }

  async function sendChange(path, options, change) {
    if (!online) return wait(change);
    try {
      const answer = await send(path, options);
      setOnline(true);
      // Said again since: what waited for this thing is older.
      await withChangeQueue((queue) => dropChange(queue, change.key));
      return answer;
    } catch (error) {
      if (error.network) {
        await lost(error);
        return wait(change);
      }
      if (error.status === 401) onLost();
      throw error;
    }
  }

  // A page's panels fixed by hand, into a downloaded comic's own panels too,
  // so it reads the fix offline from now on -- sent or still waiting. One
  // page at a time: each rewrites the comic's panels.json.
  let panelsChain = Promise.resolve();
  function keepPanels(change, answer) {
    const [, fileId, page] = change.key.split(":");
    if (!answer || !store.savePanels || !readyRow(fileId)) return;
    const forProfile = profileId;
    panelsChain = panelsChain.then(async () => {
      const saved = (await store.panels(forProfile, fileId)) || {};
      await store.savePanels(forProfile, fileId, { ...saved, [page]: answer });
    }).catch(() => {});
  }

  // A comic replaced on the server since (`stale`) is still read from the
  // copy kept, offline, until it is downloaded again.
  function readyRow(fileId) {
    const row = downloads?.row(fileId);
    return row && (row.state === "ready" || row.state === "stale") ? row : null;
  }

  async function waitingFor(fileId) {
    if (profileId === null) return null;
    return (await store.progressQueue(profileId)).find((row) => row.fileId === String(fileId)) || null;
  }

  /** What the device can answer for a GET, or undefined. */
  async function fromDevice(path, route) {
    if (route?.kind === "pages") {
      const row = readyRow(route.fileId);
      return row ? pageListFromRow(row) : undefined;
    }
    if (route?.kind === "panels") {
      const row = readyRow(route.fileId);
      return row ? panelsFor(await store.panels(profileId, route.fileId), route.fileId, route.page) : undefined;
    }
    if (route?.kind === "progress") {
      // A save still waiting, else the place the server last answered.
      const waiting = await waitingFor(route.fileId);
      const kept = profileId !== null ? await store.answer(profileId, path) : null;
      const row = readyRow(route.fileId);
      if (waiting) return progressFromBody(waiting.fileId, waiting.body, waiting.at, { pageCount: kept?.pageCount || row?.pageCount });
      return kept ?? undefined;
    }
    if (profileId !== null && keepsAnswer(path)) {
      const kept = await store.answer(profileId, path);
      return kept ?? undefined;
    }
    return undefined;
  }

  async function saveProgress(path, fileId, body) {
    const at = now();
    const row = readyRow(fileId);
    await withQueue((queue) => queueSave(queue, fileId, body, at));
    const finished = "read" in body ? Boolean(body.read)
      : Boolean(row?.pageCount && Number(body.page) >= row.pageCount - 1);
    downloads?.noteRead(fileId, { finished, at }).catch(() => {});
    if (!online) return progressFromBody(fileId, body, at, { pageCount: row?.pageCount });
    try {
      const answer = await send(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setOnline(true);
      await withQueue((queue) => queueDrop(queue, fileId, at));
      store.saveAnswer(profileId, path, answer).catch(() => {});
      return answer;
    } catch (error) {
      if (error.network) {
        await lost(error);
        return progressFromBody(fileId, body, at, { pageCount: row?.pageCount });
      }
      // Refused, not unreachable: it will not be accepted later either.
      await withQueue((queue) => queueDrop(queue, fileId, at));
      if (error.status === 401) onLost();
      if (error.status === 404) downloads?.remove(fileId, "hidden").catch(() => {});
      throw error;
    }
  }

  async function transport(path, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const route = readerRoute(path);
    if (method === "POST" && route?.kind === "progress" && profileId !== null) {
      let body = {};
      try { body = JSON.parse(options.body || "{}"); } catch { body = {}; }
      return saveProgress(path, route.fileId, body);
    }
    if (method !== "GET" && profileId !== null) {
      let body = {};
      try { body = JSON.parse(options.body || "{}"); } catch { body = {}; }
      const change = changeFor(path, method, body);
      if (change?.key.startsWith("panels:")) {
        const answer = await sendChange(path, options, change);
        keepPanels(change, answer);
        return answer;
      }
      if (change) return sendChange(path, options, change);
    }
    if (!online) {
      const answer = method === "GET" ? await fromDevice(path, route) : undefined;
      if (answer !== undefined) return shown(path, answer);
      throw unreachable();
    }
    try {
      const wait = method === "GET" && route?.kind === "panels" && options.timeoutMs === undefined ? { timeoutMs: PANELS_TIMEOUT_MS } : {};
      const answer = await send(path, { ...options, ...wait });
      setOnline(true);
      if (method === "GET" && profileId !== null) {
        if (keepsAnswer(path)) store.saveAnswer(profileId, path, answer).catch(() => {});
        if (route?.kind === "progress") return mergeProgress(await waitingFor(route.fileId), answer);
        return shown(path, answer);
      }
      return answer;
    } catch (error) {
      if (error.network) {
        await lost(error);
        const answer = method === "GET" ? await fromDevice(path, route) : undefined;
        if (answer !== undefined) return shown(path, answer);
      }
      if (error.status === 401) onLost();
      throw error;
    }
  }

  /** Send what is waiting, oldest first, with when each was made. */
  function replay() {
    if (replaying || !online || profileId === null) return replaying || Promise.resolve();
    const forProfile = profileId;
    replaying = (async () => {
      for (const row of replayOrder(await store.progressQueue(forProfile))) {
        if (profileId !== forProfile || !online) return;
        try {
          const path = `/api/v1/files/${row.fileId}/progress`;
          const answer = await send(path, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...row.body, at: row.at }),
          });
          await withQueue((queue) => queueDrop(queue, row.fileId, row.at));
          // What the server now holds -- a newer place from the web, if it
          // had one -- is where the comic opens next, online or not.
          if (answer && typeof answer === "object") store.saveAnswer(forProfile, path, answer).catch(() => {});
        } catch (error) {
          if (error.network) { await lost(error); return; }
          if (error.status === 401) { onLost(); return; }
          // Gone or refused: not sendable later either.
          await withQueue((queue) => queueDrop(queue, row.fileId, row.at));
          if (error.status === 404) downloads?.remove(row.fileId, "hidden").catch(() => {});
        }
      }
      // Then the ratings, marks and reading-list entries, oldest first.
      if (!store.changeQueue) return;
      const changes = [...await store.changeQueue(forProfile)].sort((a, b) => String(a.at).localeCompare(String(b.at)));
      for (const row of changes) {
        if (profileId !== forProfile || !online) return;
        try {
          const { path, options } = replayRequest(row);
          await send(path, options);
        } catch (error) {
          if (error.network) { await lost(error); return; }
          if (error.status === 401) { onLost(); return; }
          // Gone or refused: not sendable later either.
        }
        await withChangeQueue((queue) => dropChange(queue, row.key, row.at));
      }
    })().finally(() => { replaying = null; });
    return replaying;
  }

  /** Whether the server is back: the one public route, answered quickly. */
  async function probe() {
    const back = await reachable();
    setOnline(back);
    return back;
  }

  return {
    transport,
    replay,
    probe,
    get online() { return online; },
    setOffline(reason = "") {
      if (online) console.warn(`[Flipparr] offline: ${reason || "told so"}`);
      setOnline(false);
    },
    /**
     * The profile now reading, and its downloads: its queue, its kept
     * answers. Told the same profile again -- the server's answer arriving
     * after the device's kept session opened the app, or a reconnect -- it
     * keeps that profile's downloads; it used to drop them, and from then on
     * a read was never noted on a downloaded comic ("Unread", the owner,
     * 2026-10-11) and a downloaded comic was not served from the device if
     * the connection then went. Another profile, or none, starts without.
     */
    attach(nextProfileId, nextDownloads) {
      const next = nextProfileId === null ? null : String(nextProfileId);
      if (next !== profileId) downloads = nextDownloads ?? null;
      else if (nextDownloads !== undefined) downloads = nextDownloads;
      profileId = next;
    },
    useDownloads(nextDownloads) { downloads = nextDownloads; },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  };
}
