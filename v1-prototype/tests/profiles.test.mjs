import test from "node:test";
import assert from "node:assert/strict";
import {
  setStorageProfile, profileKey, profileStorage, migrateLegacyKeys, can, isAdmin,
  initials, pinInput, profileColour, VIEWER_CACHE_KEY, isLocked, lockChoices, lockPatch, LOCK_LABELS, nextProfileColour,
} from "../src/profiles.js";

function memoryStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
    removeItem: (key) => map.delete(key),
    dump: () => Object.fromEntries(map),
  };
}

test("each profile keeps its own browser storage", () => {
  const storage = memoryStorage();
  setStorageProfile(2);
  profileStorage(storage).setItem("flipparr.library", "{\"sort\":\"recent\"}");
  assert.deepEqual(storage.dump(), { "flipparr.u2.library": "{\"sort\":\"recent\"}" });
  setStorageProfile(3);
  assert.equal(profileStorage(storage).getItem("flipparr.library"), null, "not Sam's");
  setStorageProfile(null);
  assert.equal(profileKey("flipparr.library"), "flipparr.library", "no profile, the plain key");
  assert.equal(profileKey("flipparr.library", 5), "flipparr.u5.library");
  assert.equal(profileKey(VIEWER_CACHE_KEY, 5), VIEWER_CACHE_KEY, "who last read here is the device's, not a profile's");
  assert.equal(profileKey("other", 5), "other");
});

test("what the browser kept before profiles moves to the admin, once, and to nobody else", () => {
  const storage = memoryStorage({ "flipparr.library": "L", "flipparr.reader": "R", "flipparr.u1.reader": "kept" });
  assert.equal(migrateLegacyKeys(storage, 2), 0, "a reader inherits nothing");
  assert.equal(storage.getItem("flipparr.library"), "L");
  assert.equal(migrateLegacyKeys(storage, 1), 1);
  assert.deepEqual(storage.dump(), { "flipparr.u1.library": "L", "flipparr.u1.reader": "kept" },
    "moved where missing, never over what the admin already has, and the old keys gone");
  assert.equal(migrateLegacyKeys(storage, 1), 0, "once is once");
});

test("a reader is shown only what a reader can do", () => {
  const reader = { id: 2, role: "reader" };
  const admin = { id: 1, role: "admin" };
  assert.equal(can(reader, "nav.library"), true);
  assert.equal(can(reader, "settings.profile"), true);
  assert.equal(can(reader, "nav.discover"), true, "to find something to ask for");
  assert.equal(can(reader, "nav.requests"), true, "their requests");
  assert.equal(can(reader, "settings.profiles"), false);
  assert.equal(can(reader, "drawer.edit"), false);
  assert.equal(can(admin, "drawer.edit"), true);
  assert.equal(can(null, "drawer.edit"), true, "before anyone is known, nothing is hidden; the server decides");
  assert.equal(isAdmin(reader), false);
  assert.equal(isAdmin(admin), true);
});

test("a profile is drawn with its initials and one of the palette's colours", () => {
  assert.equal(initials("Sam"), "S");
  assert.equal(initials("  little   reader "), "LR");
  assert.equal(initials("Ángel Ruiz Soto"), "ÁR");
  assert.equal(initials(""), "?");
  assert.equal(profileColour({ colour: "teal" }), "teal");
  assert.equal(profileColour({ colour: "chartreuse" }), "violet");
});

test("the PIN pad takes digits, up to six, and backspace", () => {
  let pin = "";
  for (const key of ["1", "a", "2", "3", "4", "5", "6", "7"]) pin = pinInput(pin, key);
  assert.equal(pin, "123456");
  assert.equal(pinInput(pin, "Backspace"), "12345");
  assert.equal(pinInput("", "Backspace"), "");
});

test("a profile opens with a tap, a PIN or a password, and the admin never with a tap", () => {
  assert.equal(isLocked({ lock: "open" }), false);
  assert.equal(isLocked({ lock: "pin" }), true);
  assert.equal(isLocked({ lock: "password" }), true);
  assert.equal(isLocked(null), false);
  assert.equal(LOCK_LABELS.open, undefined, "an open profile says nothing about it");
  const disabled = (profile, options) => lockChoices(profile, options).filter((c) => c.disabled).map((c) => c.id);
  assert.deepEqual(disabled({ id: 2, role: "reader" }), []);
  assert.deepEqual(disabled({ id: 1, role: "admin" }), ["open", "password"], "no password to ask for until Security has one");
  assert.deepEqual(disabled({ id: 1, role: "admin" }, { adminPassword: true }), ["open"]);
});

test("choosing a lock sends what it needs, and nothing it cannot use", () => {
  const sam = { id: 2, role: "reader", hasPin: false, hasPassword: false };
  assert.equal(lockPatch(sam, "pin"), null, "a PIN lock needs a PIN");
  assert.equal(lockPatch(sam, "pin", { pin: "12" }), null, "four to six digits");
  assert.deepEqual(lockPatch(sam, "pin", { pin: "1357" }), { switchLock: "pin", pin: "1357" });
  assert.deepEqual(lockPatch({ ...sam, hasPin: true }, "pin"), { switchLock: "pin" }, "keeps the PIN it has");
  assert.deepEqual(lockPatch({ ...sam, hasPin: true }, "open"), { switchLock: "open", pin: null }, "an unused PIN goes");
  assert.equal(lockPatch(sam, "password", { password: "short" }), null);
  assert.deepEqual(lockPatch(sam, "password", { password: "long enough" }), { switchLock: "password", password: "long enough" });
  assert.deepEqual(lockPatch({ ...sam, hasPassword: true }, "password"), { switchLock: "password" }, "keeps the password it has");
  assert.deepEqual(lockPatch({ id: 1, role: "admin", hasPin: true }, "password", { password: "ignored!!" }),
    { switchLock: "password", pin: null }, "the admin's password is Security's");
});

test("a new profile gets the first colour nobody has, as the server gives it", () => {
  assert.equal(nextProfileColour([{ id: 1, colour: null }]), "blue", "the admin with none is violet");
  assert.equal(nextProfileColour([{ colour: null }, { colour: "blue" }, { colour: "pink" }]), "teal");
  assert.equal(nextProfileColour([]), "violet");
  const all = ["violet", "blue", "teal", "green", "amber", "orange", "red", "pink"].map((colour) => ({ colour }));
  assert.equal(nextProfileColour(all), "violet", "past eight, the palette again");
});
