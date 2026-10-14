// What the app keeps between launches, as small JSON files under
// Library/flipparr (excluded from backups by the native plugin), written
// atomically: a temporary file, then a rename, so a crash mid-write never
// leaves half a list. The only module that touches @capacitor/filesystem.
import { Directory, Encoding, Filesystem } from "@capacitor/filesystem";
import { secureStore } from "./secure-store.js";
import { DEFAULT_CAP_BYTES, comicFolder } from "./offline.js";
import { answerName } from "./offline-reading.js";

const ROOT = "flipparr";
const at = (path) => ({ path: `${ROOT}/${path}`, directory: Directory.Library });

async function readJson(path, fallback) {
  try {
    const { data } = await Filesystem.readFile({ ...at(path), encoding: Encoding.UTF8 });
    return JSON.parse(data);
  } catch {
    return fallback;
  }
}

// Writes to one file, one at a time and in the order asked: two saves at once
// (several comics finishing together, 2026-10-12) shared one .tmp, and the
// second's rename found it gone.
const writing = new Map();
let written = 0;

function writeJson(path, value) {
  const data = JSON.stringify(value);
  const run = (writing.get(path) || Promise.resolve()).catch(() => {}).then(async () => {
    written += 1;
    const temporary = `${path}.${written}.tmp`;
    await Filesystem.writeFile({ ...at(temporary), data, encoding: Encoding.UTF8, recursive: true });
    await Filesystem.rename({ from: `${ROOT}/${temporary}`, to: `${ROOT}/${path}`, directory: Directory.Library, toDirectory: Directory.Library });
  });
  writing.set(path, run);
  run.finally(() => { if (writing.get(path) === run) writing.delete(path); }).catch(() => {});
  return run;
}

export const deviceStore = {
  downloads: (profileId) => readJson(`u${profileId}/downloads.json`, []),
  saveDownloads: (profileId, rows) => writeJson(`u${profileId}/downloads.json`, rows),
  settings: () => readJson("settings.json", {}),
  async capBytes() {
    const settings = await readJson("settings.json", {});
    return Number(settings.capBytes) || DEFAULT_CAP_BYTES;
  },
  async setCapBytes(capBytes) {
    const settings = await readJson("settings.json", {});
    await writeJson("settings.json", { ...settings, capBytes });
  },
  /** Every row across the device's profiles: the cap is the device's. */
  async allDownloads() {
    try {
      const { files } = await Filesystem.readdir(at(""));
      const profiles = files.filter((entry) => entry.type === "directory" && /^u\d+$/.test(entry.name));
      // Each row says whose it is: a download making room may remove another
      // profile's finished comic, and it has to go from that profile's folder.
      return (await Promise.all(profiles.map(async (entry) => {
        const rows = await readJson(`${entry.name}/downloads.json`, []);
        return rows.map((row) => ({ ...row, profileId: entry.name.slice(1) }));
      }))).flat();
    } catch {
      return [];
    }
  },
  savePanels: (profileId, fileId, panels) => writeJson(`${comicFolder(profileId, fileId)}/panels.json`, panels),
  panels: (profileId, fileId) => readJson(`${comicFolder(profileId, fileId)}/panels.json`, null),
  /** Indexes of the per-page panel files a comic already has. */
  async panelPartsOnDisk(profileId, fileId) {
    try {
      const { files } = await Filesystem.readdir(at(comicFolder(profileId, fileId)));
      return files.map((entry) => entry.name.match(/^panels-(\d+)\.json$/)).filter(Boolean).map((match) => Number(match[1]));
    } catch {
      return [];
    }
  },
  /** Every page's panels as one object, {index: answer}; the parts removed. */
  async gatherPanelParts(profileId, fileId, pageCount) {
    const panels = {};
    for (let index = 0; index < pageCount; index += 1) {
      const part = await readJson(`${comicFolder(profileId, fileId)}/panels-${index}.json`, null);
      if (part) panels[index] = part;
      try { await Filesystem.deleteFile(at(`${comicFolder(profileId, fileId)}/panels-${index}.json`)); } catch { /* none */ }
    }
    return panels;
  },
  /** The panels a packed download took in one answer, {index: answer}; the file removed. */
  async takeBulkPanels(profileId, fileId) {
    const path = `${comicFolder(profileId, fileId)}/panels-all.json`;
    const answer = await readJson(path, null);
    try { await Filesystem.deleteFile(at(path)); } catch { /* none */ }
    return answer?.pages || {};
  },
  /** Page files a comic already has: [{index, size}]. */
  async pagesOnDisk(profileId, fileId) {
    try {
      const { files } = await Filesystem.readdir(at(comicFolder(profileId, fileId)));
      return files.map((entry) => {
        const match = entry.name.match(/^(\d+)\.jpg$/);
        return match ? { index: Number(match[1]), size: Number(entry.size) || 0 } : null;
      }).filter(Boolean);
    } catch {
      return [];
    }
  },
  async removeComic(profileId, fileId) {
    try { await Filesystem.rmdir({ ...at(comicFolder(profileId, fileId)), recursive: true }); } catch { /* already gone */ }
  },
  // Reading offline (Phase 4). Places saved while the server could not be
  // reached, waiting to be sent; the reading answers last given, so the
  // library can be browsed; and the last signed-in status, so the app opens
  // as its profile with no connection (no token: those stay in the Keychain).
  progressQueue: (profileId) => readJson(`u${profileId}/progress-queue.json`, []),
  saveProgressQueue: (profileId, queue) => writeJson(`u${profileId}/progress-queue.json`, queue),
  // This device's own choices (Phase 7 Settings): downloading what is opened,
  // and the next issue with it. Not the profile's: they are about this iPad.
  async appSettings() {
    return { downloadOpened: true, downloadNext: false, ...(await readJson("settings.json", {})) };
  },
  async saveAppSettings(next) {
    await writeJson("settings.json", { ...(await this.appSettings()), ...next });
  },
  // "Who's reading?" offline (Phase 5, offline-profiles.js): the profiles
  // as the server last listed them, and each one's last word on who it is.
  profiles: () => readJson("profiles.json", []),
  saveProfiles: (profiles) => writeJson("profiles.json", profiles),
  profileStatus: (profileId) => readJson(`u${profileId}/status.json`, null),
  saveProfileStatus: (profileId, status) => writeJson(`u${profileId}/status.json`, status),
  // Ratings, run and arc marks, reading-list entries made offline (Phase 5,
  // offline-changes.js), waiting to be sent.
  changeQueue: (profileId) => readJson(`u${profileId}/change-queue.json`, []),
  saveChangeQueue: (profileId, queue) => writeJson(`u${profileId}/change-queue.json`, queue),
  answer: (profileId, path) => readJson(`u${profileId}/answers/${answerName(path)}`, null),
  saveAnswer: (profileId, path, value) => writeJson(`u${profileId}/answers/${answerName(path)}`, value),
  pendingRemovals: (profileId) => readJson(`u${profileId}/removals.json`, []),
  savePendingRemovals: (profileId, removals) => writeJson(`u${profileId}/removals.json`, removals),
  session: () => readJson("session.json", null),
  saveSession: (status) => writeJson("session.json", status),
  async forgetSession() {
    try { await Filesystem.deleteFile(at("session.json")); } catch { /* none kept */ }
  },
  /**
   * Everything this install keeps for its server -- downloads, saved places,
   * kept answers, the last session -- gone, as connecting to another server
   * requires: the next server's profiles have their own numbers.
   */
  async forgetEverything() {
    try { await Filesystem.rmdir({ ...at(""), recursive: true }); } catch { /* nothing kept */ }
  },
  /** Whether a file is here: an image fetched before, shown offline. */
  async exists(path) {
    try { await Filesystem.stat(at(path)); return true; } catch { return false; }
  },
  /** The absolute path of a file here, for Capacitor.convertFileSrc. */
  async uri(path) {
    const { uri } = await Filesystem.getUri(at(path));
    return uri;
  },
};

/** This install's id: random, kept in the Keychain, sent with its downloads. */
export async function deviceId() {
  let id = await secureStore.get("device");
  if (!id) {
    id = `ios-${crypto.randomUUID()}`;
    await secureStore.set("device", id);
  }
  return id;
}
