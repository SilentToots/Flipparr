import { test } from "node:test";
import assert from "node:assert/strict";
import { createImageSource, imageName } from "../src/reader-app/images.js";

const client = { origin: "https://f.example", headers: () => ({ "Flipparr-Client": "app", Authorization: "Bearer t" }) };

function fakeNative() {
  const calls = [];
  return {
    calls,
    async fetchToFile(options) { calls.push(["fetch", options]); return { path: `/lib/${options.path}` }; },
    async trimFolder(options) { calls.push(["trim", options]); return { bytes: 0, removed: 0 }; },
  };
}

test("a page is fetched once, to the profile's own folder, with the token", async () => {
  const native = fakeNative();
  const source = createImageSource(client, 7, { native, toSrc: (path) => `capacitor://localhost/_capacitor_file_${path}` });
  const url = "/api/v1/files/63/pages/0?v=abc&size=read";
  const [first, again] = await Promise.all([source(url), source(url)]);
  assert.equal(first, again);
  assert.equal(native.calls.length, 1, "the same page is asked for once");
  const [, options] = native.calls[0];
  assert.equal(options.url, "https://f.example/api/v1/files/63/pages/0?v=abc&size=read");
  assert.match(options.path, /^online\/u7\/[0-9a-f]{16}\.jpg$/);
  assert.equal(options.headers.Authorization, "Bearer t");
  assert.ok(first.startsWith("capacitor://localhost/_capacitor_file_/lib/online/u7/"));
});

test("a provider's cover never gets the token; a failure is asked again", async () => {
  const native = fakeNative();
  const source = createImageSource(client, 1, { native, toSrc: (path) => path });
  await source("https://files1.comics.org/img/cover.jpg");
  assert.deepEqual(native.calls[0][1].headers, {}, "no token for someone else's server");
  let fail = true;
  native.fetchToFile = async (options) => { native.calls.push(["fetch", options]); if (fail) throw new Error("x"); return { path: "p" }; };
  await assert.rejects(source("/api/v1/files/1/pages/0"));
  fail = false;
  assert.equal(await source("/api/v1/files/1/pages/0"), "p");
});

test("names are stable and differ between pages", () => {
  assert.equal(imageName("a"), imageName("a"));
  assert.notEqual(imageName("/api/v1/files/1/pages/0"), imageName("/api/v1/files/1/pages/1"));
});

test("the folder is trimmed now and then, not on every page", async () => {
  const native = fakeNative();
  const source = createImageSource(client, 1, { native, toSrc: (path) => path });
  for (let index = 0; index < 40; index += 1) await source(`/api/v1/files/1/pages/${index}`);
  const trims = native.calls.filter(([kind]) => kind === "trim");
  assert.equal(trims.length, 1);
  assert.equal(trims[0][1].path, "online/u1");
});
