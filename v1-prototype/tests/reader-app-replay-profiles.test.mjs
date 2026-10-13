// Other profiles' offline places and changes are sent on reconnecting, each
// with its own session (src/reader-app/replay-profiles.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import { sendOtherProfilesWaiting } from "../src/reader-app/replay-profiles.js";

function device({ ended = [] } = {}) {
  const files = {
    "q2": [{ fileId: "63", body: { page: 4 }, at: "2026-10-12T10:00:00Z" }],
    "c3": [{ key: "rating:issue:5", path: "/api/v1/issues/5/rating", method: "POST", body: { rating: 4 }, at: "2026-10-12T10:01:00Z" }],
    "q4": [{ fileId: "70", body: { page: 1 }, at: "2026-10-12T10:02:00Z" }],
  };
  const store = {
    async profiles() { return [{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }, { id: 5 }]; },
    async progressQueue(id) { return files[`q${id}`] || []; },
    async saveProgressQueue(id, queue) { files[`q${id}`] = queue; },
    async changeQueue(id) { return files[`c${id}`] || []; },
    async saveChangeQueue(id, queue) { files[`c${id}`] = queue; },
    async answer() { return null; },
    async saveAnswer() {},
  };
  const keychain = { "device@http://s": "dev", "session@http://s#2": "two", "session@http://s#3": "three" };
  const secrets = { async get(key) { return keychain[key] || ""; }, async set(key, value) { keychain[key] = value; } };
  const sent = [];
  const fetchImpl = async (url, options) => {
    sent.push({ path: url.replace("http://s", ""), who: options.headers.Authorization, device: options.headers["Flipparr-Device"] });
    const who = options.headers.Authorization;
    const status = ended.includes(who) ? 401 : 200;
    return { ok: status === 200, status, headers: { get: (name) => (name === "Flipparr-Session" && who === "Bearer two" ? "two-renewed" : null) },
      async json() { return status === 200 ? {} : { error: "Sign in again" }; } };
  };
  return { store, secrets, keychain, files, sent, fetchImpl };
}

test("each other profile's waiting places and changes go with its own session; the active one's are left to it", async () => {
  const { store, secrets, keychain, files, sent, fetchImpl } = device();
  const count = await sendOtherProfilesWaiting({ origin: "http://s", currentProfileId: 1, store, secrets, fetchImpl });
  assert.equal(count, 2, "profiles 2 and 3; 4 never signed in here; 1 is the app's own");
  assert.deepEqual(sent.map(({ path, who }) => [path, who]), [
    ["/api/v1/files/63/progress", "Bearer two"],
    ["/api/v1/issues/5/rating", "Bearer three"],
  ]);
  assert.ok(sent.every((item) => item.device === "dev"), "the shared device's token with each");
  assert.deepEqual([files.q2, files.c3], [[], []], "nothing waits now");
  assert.equal(files.q4.length, 1, "a profile with no session kept here keeps its queue");
  assert.equal(keychain["session@http://s#2"], "two-renewed", "a renewed session is kept as that profile's");
  assert.equal(keychain["session@http://s"], undefined, "and never as the app's own");
});

test("a profile whose session has ended keeps what waits, for when it signs in again", async () => {
  const { store, secrets, files, fetchImpl } = device({ ended: ["Bearer three"] });
  await sendOtherProfilesWaiting({ origin: "http://s", currentProfileId: 1, store, secrets, fetchImpl });
  assert.equal(files.c3.length, 1);
  assert.deepEqual(files.q2, []);
});
