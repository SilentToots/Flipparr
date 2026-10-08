// Images in the app: pages, thumbnails and covers.
//
// The web view cannot put the app's token on an <img> request, so every
// image is fetched by the native plugin to a file (FlipparrNative.fetchToFile)
// and shown from there (Capacitor.convertFileSrc). The token goes only to the
// household's own server; a provider's cover is fetched without it.
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
export function createImageSource(client, profileId, { native = Native, toSrc = (path) => Capacitor.convertFileSrc(path) } = {}) {
  const folder = `online/u${profileId}`;
  const known = new Map();
  let fetched = 0;
  return function source(url) {
    if (!url) return Promise.resolve("");
    if (url.startsWith("data:")) return Promise.resolve(url);
    if (known.has(url)) return known.get(url);
    const absolute = new URL(url, client.origin);
    const own = absolute.origin === client.origin;
    const job = native.fetchToFile({
      url: absolute.href,
      path: `${folder}/${imageName(absolute.href)}`,
      headers: own ? client.headers() : {},
    }).then(({ path }) => toSrc(path));
    known.set(url, job);
    // A failure is not remembered: the next ask tries again.
    job.catch(() => known.delete(url));
    fetched += 1;
    if (fetched % TRIM_EVERY === 0) native.trimFolder({ path: folder, maxBytes: ONLINE_MAX_BYTES }).catch(() => {});
    return job;
  };
}
