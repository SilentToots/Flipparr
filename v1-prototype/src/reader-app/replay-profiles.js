// What other profiles on this device did offline, sent when the server is
// back -- not only when that profile is next opened (the backlog, 2026-10-12:
// a child's places and marks waited on the iPad until they switched to their
// profile again). Each profile's queue is sent with its own session, kept in
// the Keychain when it signed in here (offline-profiles.js), through a
// transport of its own: the same replay, with the same `at` rules, the
// active profile's uses (transport.js). A profile whose session has ended
// keeps its queue for when it signs in again.
import { createClient } from "./server.js";
import { createAppTransport } from "./transport.js";
import { deviceStore, deviceId } from "./device-store.js";
import { secureStore, tokenKeys } from "./secure-store.js";

/** Send every other profile's waiting places, changes and removals. Resolves to how many profiles were sent. */
export async function sendOtherProfilesWaiting({
  origin, currentProfileId, store = deviceStore, secrets = secureStore, fetchImpl = undefined, idOfDevice = deviceId,
}) {
  const keys = tokenKeys(origin);
  const device = await secrets.get(keys.device);
  let sent = 0;
  for (const profile of await store.profiles()) {
    const id = String(profile.id);
    if (id === String(currentProfileId)) continue;
    const places = await store.progressQueue(id);
    const changes = store.changeQueue ? await store.changeQueue(id) : [];
    const removals = store.pendingRemovals ? await store.pendingRemovals(id) : [];
    if (!places.length && !changes.length && !removals.length) continue;
    const session = await secrets.get(keys.profileSession(id));
    if (!session) continue;
    const tokens = {
      session, device,
      // A renewed session is that profile's, never the one the app carries.
      async save(next) { if (next.session) await secrets.set(keys.profileSession(id), next.session); },
    };
    const client = createClient({ origin, tokens, ...(fetchImpl ? { fetchImpl } : {}) });
    const transport = createAppTransport({ client, store });
    transport.attach(id, null);
    await transport.replay();
    if (!transport.online) break; // the server went again: the rest wait
    // A comic of theirs that another profile's download removed to make
    // room (downloads.js evict): the server's record of this device hears
    // of it as them, now, not when they next open the app.
    if (removals.length && !(await flushRemovals(client, store, id, await idOfDevice(), removals))) break;
    sent += 1;
  }
  return sent;
}

/** The profile's waiting removals, told as them. Resolves false when the server went. */
async function flushRemovals(client, store, profileId, device, removals) {
  for (const removal of removals) {
    try {
      await client.api("/api/v1/offline/downloads/remove", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device, ...removal }),
      });
    } catch (error) {
      if (error.network || error.status === 401 || error.status >= 500) return false;
      // Refused: it will not be accepted later either.
    }
    const waiting = await store.pendingRemovals(profileId);
    await store.savePendingRemovals(profileId, waiting.filter((item) => item.fileId !== removal.fileId));
  }
  return true;
}
