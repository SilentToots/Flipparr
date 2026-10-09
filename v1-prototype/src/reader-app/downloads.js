// Downloading comics for offline reading (Phase 3). One engine per signed-in
// profile: it asks the server what a comic needs (/files/{id}/offline), makes
// room under the device's cap, hands every page to the background session
// and each page's panels (FlipparrNative.enqueue -- it carries on while the
// app is suspended or the device locked, and moves each file into place
// itself; panels from the local finder only, ?vision=no: the owner's choice,
// 2026-10-08), gathers the panels into panels.json, and records the download
// on the server (schema 68). Rows live in downloads.json; the rules
// are offline.js's.
import { registerPlugin } from "@capacitor/core";
import { deviceStore, deviceId } from "./device-store.js";
import { downloadComplete, downloadRow, pagePath, panelPartPath, panelsUrl, parseTransferId, planDownload, transferId } from "./offline.js";

const Native = registerPlugin("FlipparrNative");

/** Every transfer this install has under way, for any profile, cancelled. */
export function cancelAllTransfers(native = Native) {
  return native.cancel({ prefix: "" }).catch(() => {});
}

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
    this.device = await this.idOfDevice();
    this.listeners = [
      await this.native.addListener("transferDone", (event) => this.onDone(event)),
      await this.native.addListener("transferFailed", (event) => this.onFailed(event)),
    ];
    await this.reconcile();
    this.changed();
    // Back in the foreground: pages may have landed while the app was
    // suspended with no page listening.
    this.onVisible = () => { if (document.visibilityState === "visible") this.recheck(); };
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", this.onVisible);
  }

  stop() {
    this.listeners?.forEach((listener) => listener.remove());
    if (this.onVisible && typeof document !== "undefined") document.removeEventListener("visibilitychange", this.onVisible);
  }

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
      if (row.state !== "ready") continue;
      const index = row.pageUrls.indexOf(url);
      if (index !== -1) return pagePath(this.profileId, row.fileId, index);
    }
    return null;
  }

  /** Download a comic. Refused with a readable reason when it cannot fit. */
  async download(fileId, { groupKey = null } = {}) {
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
    for (const evicted of plan.evict) await this.remove(evicted, "evicted");
    const row = downloadRow(manifest, { groupKey });
    this.rows = [row, ...this.rows.filter((other) => other.fileId !== row.fileId)];
    await this.save();
    await this.enqueueMissing(row, manifest.pages || []);
    return row;
  }

  /**
   * Hand what is still missing to the background session: each page, and
   * each page's panels beside it, so the whole comic finishes while the
   * device is locked or in another app (the owner, 2026-10-09: the panels
   * were fetched by the page, which iOS pauses with the screen).
   */
  async enqueueMissing(row, pages, { skip = new Set() } = {}) {
    const pagesDone = new Set(row.pagesDone);
    const panelsDone = new Set(row.panelsDone || []);
    const headers = this.client.headers();
    for (const page of pages) {
      const pageId = transferId(this.profileId, row.fileId, page.index);
      if (!pagesDone.has(page.index) && !skip.has(pageId)) {
        await this.native.enqueue({
          id: pageId, url: new URL(page.readUrl, this.client.origin).href,
          path: pagePath(this.profileId, row.fileId, page.index), headers,
        });
      }
      const panelsId = transferId(this.profileId, row.fileId, page.index, "panels");
      if (!panelsDone.has(page.index) && !skip.has(panelsId)) {
        await this.native.enqueue({
          id: panelsId, url: new URL(panelsUrl(row.fileId, page.index), this.client.origin).href,
          path: panelPartPath(this.profileId, row.fileId, page.index), headers,
        });
      }
    }
  }

  async onDone(event) {
    const { profileId, fileId, index, kind } = parseTransferId(event.id);
    if (profileId !== this.profileId) return;
    const row = this.row(fileId);
    if (!row || row.state !== "downloading") return;
    this.mark(row, kind, index, Number(event.bytes) || 0);
    if (downloadComplete(row)) await this.finish(row);
    else await this.save();
  }

  /** One page, or one page's panels, in. */
  mark(row, kind, index, bytes = 0) {
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
    if (!row || row.state !== "downloading") return;
    if (kind === "panels" && event.status !== 401) {
      // Best-effort: a page whose panels could not be had is read whole.
      this.mark(row, "panels", index);
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
    // Each page's panels came with it (the local finder only: a download
    // spends no vision budget); gathered into one file, the parts go.
    const panels = await this.store.gatherPanelParts(this.profileId, row.fileId, row.pageCount);
    await this.store.savePanels(this.profileId, row.fileId, panels);
    row.state = "ready";
    row.error = null;
    row.downloadedAt = new Date().toISOString();
    await this.save();
    this.client.api("/api/v1/offline/downloads", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device: this.device, fileId: row.fileId, fileSignature: row.fileSignature, bytes: row.bytes }),
    }).catch(() => {});
  }

  /**
   * A place saved in a downloaded comic: when it was last read, and whether
   * it is finished, so the cap makes way for finished comics read longest ago.
   */
  async noteRead(fileId, { finished = false, at = new Date().toISOString() } = {}) {
    await this.ready;
    const row = this.row(fileId);
    if (!row || row.state !== "ready") return;
    row.lastReadAt = at;
    row.finished = finished;
    await this.save();
  }

  /** Take a comic off the device, and tell the server why. */
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

  /** Send the removals that waited for the server. */
  async flushRemovals() {
    await this.ready;
    for (const removal of await this.store.pendingRemovals(this.profileId)) {
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
      for (const page of await this.store.pagesOnDisk(this.profileId, row.fileId)) this.mark(row, "page", page.index, page.size);
      for (const index of await this.store.panelPartsOnDisk(this.profileId, row.fileId)) this.mark(row, "panels", index);
      if (downloadComplete(row)) { await this.finish(row); continue; }
      const pages = row.pageUrls.map((readUrl, index) => ({ index, readUrl }));
      await this.enqueueMissing(row, pages, { skip: inFlight });
    }
    await this.store.saveDownloads(this.profileId, this.rows);
  }
}
