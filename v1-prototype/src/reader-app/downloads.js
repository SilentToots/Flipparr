// Downloading comics for offline reading (Phase 3). One engine per signed-in
// profile: it asks the server what a comic needs (/files/{id}/offline), makes
// room under the device's cap, and hands the comic to the background session
// -- as one packed file and one answer of panels where the server packs it
// (2026-10-12: page by page, one profile's downloads filled the server), else
// every page
// and each page's panels (FlipparrNative.enqueue -- it carries on while the
// app is suspended or the device locked, and moves each file into place
// itself; panels only as the server already keeps them, ?stored=only: the
// owner's choice, 2026-10-09), gathers the panels into panels.json, and records the download
// on the server (schema 68). Rows live in downloads.json; the rules
// are offline.js's.
import { registerPlugin } from "@capacitor/core";
import { deviceStore, deviceId } from "./device-store.js";
import {
  bulkPanelsPath, comicFolder, downloadComplete, downloadRow, packagePath, pagePath, panelPartPath, panelsUrl, parseTransferId,
  planDownload, transferId,
} from "./offline.js";

const Native = registerPlugin("FlipparrNative");

/** Every transfer this install has under way, for any profile, cancelled. */
export function cancelAllTransfers(native = Native) {
  return native.cancel({ prefix: "" }).catch(() => {});
}

/** Seconds between the comics of one choice (a run, an arc) starting to download. */
export const DOWNLOAD_STAGGER_SECONDS = 4;

export class Downloads extends EventTarget {
  constructor({ client, profileId, store = deviceStore, native = Native, idOfDevice = deviceId }) {
    super();
    this.client = client;
    this.profileId = String(profileId);
    this.store = store;
    this.native = native;
    this.idOfDevice = idOfDevice;
    this.rows = [];
    this.reading = null;
    this.finishing = new Set();
    this.ready = this.start();
  }

  async start() {
    this.rows = await this.store.downloads(this.profileId);
    // Notes another profile's download left when it removed one of this
    // profile's finished comics to make room: taken out of the rows here,
    // shown once (takeEvicted), never counted or listed as a download.
    this.evicted = this.rows.filter((row) => row.state === "evicted");
    if (this.evicted.length) {
      this.rows = this.rows.filter((row) => row.state !== "evicted");
      await this.store.saveDownloads(this.profileId, this.rows);
    }
    this.device = await this.idOfDevice();
    this.listeners = [
      await this.native.addListener("transferDone", (event) => this.onDone(event)),
      await this.native.addListener("transferFailed", (event) => this.onFailed(event)),
      await this.native.addListener("transferProgress", (event) => this.onProgress(event)),
      await this.native.addListener("transferWaiting", (event) => this.onWaiting(event)),
    ];
    await this.reconcile();
    this.changed();
    // Back in the foreground: pages may have landed while the app was
    // suspended with no page listening.
    this.onVisible = () => { if (document.visibilityState === "visible") this.recheck(); };
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", this.onVisible);
  }

  stop() {
    this.held = true;
    this.listeners?.forEach((listener) => listener.remove());
    if (this.onVisible && typeof document !== "undefined") document.removeEventListener("visibilitychange", this.onVisible);
  }

  /**
   * Held while the app switches profile: a check or a flush already under
   * way finishes without touching anything. The client's token becomes the
   * next profile's the moment the switch is asked, and an answer to this
   * profile's question would otherwise be read as theirs -- a child's
   * `hidden` removed the parent's downloads (the review of 2026-10-14).
   */
  hold() { this.held = true; }
  release() { this.held = false; }

  /** Reconcile again, one at a time. */
  recheck() {
    this.checking = (this.checking || Promise.resolve())
      .then(() => this.reconcile())
      .then(() => this.changed(), () => {});
    return this.checking;
  }

  changed() {
    this.dispatchEvent(new Event("change"));
  }

  async save() {
    await this.store.saveDownloads(this.profileId, this.rows);
    this.changed();
  }

  row(fileId) {
    return this.rows.find((row) => row.fileId === String(fileId)) || null;
  }

  /** Where a page address is on this device, if its comic is downloaded. */
  localPage(url) {
    for (const row of this.rows) {
      if (row.state !== "ready" && row.state !== "stale") continue;
      const index = row.pageUrls.indexOf(url);
      if (index !== -1) return pagePath(this.profileId, row.fileId, index);
    }
    return null;
  }

  /** Download a comic. Refused with a readable reason when it cannot fit. */
  async download(fileId, { groupKey = null, startIn = 0 } = {}) {
    await this.ready;
    const existing = this.row(fileId);
    if (existing && (existing.state === "ready" || existing.state === "downloading")) return existing;
    let manifest;
    try {
      manifest = await this.client.api(`/api/v1/files/${fileId}/offline`);
    } catch (error) {
      if (error.status === 404) throw new Error("This comic is not available to download");
      throw error;
    }
    const plan = planDownload(Number(manifest.bytesEstimate) || 0, {
      capBytes: await this.store.capBytes(),
      rows: await this.store.allDownloads(),
      reading: this.reading,
    });
    if (!plan.fits) {
      const megabytes = Math.ceil(plan.shortfall / (1024 * 1024));
      throw new Error(`Needs ${megabytes} MB more than this device's download limit allows`);
    }
    for (const evicted of plan.evict) await this.evict(evicted);
    const row = downloadRow(manifest, { groupKey });
    this.rows = [row, ...this.rows.filter((other) => other.fileId !== row.fileId)];
    await this.save();
    await this.enqueueMissing(row, manifest.pages || [], { startIn });
    return row;
  }

  /**
   * Several comics, in order, as one choice (a run's next unread, an arc):
   * each planned as it is asked for, so each sees the room the last took.
   * Stops at the first that cannot be had and says why. {started, error}.
   */
  async downloadMany(fileIds, { groupKey = null } = {}) {
    let started = 0;
    for (const fileId of fileIds) {
      try {
        // Each comic a little after the last, about as fast as the server
        // packs them: a run asked for at once sent every comic's requests
        // together and queued the server's other answers behind them
        // (2026-10-14). All are handed to iOS now, so a locked phone still
        // downloads the whole run; iOS starts each when its time comes.
        await this.download(fileId, { groupKey, startIn: started * DOWNLOAD_STAGGER_SECONDS });
        started += 1;
      } catch (error) {
        return { started, error: error.message || "The download could not start" };
      }
    }
    return { started, error: null };
  }

  /** Every finished comic off the device; resolves to how many went. */
  async removeFinished() {
    await this.ready;
    const finished = this.rows.filter((row) => row.state === "ready" && row.finished).map((row) => row.fileId);
    for (const fileId of finished) await this.remove(fileId, "removed");
    return finished.length;
  }

  /**
   * Hand what is still missing to the background session: each page, and
   * each page's panels beside it, so the whole comic finishes while the
   * device is locked or in another app (the owner, 2026-10-09: the panels
   * were fetched by the page, which iOS pauses with the screen).
   */
  async enqueueMissing(row, pages, { skip = new Set(), startIn = 0 } = {}) {
    if (row.packageUrl) return this.enqueuePackage(row, { skip, startIn });
    const pagesDone = new Set(row.pagesDone);
    const panelsDone = new Set(row.panelsDone || []);
    const headers = this.client.headers();
    for (const page of pages) {
      const pageId = transferId(this.profileId, row.fileId, page.index);
      if (!pagesDone.has(page.index) && !skip.has(pageId)) {
        await this.native.enqueue({
          id: pageId, url: new URL(page.readUrl, this.client.origin).href,
          path: pagePath(this.profileId, row.fileId, page.index), headers, startIn,
        });
      }
      const panelsId = transferId(this.profileId, row.fileId, page.index, "panels");
      if (!panelsDone.has(page.index) && !skip.has(panelsId)) {
        await this.native.enqueue({
          id: panelsId, url: new URL(panelsUrl(row.fileId, page.index), this.client.origin).href,
          path: panelPartPath(this.profileId, row.fileId, page.index), headers, startIn,
        });
      }
    }
  }

  /**
   * A packed comic: the package, unpacked by the native side into the
   * comic's folder as it lands, and the panels' one answer. The server packs
   * a comic before sending it and says how long that takes; the session asks
   * again then by itself, with the app asleep.
   */
  async enqueuePackage(row, { skip = new Set(), startIn = 0 } = {}) {
    const headers = this.client.headers();
    const packageId = transferId(this.profileId, row.fileId, null, "package");
    if ((row.pagesDone?.length || 0) < row.pageCount && !skip.has(packageId)) {
      await this.native.enqueue({
        id: packageId, url: new URL(row.packageUrl, this.client.origin).href,
        path: packagePath(this.profileId, row.fileId), unpack: comicFolder(this.profileId, row.fileId), headers, startIn,
      });
    }
    const panelsId = transferId(this.profileId, row.fileId, null, "allPanels");
    if ((row.panelsDone?.length || 0) < row.pageCount && !skip.has(panelsId)) {
      if (!row.panelsUrl) {
        this.mark(row, "allPanels");
      } else {
        await this.native.enqueue({
          id: panelsId, url: new URL(row.panelsUrl, this.client.origin).href,
          path: bulkPanelsPath(this.profileId, row.fileId), headers, startIn,
        });
      }
    }
  }

  /** The server still packing a comic: said under its cover until bytes come. */
  onWaiting(event) {
    const { profileId, fileId, kind } = parseTransferId(event.id);
    if (profileId !== this.profileId || kind !== "package") return;
    const row = this.row(fileId);
    if (!row || row.state !== "downloading") return;
    row.preparing = true;
    // Whatever arrived so far was the "still being packed" answer, not the
    // comic: the ring starts again from nothing (2026-10-14).
    row.received = 0;
    delete row.expected;
    this.changed();
  }

  /** A packed comic's bytes as they arrive, for its ring; not written down. */
  onProgress(event) {
    const { profileId, fileId, kind } = parseTransferId(event.id);
    if (profileId !== this.profileId || kind !== "package") return;
    const row = this.row(fileId);
    if (!row || row.state !== "downloading") return;
    row.preparing = false;
    row.received = Number(event.written) || 0;
    if (Number(event.expected) > 0) row.expected = Number(event.expected);
    this.changed();
  }

  async onDone(event) {
    const { profileId, fileId, index, kind } = parseTransferId(event.id);
    if (profileId !== this.profileId) return;
    const row = this.row(fileId);
    if (row && row.state === "ready" && kind === "allPanels" && row.panelsWanted) {
      await this.store.savePanels(this.profileId, row.fileId, await this.store.takeBulkPanels(this.profileId, row.fileId));
      row.panelsAt = row.panelsWanted;
      delete row.panelsWanted;
      await this.save();
      this.changed();
      return;
    }
    if (!row || row.state !== "downloading") return;
    this.mark(row, kind, index, Number(event.bytes) || 0);
    if (downloadComplete(row)) await this.finish(row);
    else await this.save();
  }

  /** One page, or one page's panels, in. */
  mark(row, kind, index, bytes = 0) {
    const every = () => Array.from({ length: row.pageCount }, (_, page) => page);
    if (kind === "package") {
      row.pagesDone = every();
      row.bytes = bytes || row.bytes;
      row.received = row.expected || row.received;
      return;
    }
    if (kind === "allPanels") {
      row.panelsDone = every();
      return;
    }
    if (kind === "panels") {
      if (!(row.panelsDone || []).includes(index)) row.panelsDone = [...(row.panelsDone || []), index];
      return;
    }
    if (!row.pagesDone.includes(index)) {
      row.pagesDone = [...row.pagesDone, index];
      row.bytes += bytes;
    }
  }

  async onFailed(event) {
    const { profileId, fileId, index, kind } = parseTransferId(event.id);
    if (profileId !== this.profileId || event.cancelled) return;
    const row = this.row(fileId);
    if (row && row.state === "ready" && kind === "allPanels" && row.panelsWanted) {
      // Asked again at the next check; the copy's panels stand meanwhile.
      delete row.panelsWanted;
      await this.save();
      return;
    }
    if (!row || row.state !== "downloading") return;
    if (kind === "package" && event.status === 409) {
      // The comic changed on the server since it was asked for: as it is now.
      await this.redownload(fileId);
      return;
    }
    if ((kind === "panels" || kind === "allPanels") && event.status !== 401) {
      // Best-effort: a page whose panels could not be had is read whole.
      this.mark(row, kind, index);
      if (downloadComplete(row)) await this.finish(row);
      else await this.save();
      return;
    }
    if (event.status === 404 || event.status === 403) {
      // The server stopped serving it to this profile: nothing of it stays.
      await this.remove(fileId, "hidden");
      return;
    }
    row.state = "failed";
    row.error = event.status === 401 ? "Sign in again to finish this download" : "The download stopped. Try again.";
    await this.native.cancel({ prefix: `${this.profileId}:${fileId}:` });
    await this.save();
  }

  async finish(row) {
    // Once per comic: a reconcile and the last page's transferDone can both
    // see it complete.
    if (row.state !== "downloading" || this.finishing.has(row.fileId)) return;
    this.finishing.add(row.fileId);
    try {
      await this.finishOnce(row);
    } finally {
      this.finishing.delete(row.fileId);
    }
  }

  async finishOnce(row) {
    // Each page's panels came with it, as the server kept them (a page with
    // none is read whole offline); gathered into one file, the parts go.
    const panels = row.packageUrl
      ? await this.store.takeBulkPanels(this.profileId, row.fileId)
      : await this.store.gatherPanelParts(this.profileId, row.fileId, row.pageCount);
    await this.store.savePanels(this.profileId, row.fileId, panels);
    // Panels the server reads after this are taken again (refreshPanels).
    row.panelsAt = new Date().toISOString();
    row.state = "ready";
    row.error = null;
    row.downloadedAt = new Date().toISOString();
    row.noted = false;
    await this.save();
    this.noteDownloaded(row).catch(() => {});
  }

  /**
   * The server's record of what this device keeps (schema 68), told of a
   * finished download. A comic often finishes while iOS has the app paused
   * (the iPad locked), where this request fails; `noted` stays false and
   * flushRemovals sends it again when the app runs and the server answers
   * (the owner's TestFlight run, 2026-10-10: 3 of 5 notes arrived). The
   * server updates a row it has, so a repeat is harmless.
   */
  async noteDownloaded(row) {
    await this.client.api("/api/v1/offline/downloads", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device: this.device, fileId: row.fileId, fileSignature: row.fileSignature, bytes: row.bytes }),
    });
    row.noted = true;
    await this.save();
  }

  /**
   * A place saved in a downloaded comic: when it was last read, and whether
   * it is finished, so the cap makes way for finished comics read longest ago.
   * A comic still on its way counts too -- opening one starts its download,
   * and the pages read meanwhile are reading (it said "Unread" otherwise).
   */
  async noteRead(fileId, { finished = false, at = new Date().toISOString() } = {}) {
    await this.ready;
    const row = this.row(fileId);
    if (!row || row.state === "failed") return;
    row.lastReadAt = at;
    row.finished = finished;
    await this.save();
  }

  /** Take a comic off the device, and tell the server why. */
  /** The notes of comics removed to make room while this profile was away, once. */
  takeEvicted() {
    const notes = this.evicted || [];
    this.evicted = [];
    return notes;
  }

  /**
   * A finished comic that makes way for a download -- as often another
   * profile's as this one's, since the cap is the device's. It leaves its
   * own profile's folder and rows; that profile is left a note, shown when
   * they next open the app, and the server is told as them when their
   * session is next at hand (removals.json, flushRemovals). Before, the
   * comic was removed under *this* profile's id: the other's stayed, this
   * profile's unfinished copy went instead, and the server was told of a
   * removal that never was (the review of 2026-10-14).
   */
  async evict(row) {
    const owner = String(row.profileId ?? this.profileId);
    const fileId = String(row.fileId);
    if (owner === this.profileId) {
      await this.remove(fileId, "evicted");
      return;
    }
    await this.store.removeComic(owner, fileId);
    const theirs = await this.store.downloads(owner);
    const note = {
      fileId, seriesTitle: row.seriesTitle, issueNumber: row.issueNumber, filename: row.filename,
      state: "evicted", evictedAt: new Date().toISOString(), evictedFor: this.profileId, bytes: 0, estimate: 0,
    };
    await this.store.saveDownloads(owner, [note, ...theirs.filter((other) => other.fileId !== fileId)]);
    const waiting = await this.store.pendingRemovals(owner);
    await this.store.savePendingRemovals(owner, [...waiting.filter((item) => item.fileId !== fileId), { fileId, reason: "evicted" }]);
  }

  async remove(fileId, reason = "removed") {
    await this.native.cancel({ prefix: `${this.profileId}:${fileId}:` });
    await this.store.removeComic(this.profileId, fileId);
    this.rows = this.rows.filter((row) => row.fileId !== String(fileId));
    await this.save();
    await this.tellRemoved({ fileId: String(fileId), reason });
  }

  /**
   * The server's record of what this device keeps (schema 68), told of a
   * removal. Unreachable, the removal waits and is sent when the server is
   * back (flushRemovals), so the record does not keep a comic that left.
   */
  async tellRemoved(removal) {
    try {
      await this.client.api("/api/v1/offline/downloads/remove", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: this.device, ...removal }),
      });
    } catch (error) {
      if (!error.network) return;
      const waiting = await this.store.pendingRemovals(this.profileId);
      await this.store.savePendingRemovals(this.profileId, [...waiting.filter((item) => item.fileId !== removal.fileId), removal]);
    }
  }

  /** Send the removals that waited for the server, and the downloads it was not told of. */
  async flushRemovals() {
    await this.ready;
    // A row from before `noted` existed counts as told: telling again is
    // harmless, but every old download at once is not worth it.
    for (const row of this.rows.filter((item) => item.state === "ready" && item.noted === false)) {
      if (this.held) return;
      try { await this.noteDownloaded(row); } catch (error) { if (error.network) break; }
    }
    for (const removal of await this.store.pendingRemovals(this.profileId)) {
      if (this.held) return;
      try {
        await this.client.api("/api/v1/offline/downloads/remove", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ device: this.device, ...removal }),
        });
      } catch (error) {
        if (error.network) return;
        // Refused: it will not be accepted later either.
      }
      const waiting = await this.store.pendingRemovals(this.profileId);
      await this.store.savePendingRemovals(this.profileId, waiting.filter((item) => item.fileId !== removal.fileId));
    }
  }

  /**
   * On launching, reconnecting and coming back to the foreground (Phase 5):
   * whether every kept comic is still this profile's to read. One the admin
   * has put above its rating limit, or the library no longer has, goes from
   * the device; one replaced on the server is kept, readable, as `stale`
   * with Download again. Resolves to how many went; unreachable, to 0.
   */
  async check() {
    await this.ready;
    if (this.held) return 0;
    const kept = this.rows.filter((row) => row.state === "ready" || row.state === "stale");
    let removed = 0;
    let changed = false;
    // The server takes 500 at a time.
    for (let start = 0; start < kept.length; start += 500) {
      let answer;
      try {
        answer = await this.client.api("/api/v1/offline/check", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ files: kept.slice(start, start + 500).map((row) => ({ fileId: row.fileId, fileSignature: row.fileSignature })) }),
        });
      } catch {
        break;
      }
      // Asked as this profile, answered after a switch: not this profile's answer.
      if (this.held) return 0;
      for (const { fileId, status, lastReadAt, finished, panelsAt } of answer?.files || []) {
        const row = this.row(fileId);
        if (!row) continue;
        // Panels read on the server since this copy's -- the overnight read
        // finds them for comics already here (2026-10-14): taken again, so
        // offline reading has them too.
        if (status === "ok" && row.state === "ready" && await this.refreshPanels(row, panelsAt)) changed = true;
        // Where this profile is with it, as the server knows -- read on the
        // web or another device too (the owner, 2026-10-11: finished on one
        // iPad, Unread on the other). Taken only when newer than what this
        // device noted; a read made here offline and not yet sent is newer.
        if (lastReadAt && (!row.lastReadAt || Date.parse(lastReadAt) > Date.parse(row.lastReadAt))) {
          row.lastReadAt = lastReadAt;
          row.finished = Boolean(finished);
          changed = true;
        }
        if (status === "hidden" || status === "gone") {
          await this.remove(fileId, status);
          removed += 1;
        } else if (status === "changed" && row.state === "ready") {
          row.state = "stale";
          changed = true;
        } else if (status === "ok" && row.state === "stale") {
          row.state = "ready";
          changed = true;
        }
      }
    }
    if (changed) await this.save();
    return removed;
  }

  /**
   * A downloaded comic's panels again, when the server's are newer than this
   * copy's (`panelsAt`). One transfer, the comic's bulk panels; panels.json
   * is replaced when it lands. Comics downloaded page by page (an older
   * server) keep theirs. Resolves to whether one was asked for.
   */
  async refreshPanels(row, panelsAt) {
    if (!panelsAt || !row.panelsUrl || row.panelsWanted) return false;
    if (row.panelsAt && Date.parse(panelsAt) <= Date.parse(row.panelsAt)) return false;
    row.panelsWanted = panelsAt;
    await this.native.enqueue({
      id: transferId(this.profileId, row.fileId, null, "allPanels"),
      url: new URL(row.panelsUrl, this.client.origin).href,
      path: bulkPanelsPath(this.profileId, row.fileId), headers: this.client.headers(),
    });
    return true;
  }

  /** A comic replaced on the server, downloaded again as it now is. */
  async redownload(fileId) {
    const row = this.row(fileId);
    const groupKey = row?.groupKey || null;
    await this.remove(fileId, "changed");
    return this.download(fileId, { groupKey });
  }

  /** Try a failed download again from where it stopped. */
  async retry(fileId) {
    const row = this.row(fileId);
    if (!row) return;
    row.state = "downloading";
    row.error = null;
    await this.save();
    await this.enqueueMissing(row, row.pageUrls.map((readUrl, index) => ({ index, readUrl })));
  }

  /**
   * After a launch: pages that landed while no page was listening are on
   * disk already (the native side moves them); count them, finish what is
   * complete, and hand what is missing and not in flight back to the session.
   */
  async reconcile() {
    const { transfers = [] } = await this.native.transfers().catch(() => ({}));
    const inFlight = new Set(transfers.map((transfer) => transfer.id));
    for (const row of this.rows.filter((other) => other.state === "downloading")) {
      if (row.packageUrl) {
        await this.reconcilePackage(row, inFlight);
        if (downloadComplete(row)) await this.finish(row);
        else await this.enqueuePackage(row, { skip: inFlight });
        continue;
      }
      for (const page of await this.store.pagesOnDisk(this.profileId, row.fileId)) this.mark(row, "page", page.index, page.size);
      for (const index of await this.store.panelPartsOnDisk(this.profileId, row.fileId)) this.mark(row, "panels", index);
      if (downloadComplete(row)) { await this.finish(row); continue; }
      const pages = row.pageUrls.map((readUrl, index) => ({ index, readUrl }));
      await this.enqueueMissing(row, pages, { skip: inFlight });
    }
    await this.store.saveDownloads(this.profileId, this.rows);
  }

  /**
   * A packed comic after a launch: its pages are on disk if the package
   * landed while no page listened; a package left whole (the app stopped
   * as it was unpacked) is unpacked now; the panels' answer, if it came.
   */
  async reconcilePackage(row, inFlight) {
    const onDisk = await this.store.pagesOnDisk(this.profileId, row.fileId);
    const packageId = transferId(this.profileId, row.fileId, null, "package");
    if (onDisk.length >= row.pageCount) {
      this.mark(row, "package", null, onDisk.reduce((sum, page) => sum + page.size, 0));
    } else if (!inFlight.has(packageId) && await this.store.exists(packagePath(this.profileId, row.fileId))) {
      try {
        await this.native.unpack({ path: packagePath(this.profileId, row.fileId), folder: comicFolder(this.profileId, row.fileId) });
        const pages = await this.store.pagesOnDisk(this.profileId, row.fileId);
        if (pages.length >= row.pageCount) this.mark(row, "package", null, pages.reduce((sum, page) => sum + page.size, 0));
      } catch { /* fetched again below */ }
    }
    const panelsId = transferId(this.profileId, row.fileId, null, "allPanels");
    if (!inFlight.has(panelsId) && await this.store.exists(bulkPanelsPath(this.profileId, row.fileId))) this.mark(row, "allPanels");
  }
}
