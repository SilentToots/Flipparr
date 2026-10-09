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

async function writeJson(path, value) {
  const temporary = `${path}.tmp`;
  await Filesystem.writeFile({ ...at(temporary), data: JSON.stringify(value), encoding: Encoding.UTF8, recursive: true });
  await Filesystem.rename({ from: `${ROOT}/${temporary}`, to: `${ROOT}/${path}`, directory: Directory.Library, toDirectory: Directory.Library });
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
      return (await Promise.all(profiles.map((entry) => readJson(`${entry.name}/downloads.json`, [])))).flat();
    } catch {
      return [];
    }
  },
  savePanels: (profileId, fileId, panels) => writeJson(`${comicFolder(profileId, fileId)}/panels.json`, panels),
  panels: (profileId, fileId) => readJson(`${comicFolder(profileId, fileId)}/panels.json`, null),
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
  answer: (profileId, path) => readJson(`u${profileId}/answers/${answerName(path)}`, null),
  saveAnswer: (profileId, path, value) => writeJson(`u${profileId}/answers/${answerName(path)}`, value),
  session: () => readJson("session.json", null),
  saveSession: (status) => writeJson("session.json", status),
  async forgetSession() {
    try { await Filesystem.deleteFile(at("session.json")); } catch { /* none kept */ }
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
