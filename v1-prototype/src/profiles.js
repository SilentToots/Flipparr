// Reader profiles on the client: whose browser storage this is, what a
// profile may see, and how a profile is drawn. Pure apart from the storage it
// is handed, and tested on its own.
//
// Everything the app keeps in the browser -- the library's sort and view,
// recent searches, the reader's settings, which notifications were dismissed
// -- belongs to the profile using it, so two people sharing a tablet do not
// share a Recent sort. Keys are namespaced by profile (`flipparr.u2.library`)
// through `profileStorage`, which the storage helpers use by default. The
// namespace is set once per page load, from who the server says is reading;
// switching profile reloads the page.

let scope = null;

export const VIEWER_CACHE_KEY = "flipparr.viewer";

/** The profile whose storage `profileStorage` reads and writes, or null for the unscoped keys. */
export function setStorageProfile(id) {
  const number = Number(id);
  scope = Number.isInteger(number) && number > 0 ? number : null;
}

export function storageProfile() {
  return scope;
}

/** A `flipparr.*` key in a profile's own namespace. Other keys pass through. */
export function profileKey(key, id = scope) {
  if (!id || typeof key !== "string" || !key.startsWith("flipparr.") || key === VIEWER_CACHE_KEY) return key;
  return `flipparr.u${id}.${key.slice("flipparr.".length)}`;
}

function base(storage) {
  try {
    return storage === undefined ? globalThis.localStorage : storage;
  } catch {
    return null;
  }
}

/** Storage with every `flipparr.*` key namespaced to the current profile. */
export function profileStorage(storage) {
  return {
    getItem: (key) => base(storage)?.getItem(profileKey(key)) ?? null,
    setItem: (key, value) => base(storage)?.setItem(profileKey(key), value),
    removeItem: (key) => base(storage)?.removeItem(profileKey(key)),
  };
}

// What the browser kept before profiles existed. It was the admin's -- there
// was nobody else -- so it moves into the admin's namespace, once.
export const LEGACY_KEYS = [
  "flipparr.library", "flipparr.reader", "flipparr.search.recent",
  "flipparr.notifications.dismissed", "flipparr.notifications.seenUntil",
];

export function migrateLegacyKeys(storage, viewerId, keys = LEGACY_KEYS) {
  if (Number(viewerId) !== 1) return 0;
  let moved = 0;
  for (const key of keys) {
    try {
      const legacy = storage?.getItem(key);
      if (legacy === null || legacy === undefined) continue;
      const scoped = profileKey(key, 1);
      if (storage.getItem(scoped) === null) {
        storage.setItem(scoped, legacy);
        moved += 1;
      }
      storage.removeItem(key);
    } catch {
      // A browser that will not store keeps nothing to move.
    }
  }
  return moved;
}

// ---- What a profile may see -------------------------------------------------
//
// The server is the boundary -- a reader's request for anything else is
// refused there -- so this is only about not showing a reader controls that
// would be refused. The admin sees everything.

const READER_SURFACES = new Set([
  "nav.library", "nav.settings",
  "settings.profile", "settings.reader",
]);

export function can(viewer, surface) {
  if (!viewer) return true;
  if (viewer.role === "admin") return true;
  return READER_SURFACES.has(surface);
}

export function isAdmin(viewer) {
  return !viewer || viewer.role === "admin";
}

// ---- Drawing a profile --------------------------------------------------------

export const PROFILE_COLOURS = ["violet", "blue", "teal", "green", "amber", "orange", "red", "pink"];

export function profileColour(profile) {
  return PROFILE_COLOURS.includes(profile?.colour) ? profile.colour : "violet";
}

/** One or two letters for an avatar: the first of each of the first two words. */
export function initials(name) {
  const words = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  const letters = words.length === 1 ? [...words[0]].slice(0, 1) : [[...words[0]][0], [...words[1]][0]];
  return letters.join("").toUpperCase();
}

// ---- What switching to a profile asks for -------------------------------------
//
// Each profile chooses: a tap, its PIN or its password. The server says which
// (`lock`); the admin is never a tap while it has a PIN or a password.

export const LOCK_LABELS = { pin: "PIN", password: "Password" };

export function isLocked(profile) {
  return profile?.lock === "pin" || profile?.lock === "password";
}

/** The choices a profile's lock control offers, and why one is unavailable. */
export function lockChoices(profile, { adminPassword = false } = {}) {
  const admin = profile?.role === "admin";
  const owner = profile?.id === 1;
  return [
    { id: "open", label: "Open", disabled: admin, reason: admin ? "The admin always asks for something." : "" },
    { id: "pin", label: "PIN", disabled: false, reason: "" },
    {
      id: "password", label: "Password", disabled: owner && !adminPassword,
      reason: owner && !adminPassword ? "Set a password in Security first." : "",
    },
  ];
}

/**
 * The edit that makes a profile ask for `lock`, or null while it is missing
 * what that needs: a PIN it does not have yet, or a password (the admin's
 * lives in Security, so is never sent from here). A PIN the profile no longer
 * asks for is not kept.
 */
export function lockPatch(profile, lock, { pin = "", password = "" } = {}) {
  const body = { switchLock: lock };
  if (lock === "pin") {
    if (pin) {
      if (!/^\d{4,6}$/.test(pin)) return null;
      body.pin = pin;
    } else if (!profile?.hasPin) {
      return null;
    }
  } else if (profile?.hasPin) {
    body.pin = null;
  }
  if (lock === "password" && profile?.id !== 1 && !profile?.hasPassword) {
    if (password.length < 8) return null;
    body.password = password;
  }
  return body;
}

/** A PIN as the pad builds it: digits only, at most six. */
export function pinInput(current, key) {
  if (key === "Backspace") return current.slice(0, -1);
  if (/^\d$/.test(key) && current.length < 6) return current + key;
  return current;
}
