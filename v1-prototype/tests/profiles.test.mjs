import test from "node:test";
import assert from "node:assert/strict";
import {
  setStorageProfile, profileKey, profileStorage, migrateLegacyKeys, can, isAdmin,
  initials, pinInput, profileColour, VIEWER_CACHE_KEY,
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
  assert.equal(can(reader, "nav.discover"), false);
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
