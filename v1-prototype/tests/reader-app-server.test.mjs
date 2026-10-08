import { test } from "node:test";
import assert from "node:assert/strict";
import { serverOrigin, createClient, checkServer, READER_API_VERSION } from "../src/reader-app/server.js";

function answer(status, body, headers = {}) {
  const lower = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  return {
    ok: status >= 200 && status < 300, status,
    headers: { get: (name) => (name.toLowerCase() in lower ? lower[name.toLowerCase()] : null) },
    json: async () => body,
  };
}

function store(initial = {}) {
  const saved = [];
  return { session: "", device: "", ...initial, saved, async save(next) { saved.push(next); } };
}

test("an address becomes the origin to talk to", () => {
  assert.equal(serverOrigin("flipparr.example.com"), "https://flipparr.example.com");
  assert.equal(serverOrigin(" https://flipparr.example.com/library/ "), "https://flipparr.example.com");
  assert.equal(serverOrigin("http://nas.local:8787"), "http://nas.local:8787");
  assert.throws(() => serverOrigin(""), /address/);
  assert.throws(() => serverOrigin("ftp://nas.local"), /https/);
});

test("every request says it is the app and carries its tokens in headers", async () => {
  const seen = [];
  const tokens = store({ session: "v3.s", device: "d1" });
  const client = createClient({ origin: "https://f.example", tokens, fetchImpl: async (url, options) => {
    seen.push({ url, headers: options.headers });
    return answer(200, { ok: true });
  } });
  assert.deepEqual(await client.api("/api/v1/me"), { ok: true });
  assert.equal(seen[0].url, "https://f.example/api/v1/me");
  assert.deepEqual(seen[0].headers, { "Flipparr-Client": "app", Authorization: "Bearer v3.s", "Flipparr-Device": "d1" });
  assert.deepEqual(tokens.saved, [], "nothing issued, nothing saved");
});

test("a token issued or ended on any answer is kept, the other left as it was", async () => {
  const tokens = store({ session: "old", device: "dev" });
  let next = answer(200, {}, { "Flipparr-Session": "new" });
  const client = createClient({ origin: "https://f.example", tokens, fetchImpl: async () => next });
  await client.api("/api/v1/me");
  assert.deepEqual([tokens.session, tokens.device], ["new", "dev"]);
  next = answer(200, {}, { "Flipparr-Session": "" });
  await client.api("/api/v1/auth/logout");
  assert.deepEqual([tokens.session, tokens.device], ["", "dev"], "an empty header ends the session");
  assert.deepEqual(tokens.saved, [{ session: "new", device: "dev" }, { session: "", device: "dev" }]);
});

test("a refusal carries its status and reason; an unreachable server says so", async () => {
  const client = createClient({ origin: "https://f.example", tokens: store(), fetchImpl: async () =>
    answer(401, { error: "Authentication required", reason: "signin_required" }) });
  await assert.rejects(client.api("/api/v1/me"), (error) => error.status === 401 && error.reason === "signin_required");
  const offline = createClient({ origin: "https://f.example", tokens: store(), fetchImpl: async () => { throw new TypeError("Load failed"); } });
  await assert.rejects(offline.api("/api/v1/me"), (error) => error.network === true && /reached/.test(error.message));
});

test("only a Flipparr this build can read from is accepted", async () => {
  const ok = await checkServer("https://f.example", async () => answer(200, { name: "Flipparr", apiVersion: READER_API_VERSION, authMethod: "forms" }));
  assert.equal(ok.authMethod, "forms");
  await assert.rejects(checkServer("https://f.example", async () => answer(200, { name: "Something else" })), /not a Flipparr/);
  await assert.rejects(checkServer("https://f.example", async () => answer(404, {})), /not a Flipparr/);
  await assert.rejects(checkServer("https://f.example", async () => answer(200, { name: "Flipparr", apiVersion: READER_API_VERSION + 1 })), /Update Flipparr Reader/);
  await assert.rejects(checkServer("https://f.example", async () => { throw new TypeError("x"); }), /Nothing answered/);
});
