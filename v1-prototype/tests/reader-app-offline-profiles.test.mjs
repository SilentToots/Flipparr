import { test } from "node:test";
import assert from "node:assert/strict";
import { pbkdf2Sync } from "node:crypto";
import { hashPin, newPinSalt, verifyPin } from "../src/reader-app/offline-pin.js";
import { mergeProfiles, offlineChoice, withPinHash } from "../src/reader-app/offline-profiles.js";

test("a kept PIN is PBKDF2-SHA256, 200 000 rounds, 256 bits, with the install's salt", async () => {
  const salt = newPinSalt();
  assert.match(salt, /^[0-9a-f]{32}$/);
  const expected = pbkdf2Sync("4821", Buffer.from(salt, "hex"), 200000, 32, "sha256").toString("hex");
  assert.equal(await hashPin("4821", salt), expected);
  assert.equal(await verifyPin("4821", salt, expected), true);
  assert.equal(await verifyPin("4822", salt, expected), false);
  assert.equal(await verifyPin("4821", newPinSalt(), expected), false, "another install's salt");
  assert.equal(await verifyPin("4821", salt, undefined), false, "nothing kept, nothing opens");
});

test("a PIN's hash is kept only while the profile still has a PIN of that length", () => {
  const kept = withPinHash([{ id: 2, lock: "pin", pinLength: 4 }, { id: 3, lock: "pin", pinLength: 4 }, { id: 4, lock: "pin", pinLength: 4 }], 2, "h2");
  const withThree = withPinHash(withPinHash(kept, 3, "h3"), 4, "h4");
  const fresh = [{ id: 2, lock: "pin", pinLength: 4 }, { id: 3, lock: "none" }, { id: 4, lock: "pin", pinLength: 6 }, { id: 5, lock: "none" }];
  const merged = mergeProfiles(withThree, fresh);
  assert.deepEqual(merged.map((profile) => profile.pinHash), ["h2", undefined, undefined, undefined]);
  assert.equal(merged.length, 4, "the server's list is the list");
});

test("offline, only a profile used here before opens; a PIN is asked, a password waits for Flipparr", () => {
  assert.deepEqual(offlineChoice({ name: "Sam", lock: "none" }, { signedIn: true }), { open: true });
  assert.deepEqual(offlineChoice({ name: "Sam", lock: "pin", pinHash: "h" }, { signedIn: true }), { ask: "pin" });
  assert.match(offlineChoice({ name: "Sam", lock: "pin" }, { signedIn: true }).refused, /PIN hasn’t been used on this iPad/);
  assert.match(offlineChoice({ name: "Robin", lock: "password" }, { signedIn: true }).refused, /password needs Flipparr/);
  assert.match(offlineChoice({ name: "Sam", lock: "none" }, { signedIn: false }).refused, /hasn’t been used on this iPad/);
});
