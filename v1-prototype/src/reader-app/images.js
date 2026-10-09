// Images in the app: pages, thumbnails and covers.
//
// The web view cannot put the app's token on an <img> request, so every
// image is fetched by the native plugin to a file (FlipparrNative.fetchToFile)
// and shown from there (Capacitor.convertFileSrc). The token goes only to the
// household's own server; a provider's cover is fetched without it.
//
// A page of a comic downloaded for offline reading (downloads.js) is shown
// from its file and never fetched. With no connection, an image fetched
// before is shown from the cache rather than not at all (Phase 4).
//
// Online reading keeps what it fetched in Library/flipparr/online/u<profile>,
// one folder per profile so a page fetched for one profile is never served
// from the cache to another (the rating gate is the server's; this keeps the
// device from undoing it), trimmed to ONLINE_MAX_BYTES, least recently used
// first. Downloads for offline reading (Phase 3) live elsewhere and are not
// trimmed.
import { Capacitor, registerPlugin } from "@capacitor/core";

const Native = registerPlugin("FlipparrNative");

export const ONLINE_MAX_BYTES = 200 * 1024 * 1024;

/**
 * The online page cache trimmed to `maxBytes` (0 clears it); resolves to
 * `{bytes}` left. A limit above its size removes nothing, so it measures.
 */
export function trimNative({ path, maxBytes }, native = Native) {
  return native.trimFolder({ path, maxBytes });
}
const TRIM_EVERY = 40;

/** A stable file name for an address: FNV-1a, so the same page is one file. */
export function imageName(address) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < address.length; index += 1) {
    hash ^= address.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  // Two passes in different seeds' order, so 64 bits, not 32, name a file.
  let second = 0x01000193;
  for (let index = address.length - 1; index >= 0; index -= 1) {
    second ^= address.charCodeAt(index);
    second = Math.imul(second, 0x811c9dc5) >>> 0;
  }
  return `${hash.toString(16).padStart(8, "0")}${second.toString(16).padStart(8, "0")}.jpg`;
}

/**
 * `source(url)` -> Promise of an address the web view can show. `client`
 * is the server's (src/reader-app/server.js); `profileId` names the cache.
 */
export function createImageSource(client, profileId, {
  native = Native, toSrc = (path) => Capacitor.convertFileSrc(path), local = () => null, localUri = null,
  exists = null,
} = {}) {
  const folder = `online/u${profileId}`;
  const known = new Map();
  let fetched = 0;
  return function source(url) {
    if (!url) return Promise.resolve("");
    if (url.startsWith("data:")) return Promise.resolve(url);
    // A downloaded comic's page is read from the device, network or none.
    const downloaded = local(url);
    if (downloaded && localUri) return localUri(downloaded).then(toSrc);
    if (known.has(url)) return known.get(url);
    const absolute = new URL(url, client.origin);
    const own = absolute.origin === client.origin;
    const cached = `${folder}/${imageName(absolute.href)}`;
    const job = native.fetchToFile({
      url: absolute.href,
      path: cached,
      headers: own ? client.headers() : {},
    }).then(({ path }) => toSrc(path), async (error) => {
      // Unreachable: the copy fetched last time, if there is one.
      if (exists && localUri && await exists(cached)) return toSrc(await localUri(cached));
      throw error;
    });
    known.set(url, job);
    // A failure is not remembered: the next ask tries again.
    job.catch(() => known.delete(url));
    fetched += 1;
    if (fetched % TRIM_EVERY === 0) native.trimFolder({ path: folder, maxBytes: ONLINE_MAX_BYTES }).catch(() => {});
    return job;
  };
}
