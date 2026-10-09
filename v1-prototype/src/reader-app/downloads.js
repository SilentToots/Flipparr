// Downloading comics for offline reading (Phase 3). One engine per signed-in
// profile: it asks the server what a comic needs (/files/{id}/offline), makes
// room under the device's cap, hands every page to the background session
// (FlipparrNative.enqueue -- it carries on while the app is suspended, and
// moves each file into place itself), fetches the panels from the local
// finder only (?vision=no: the owner's choice, 2026-10-08), and records the
// download on the server (schema 68). Rows live in downloads.json; the rules
// are offline.js's.
import { registerPlugin } from "@capacitor/core";
import { deviceStore, deviceId } from "./device-store.js";
import { downloadRow, pagePath, parseTransferId, planDownload, transferId } from "./offline.js";

const Native = registerPlugin("FlipparrNative");

export class Downloads extends EventTarget {
  constructor({ client, profileId, store = deviceStore, native = Native }) {
    super();
    this.client = client;
    this.profileId = String(profileId);
    this.store = store;
    this.native = native;
    this.rows = [];
    this.reading = null;
    this.finishing = new Set();
    this.ready = this.start();
  }

  async start() {
    this.rows = await this.store.downloads(this.profileId);
    this.device = await deviceId();
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

  async enqueueMissing(row, pages) {
    const done = new Set(row.pagesDone);
    const headers = this.client.headers();
    for (const page of pages) {
      if (done.has(page.index)) continue;
      await this.native.enqueue({
        id: transferId(this.profileId, row.fileId, page.index),
        url: new URL(page.readUrl, this.client.origin).href,
        path: pagePath(this.profileId, row.fileId, page.index),
        headers,
      });
    }
  }

  async onDone(event) {
    const { profileId, fileId, index } = parseTransferId(event.id);
    if (profileId !== this.profileId) return;
    const row = this.row(fileId);
    if (!row || row.state !== "downloading") return;
    if (!row.pagesDone.includes(index)) {
      row.pagesDone = [...row.pagesDone, index];
      row.bytes += Number(event.bytes) || 0;
    }
    if (row.pagesDone.length >= row.pageCount) await this.finish(row);
    else await this.save();
  }

  async onFailed(event) {
    const { profileId, fileId } = parseTransferId(event.id);
    if (profileId !== this.profileId || event.cancelled) return;
    const row = this.row(fileId);
    if (!row || row.state !== "downloading") return;
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
    // Panels from the local finder only: a download spends no vision budget.
    const panels = {};
    for (let index = 0; index < row.pageCount; index += 1) {
      try {
        panels[index] = await this.client.api(`/api/v1/files/${row.fileId}/pages/${index}/panels?vision=no`);
      } catch {
        // Best-effort: a page without panels is read whole.
      }
    }
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

  /** Take a comic off the device, and tell the server why. */
  async remove(fileId, reason = "removed") {
    await this.native.cancel({ prefix: `${this.profileId}:${fileId}:` });
    await this.store.removeComic(this.profileId, fileId);
    this.rows = this.rows.filter((row) => row.fileId !== String(fileId));
    await this.save();
    this.client.api("/api/v1/offline/downloads/remove", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device: this.device, fileId: String(fileId), reason }),
    }).catch(() => {});
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
      for (const page of await this.store.pagesOnDisk(this.profileId, row.fileId)) {
        if (row.pagesDone.includes(page.index)) continue;
        row.pagesDone = [...row.pagesDone, page.index];
        row.bytes += page.size;
      }
      if (row.pagesDone.length >= row.pageCount) { await this.finish(row); continue; }
      const missing = row.pageUrls.map((readUrl, index) => ({ index, readUrl }))
        .filter((page) => !row.pagesDone.includes(page.index) && !inFlight.has(transferId(this.profileId, row.fileId, page.index)));
      await this.enqueueMissing(row, missing);
    }
    await this.store.saveDownloads(this.profileId, this.rows);
  }
}
