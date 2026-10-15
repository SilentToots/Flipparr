import { test } from "node:test";
import assert from "node:assert/strict";
import { serverOrigin, createClient, checkServer, findServer, READER_API_VERSION } from "../src/reader-app/server.js";

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
  await assert.rejects(checkServer("https://f.example", async () => answer(200, { name: "Flipparr", apiVersion: READER_API_VERSION + 1 })), /Update Flipparr View/);
  await assert.rejects(checkServer("https://f.example", async () => { throw new TypeError("x"); }), /Nothing answered/);
});

test("an address typed without a scheme tries https, then http only when https does not answer", async () => {
  const flipparr = { ok: true, json: async () => ({ name: "Flipparr", apiVersion: 1 }) };
  const asked = [];
  const homeOnly = async (url) => {
    asked.push(url);
    if (url.startsWith("https:")) throw new TypeError("TLS handshake failed");
    return flipparr;
  };
  assert.equal((await findServer("192.168.1.20:8787", homeOnly)).origin, "http://192.168.1.20:8787");
  assert.deepEqual(asked, ["https://192.168.1.20:8787/api/v1/app", "http://192.168.1.20:8787/api/v1/app"]);

  const both = async () => flipparr;
  assert.equal((await findServer("flipparr.example.com", both)).origin, "https://flipparr.example.com", "https first");

  const notFlipparr = async (url) => (url.startsWith("https:") ? { ok: true, json: async () => ({}) } : flipparr);
  await assert.rejects(findServer("example.com", notFlipparr), /not a Flipparr/, "an https answer is never dropped to http");

  await assert.rejects(findServer("https://nas.local:8787", homeOnly), (error) => error.network, "a typed scheme is used as is");
  const nothing = async () => { throw new TypeError("offline"); };
  await assert.rejects(findServer("nas.local", nothing), /Nothing answered/);
});

test("an https try that hangs counts as nothing answering, and http is tried", async () => {
  const flipparr = { ok: true, json: async () => ({ name: "Flipparr", apiVersion: 1 }) };
  const hangsOnHttps = (url) => (url.startsWith("https:") ? new Promise(() => {}) : Promise.resolve(flipparr));
  const found = await findServer("localhost:8804", hangsOnHttps, { httpsWait: 10 });
  assert.equal(found.origin, "http://localhost:8804");
});

test("a fall to http says so, so the screen can", async () => {
  const flipparr = { ok: true, json: async () => ({ name: "Flipparr", apiVersion: 1 }) };
  const homeOnly = async (url) => {
    if (url.startsWith("https:")) throw new TypeError("refused");
    return flipparr;
  };
  const found = await findServer("192.168.1.20:8787", homeOnly);
  assert.equal(found.origin, "http://192.168.1.20:8787");
  assert.equal(found.unencrypted, true);
  const secure = async () => flipparr;
  assert.equal((await findServer("nas.example", secure)).unencrypted, undefined, "https answered: nothing to say");
});
