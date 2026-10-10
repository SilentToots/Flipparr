// Flipparr View: the app around the web app's own screens -- the Comics page,
// its drawers and the reader (Phase 3b: one UI). Find the server, sign in,
// see what to read, read it, keep some on the device. Search, requests and
// everything an admin manages stay in the web app.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowClockwise, BookOpen, DownloadSimple, ShieldCheck, Trash, WifiSlash, X } from "@phosphor-icons/react";
import { FlipparrMark } from "../brand.jsx";
import { LoadingIndicator } from "../components/LoadingIndicator";
import { SettingsCard, Toggle } from "../components/SettingsControls.jsx";
import { PasswordInput } from "../components/PasswordInput.jsx";
import { GlassSelect } from "../components/glass.jsx";
import { useToast } from "../components/Toast.jsx";
import { PinPad, ProfileAvatar, ProfileTiles } from "../components/ProfileTiles.jsx";
import { ComicsIcon, DownloadsNavIcon, ReadingListIcon, SettingsNavIcon } from "../design-icons.jsx";
import { AboutSettings, PRIVACY_SECTIONS } from "../settings/about.jsx";
import { FinishDrawer } from "../drawers/FinishDrawer.jsx";
import { ReadingListDrawer } from "../drawers/ReadingListDrawer.jsx";
import { RunCollectionDrawer } from "../drawers/RunCollectionDrawer.jsx";
import { SeriesDrawer } from "../drawers/SeriesDrawer.jsx";
import { CollectedEditionsContext } from "../editions.js";
import { Nav } from "../frame/Nav.jsx";
import { HeaderContext, PageHeader } from "../frame/header.jsx";
import { usePullToRefresh } from "../frame/pullRefresh.jsx";
import { Host } from "../host.js";
import { apiRequest, forgetAnswers, lastAnswer, setApiTransport } from "../api.js";
import { readableFile, readingContext, useReadingActions } from "../library/actions.js";
import { LibraryView, logicalCatalogSeries } from "../library/LibraryView.jsx";
import { isLocked, profileStorage, setStorageProfile } from "../profiles.js";
import { EMPTY_ENTRIES, isOnReadingList } from "../reading-list-tab.js";
import { ReaderView } from "../reader/ReaderView.jsx";
import { collectionCards } from "../run-collections.js";
import { ViewerContext } from "../viewer.js";
import { createClient, findServer } from "./server.js";
import { secureStore, tokenKeys } from "./secure-store.js";
import { ONLINE_MAX_BYTES, createImageSource, trimNative } from "./images.js";
import { hashPin, newPinSalt, verifyPin } from "./offline-pin.js";
import { mergeProfiles, offlineChoice, withPinHash } from "./offline-profiles.js";
import { Downloads, cancelAllTransfers } from "./downloads.js";
import { deviceStore } from "./device-store.js";
import { CAP_CHOICES, coverStatusFor, formatBytes, nextUnread, planDownloads, progressOf, readerCatalog, rowsFor, usageBytes } from "./offline.js";
import { DialogCloseButton, useDialog } from "../dialogs.jsx";
import { PROBE_EVERY_MS, createAppTransport } from "./transport.js";

/** How long a launch's background check of the session waits for the server. */
const LAUNCH_CHECK_MS = 5000;

/** This install's salt for PINs kept on the device, made the first time. */
async function pinSalt(origin) {
  const key = tokenKeys(origin).pinSalt;
  let salt = await secureStore.get(key);
  if (!salt) { salt = newPinSalt(); await secureStore.set(key, salt); }
  return salt;
}

/**
 * A profile the server has just signed in here, kept so it can be switched
 * back to offline (offline-profiles.js): its session token and who it is.
 */
async function rememberProfile(origin, answer) {
  const keys = tokenKeys(origin);
  const token = await secureStore.get(keys.session);
  if (token) await secureStore.set(keys.profileSession(answer.viewer.id), token);
  await deviceStore.saveProfileStatus(answer.viewer.id, answer);
}

/**
 * A shared iPad the admin has forgotten ("forget shared devices"): the
 * sessions kept for switching offline, and the profiles list, go, so no
 * profile can be opened here without Flipparr again. Downloads stay, each
 * profile's own, and are checked against the server when it next signs in.
 */
async function forgetProfiles(origin) {
  const keys = tokenKeys(origin);
  for (const profile of await deviceStore.profiles()) await secureStore.remove(keys.profileSession(profile.id));
  await deviceStore.saveProfiles([]);
}

async function openClient(origin) {
  const keys = tokenKeys(origin);
  const tokens = {
    session: await secureStore.get(keys.session),
    device: await secureStore.get(keys.device),
    async save(next) {
      await (next.session ? secureStore.set(keys.session, next.session) : secureStore.remove(keys.session));
      await (next.device ? secureStore.set(keys.device, next.device) : secureStore.remove(keys.device));
    },
  };
  return createClient({ origin, tokens });
}

export function ReaderApp() {
  const [phase, setPhase] = useState("starting");
  const [client, setClient] = useState(null);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  // A profile chosen in the header's menu that asks for its PIN or password.
  const [asking, setAsking] = useState(null);

  // A session that ends while the app is open -- a new PIN, the admin's
  // first reader, "forget shared devices" -- answers 401 to whatever asked:
  // the shared screens' requests come through here, so any of them sends the
  // device back to where it now stands (the picker on a shared one, else
  // sign-in).
  const lostRef = useRef(() => {});
  const settledViewer = useRef(null);
  // Whether this device was shared when last told: one that stops being
  // shared was forgotten by the admin (forgetProfiles).
  const wasShared = useRef(false);
  // Every shared screen's requests, with reading offline built in
  // (transport.js). `online` is its word on whether the server answers.
  const transportRef = useRef(null);
  const [online, setOnline] = useState(true);
  async function enter(origin) {
    const next = await openClient(origin);
    const transport = createAppTransport({ client: next, store: deviceStore, onLost: () => lostRef.current() });
    transportRef.current = transport;
    transport.subscribe((now) => {
      setOnline(now);
      // Back: where this device stands may have changed while it was away
      // (a new PIN, devices forgotten), so the server is asked again.
      if (now) next.api("/api/v1/auth/status").then((answer) => settle(next, answer), () => {});
    });
    setApiTransport(transport.transport);
    setClient(next);
    // Signed in here before: open at once as that profile, from what the
    // device kept, and ask the server in the background -- a launch never
    // waits on the network (owner, 2026-10-09: airplane mode showed nothing
    // for seconds). iOS saying there is no connection is believed straight
    // away. If the server says the session ended, settle() moves on to
    // "Who's reading?" or sign-in.
    const kept = await deviceStore.session();
    if (kept?.viewer) {
      if (navigator.onLine === false) transport.setOffline("navigator.onLine is false at launch");
      await settle(next, kept, { kept: true });
      if (navigator.onLine !== false) {
        transport.transport("/api/v1/auth/status", { timeoutMs: LAUNCH_CHECK_MS })
          .then((answer) => settle(next, answer), () => { /* unreachable: the transport says offline */ });
      }
      return;
    }
    let answer;
    try {
      answer = await transport.transport("/api/v1/auth/status");
    } catch (error) {
      // No connection: open as the profile last signed in here, from what
      // the device kept, rather than at the connect screen. (Resuming is not
      // switching: a PIN guards switching, as on the web.)
      const saved = error.network ? await deviceStore.session() : null;
      if (!saved?.viewer) throw error;
      transport.setOffline("no connection at launch");
      await settle(next, saved, { kept: true });
      return;
    }
    await settle(next, answer);
  }

  // Signed in or not, from /auth/status. Signed in, the profile's own
  // browser storage is chosen (as the web app does, profiles.js) and its
  // reader settings are taken from the server, where they follow the person
  // between devices -- without this the reader fell back to a phone's
  // default, panel view on.
  async function settle(next, answer, { kept = false } = {}) {
    // Another profile, or none, sees nothing answered for the last one: the
    // shared screens draw from api.js's last answers while they ask again,
    // and a reader must never glimpse a library above their rating limit (as
    // the web app's enterProfile forgets them).
    const viewerId = answer.viewer?.id ?? null;
    if (viewerId !== settledViewer.current) { forgetAnswers(); settledViewer.current = viewerId; }
    setStatus(answer);
    // A shared device (the admin signed in on it) that has not been told
    // who is reading asks, as the web app's picker does.
    if (!(answer.authenticated && answer.viewer)) {
      if (wasShared.current && !answer.household) forgetProfiles(next.origin).catch(() => {});
      wasShared.current = Boolean(answer.household);
      transportRef.current?.attach(null);
      deviceStore.forgetSession().catch(() => {});
      setPhase(answer.household && answer.profileRequired ? "picker" : "signin");
      return;
    }
    wasShared.current = Boolean(answer.household);
    setStorageProfile(answer.viewer.id);
    transportRef.current?.attach(answer.viewer.id);
    // From the session the device kept: open now; the server's answer, when
    // it comes, saves the session and brings the reader settings.
    if (kept) { setPhase("ready"); return; }
    deviceStore.saveSession(answer).catch(() => {});
    rememberProfile(next.origin, answer).catch(() => {});
    try {
      const me = await next.api("/api/v1/me");
      const prefs = me?.prefs || {};
      if (Object.keys(prefs).length) {
        const storage = profileStorage();
        let local = {};
        try { local = JSON.parse(storage.getItem("flipparr.reader") || "{}") || {}; } catch { local = {}; }
        storage.setItem("flipparr.reader", JSON.stringify({ ...local, ...prefs }));
      }
    } catch {
      // Unreachable or refused: the device's copy stands.
    }
    setPhase("ready");
  }

  useEffect(() => {
    (async () => {
      try {
        const origin = await secureStore.get("server");
        if (!origin) { setPhase("connect"); return; }
        await enter(origin);
      } catch (err) {
        setError(err.message || "Flipparr could not be reached");
        setPhase("connect");
      }
    })();
  }, []);

  // Where a 401 leaves this device: ask the server (the picker on a shared
  // one, else sign-in).
  async function lost() {
    try { await settle(client, await client.api("/api/v1/auth/status")); } catch { setPhase("signin"); }
  }
  lostRef.current = lost;
  const openPicker = useCallback(() => { setAsking(null); setPhase("picker"); }, []);
  // From the header's profile menu: a profile that asks for its PIN or
  // password asks on the picker; one that does not opens at once.
  const switchTo = useCallback(async (profile) => {
    // Offline, the picker knows what this iPad can switch to without Flipparr.
    if (isLocked(profile) || transportRef.current?.online === false) { setAsking(profile); setPhase("picker"); return; }
    try {
      await client.api("/api/v1/profiles/switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ userId: profile.id }) });
      await settle(client, await client.api("/api/v1/auth/status"));
    } catch (err) { setError(err.message); }
  }, [client]);
  // Sign out works whatever the server is doing (owner, 2026-10-09: with it
  // down there was no way out to another server):
  // - sign-in off: there is no account to leave, so it leaves the server;
  // - the server answers: it ends the session there, and the device goes
  //   where the server says (the picker on a shared iPad, else sign-in);
  // - it does not: a shared iPad goes to "Who's reading?", which works
  //   offline; any other forgets the session here and goes to sign-in. The
  //   token is gone from the device, and the server's copy lapses on its own.
  // Both screens offer "Use another server".
  const signOut = useCallback(async () => {
    if (status?.method !== "forms") { await forgetServerRef.current(); return; }
    const transport = transportRef.current;
    if (transport && transport.online !== false) {
      try {
        await transport.transport("/api/v1/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", timeoutMs: 5000 });
      } catch { /* refused or unreachable: the transport knows which */ }
      if (transport.online !== false) { await lostRef.current(); return; }
    }
    setAsking(null);
    if (status?.household) { setPhase("picker"); return; }
    const keys = tokenKeys(client.origin);
    await secureStore.remove(keys.session);
    if (status?.viewer) await secureStore.remove(keys.profileSession(status.viewer.id));
    await deviceStore.forgetSession();
    transport?.attach(null);
    setPhase("signin");
  }, [client, status]);

  // Coming back to the foreground: send what waited, or ask whether the
  // server is back; and while offline, ask every half minute.
  useEffect(() => {
    const transport = transportRef.current;
    if (!transport || phase !== "ready") return undefined;
    const check = () => (transport.online ? transport.replay() : transport.probe());
    const onVisible = () => { if (document.visibilityState === "visible") check(); };
    document.addEventListener("visibilitychange", onVisible);
    // iOS's own word on the connection, as soon as it changes.
    const onOffline = () => transport.setOffline("iOS sent the offline event");
    const onOnline = () => transport.probe();
    window.addEventListener("offline", onOffline);
    window.addEventListener("online", onOnline);
    const timer = window.setInterval(() => { if (!transport.online) transport.probe(); }, PROBE_EVERY_MS);
    check();
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("offline", onOffline);
      window.removeEventListener("online", onOnline);
      window.clearInterval(timer);
    };
  }, [phase, client]);

  // Another server: what this install kept for this one goes (its profiles'
  // downloads and places would otherwise sit under the next server's
  // profile numbers), after saying so when there is anything to lose.
  const forgetServerRef = useRef(() => {});
  forgetServerRef.current = () => forgetServer();
  async function forgetServer() {
    const kept = (await deviceStore.allDownloads()).length;
    if (kept && !window.confirm(`Connecting to another server removes ${kept} downloaded ${kept === 1 ? "comic" : "comics"} and every saved place still waiting to be sent from this device.`)) return;
    // Ends the session on the server when it answers quickly; unreachable,
    // the device forgets it all the same (the token lapses on its own).
    const transport = transportRef.current;
    if (transport && transport.online !== false) {
      try {
        await transport.transport("/api/v1/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", timeoutMs: 5000 });
      } catch { /* ended or unreachable */ }
    }
    if (client) {
      const keys = tokenKeys(client.origin);
      for (const profile of await deviceStore.profiles()) await secureStore.remove(keys.profileSession(profile.id));
      await secureStore.remove(keys.pinSalt);
      await secureStore.remove(keys.session);
      await secureStore.remove(keys.device);
    }
    await secureStore.remove("server");
    await cancelAllTransfers();
    await deviceStore.forgetEverything();
    forgetAnswers();
    settledViewer.current = null;
    transportRef.current?.attach(null);
    setStatus(null);
    setAsking(null);
    setPhase("connect");
  }

  // "Who's reading?" sits outside the library's host, so its avatars need
  // their own way in: fetched with this device's tokens, as every image here
  // is, and kept so they still show offline.
  const pickerImages = useMemo(() => (client ? createImageSource(client, "shared", {
    localUri: (path) => deviceStore.uri(path), exists: (path) => deviceStore.exists(path),
  }) : null), [client]);
  const pickerHost = useMemo(() => ({ api: apiRequest, pageSource: pickerImages }), [pickerImages]);

  if (phase === "starting") return <div className="login-shell"><LoadingIndicator size="lg" label="Starting" /></div>;
  if (phase === "connect") {
    return <ConnectScreen error={error} onConnected={async (origin) => {
      await secureStore.set("server", origin);
      setError("");
      await enter(origin);
    }} />;
  }
  if (phase === "picker") {
    return <Host.Provider value={pickerHost}><PickerScreen client={client} current={status?.viewer?.id ?? null} ask={asking} online={online}
      onClose={status?.viewer ? () => { setAsking(null); setPhase("ready"); } : null}
      onSwitched={async () => settle(client, await client.api("/api/v1/auth/status"))}
      onSwitchedOffline={async (answer) => {
        await deviceStore.saveSession(answer);
        setAsking(null);
        await settle(client, answer, { kept: true });
      }} onForget={forgetServer} /></Host.Provider>;
  }
  if (phase === "signin") {
    return <SignInScreen client={client} onSignedIn={async () => {
      await settle(client, await client.api("/api/v1/auth/status"));
      // The password sets the device up; who is reading is asked next, as on
      // the web and as Plex asks "Who's watching?" (owner, 2026-10-09).
      try {
        const { profiles = [] } = await client.api("/api/v1/profiles");
        if (profiles.length > 1) { setAsking(null); setPhase("picker"); }
      } catch { /* asked from the menu instead */ }
    }} onForget={forgetServer} />;
  }
  return <Library key={status.viewer.id} client={client} status={status} online={online} transport={transportRef.current}
    onSwitch={openPicker} onSwitchTo={switchTo} onSignOut={signOut} />;
}

// Signed in: the web app's own Comics page, drawers and reader (Phase 3b,
// the owner's call: one UI). Flipparr View is their host -- requests carry
// its token (api.js's transport, installed when the client is made), images
// come through its image source, and the library is not managed from here:
// an admin reading on an iPad sees a reader's drawers. Its own additions are
// Downloads, in the bar beside Comics, and Download in an issue's menu.
const NAV = [
  { id: "library", label: "Comics", icon: ComicsIcon },
  { id: "downloads", label: "Downloads", icon: DownloadsNavIcon },
  // Its own id: the web's "settings" carries the admin's Library health count.
  { id: "app-settings", label: "Settings", icon: SettingsNavIcon },
];

function Library({ client, status, online, transport, onSwitch, onSwitchTo, onSignOut }) {
  const viewer = status.viewer;
  const [toastElement, showToast] = useToast();
  // Made here, and handed to the transport before any screen asks for a
  // page, so a downloaded comic is answered from the device offline.
  const downloads = useMemo(() => {
    const made = new Downloads({ client, profileId: viewer.id });
    transport?.useDownloads(made);
    return made;
  }, [client, viewer.id, transport]);
  // Every kept comic put to the server (Phase 5): one this profile may no
  // longer read goes from the device, and says so.
  function checkDownloads() {
    downloads.check().then((removed) => {
      if (removed) showToast(`${removed} download${removed === 1 ? " was" : "s were"} removed: ${removed === 1 ? "it is" : "they are"} no longer available to this profile.`);
    }, () => {});
  }
  useEffect(() => {
    // Back in the foreground: what finished while the iPad was locked is
    // told to the server, and every kept comic put to it.
    const onVisible = () => {
      if (document.visibilityState !== "visible" || !transport?.online) return;
      downloads.flushRemovals().catch(() => {});
      checkDownloads();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  // checkDownloads reads only these.
  }, [downloads, transport]);
  useEffect(() => {
    // Removals that waited for the server, from the last time it was away.
    if (online) { downloads.flushRemovals().catch(() => {}); checkDownloads(); }
    return () => downloads.stop();
  // Once per profile's engine; reconnecting flushes below.
  }, [downloads]);
  const downloadVersion = useDownloadVersion(downloads);
  const pageSource = useMemo(() => createImageSource(client, viewer.id, {
    local: (url) => downloads.localPage(url),
    localUri: (path) => deviceStore.uri(path),
    exists: (path) => deviceStore.exists(path),
  }), [client, viewer.id, downloads]);

  // The server's catalog; what is shown is `catalog`, below (readerCatalog).
  const [rawCatalog, setCatalog] = useState(() => lastAnswer("/api/v1/catalog"));
  const [backendStatus, setBackendStatus] = useState(() => (lastAnswer("/api/v1/catalog") ? "live" : "loading"));
  const loadCatalog = useCallback(async () => {
    try {
      const data = await apiRequest("/api/v1/catalog");
      setCatalog(data);
      setBackendStatus("live");
      return data;
    } catch (error) {
      // A 401 is the shell's to answer (sign in again, or "Who's reading?");
      // unreachable is the offline banner's to say.
      if (error.status !== 401 && !error.network) showToast(error.message || "Flipparr could not be reached", "error");
      return null;
    }
  }, [showToast]);
  useEffect(() => { loadCatalog(); }, [loadCatalog]);

  const [page, setPage] = useState("library");
  const [libraryTab, setLibraryTab] = useState("recommended");
  const [selectedSeries, setSelectedSeries] = useState(null);
  const [seriesParentCollection, setSeriesParentCollection] = useState(null);
  const [selectedList, setSelectedList] = useState(null);
  const [runCollectionId, setRunCollectionId] = useState(null);
  const [readFileId, setReadFileId] = useState("");
  const [readFrom, setReadFrom] = useState(null);
  const [readingList, setReadingList] = useState(null);
  const [finishedFileId, setFinishedFileId] = useState("");
  // This iPad's choices (Settings -> Reading), read once and saved as changed.
  const [appSettings, setAppSettings] = useState({ downloadOpened: true, downloadNext: false });
  useEffect(() => { deviceStore.appSettings().then(setAppSettings).catch(() => {}); }, []);
  const changeAppSettings = useCallback((change) => {
    setAppSettings((current) => ({ ...current, ...change }));
    deviceStore.saveAppSettings(change).catch(() => {});
  }, []);

  const actions = useReadingActions({
    notify: showToast, viewerId: viewer.id,
    afterRating: async () => {
      const data = await loadCatalog();
      const refreshed = readerCatalog(data)?.series?.find((item) => item.id === selectedSeries?.id);
      if (refreshed) setSelectedSeries(refreshed);
    },
  });
  const { readingVersion, noteReadingChanged } = actions;
  useEffect(() => { downloads.reading = readFileId || null; }, [downloads, readFileId]);
  useEffect(() => { if (!readFileId) noteReadingChanged(); }, [readFileId, noteReadingChanged]);
  // Back online: the library as it is now, and every place re-read.
  const wasOnline = useRef(online);
  useEffect(() => {
    if (online && !wasOnline.current) { loadCatalog(); noteReadingChanged(); downloads.flushRemovals().catch(() => {}); checkDownloads(); }
    wasOnline.current = online;
  }, [online, loadCatalog, noteReadingChanged, downloads]);
  const readyRows = downloads.rows.filter((row) => row.state === "ready");
  // A reader's library: only issues there are to read, and offline only
  // those on this device (the owner, 2026-10-10; readerCatalog).
  const onDeviceKey = downloads.rows.filter((row) => row.state === "ready" || row.state === "stale").map((row) => row.fileId).join(",");
  // Each issue's file, from the whole catalog: an arc lists issues, and
  // offline is readable when one of their files is here.
  const issueFiles = useMemo(() => new Map((rawCatalog?.series || []).flatMap((run) => (run.issues || [])
    .filter((issue) => issue.fileId).map((issue) => [String(issue.id), String(issue.fileId)]))), [rawCatalog]);
  const catalog = useMemo(() => readerCatalog(rawCatalog, { onDevice: online ? null : new Set(onDeviceKey ? onDeviceKey.split(",") : []) }),
    [rawCatalog, online, onDeviceKey]);
  // Refresh: pull down at the top (the web's own gesture), or the button
  // where an admin's Scan sits on the web. Offline it first asks whether the
  // server is back; then the library, every place and the story arcs are
  // fetched again, and anything waiting is sent.
  const [refreshing, setRefreshing] = useState(false);
  const [listsVersion, setListsVersion] = useState(0);
  const refreshLibrary = useCallback(async () => {
    setRefreshing(true);
    try {
      if (transport && !transport.online) await transport.probe();
      await loadCatalog();
      noteReadingChanged();
      setListsVersion((version) => version + 1);
      await transport?.replay();
    } finally {
      setRefreshing(false);
    }
  }, [transport, loadCatalog, noteReadingChanged]);
  const pullPill = usePullToRefresh({ enabled: true, onRefresh: refreshLibrary });
  // Offline, said under each page's header (the web's backend-banner).
  const offlineNotice = online ? null : <div className="backend-banner" role="status"><WifiSlash size={19} weight="fill" aria-hidden="true" />
    {`You’re offline — ${readyRows.length ? `${readyRows.length} downloaded ${readyRows.length === 1 ? "issue is" : "issues are"} ready.` : "nothing is downloaded on this device."}`}</div>;

  // As the web app's readComic: opening a comic puts the drawers away.
  async function readComic(target) {
    const open = (fileId) => {
      setReadFrom(target?.fromStart ? 0 : null);
      setReadFileId(String(fileId));
      setReadingList(target?.listId ? { id: String(target.listId), name: target.listName || "" } : null);
      setSelectedSeries(null);
      setSelectedList(null);
      setRunCollectionId(null);
      setFinishedFileId("");
      // A comic opened is kept on the device too, downloading behind the
      // reader as Kindle and Apple Books do (the owner, 2026-10-09), so it
      // is there to finish offline. It counts against the limit like any
      // download; one that cannot fit is still read online, and says so.
      const row = downloads.row(fileId);
      if (online && appSettings.downloadOpened && (!row || row.state === "failed")) {
        downloads.reading = String(fileId);
        (row ? downloads.retry(fileId) : downloads.download(fileId, { groupKey: "opened" }))
          .catch((error) => showToast(`Reading online: not kept on this device. ${error.message || ""}`.trim()));
      }
      // And the issue after it, when this iPad is set to (off unless chosen).
      const next = online && appSettings.downloadNext ? readingContext(catalog, fileId).next : null;
      if (next?.fileId && !downloads.row(next.fileId)) downloads.download(next.fileId, { groupKey: "opened" }).catch(() => {});
    };
    // Offline, only what is on the device can be opened.
    const openable = (fileId) => {
      if (online || ["ready", "stale"].includes(downloads.row(fileId)?.state)) { open(fileId); return; }
      showToast("This issue isn't downloaded. Connect to read it.", "error");
    };
    const known = typeof target === "string" ? target : target?.id;
    if (known) { openable(known); return; }
    if (!target?.runId && !target?.listId) return;
    try {
      const fileId = await readableFile(target);
      if (fileId) openable(fileId);
      else showToast("There is nothing here that can be read yet.", "error");
    } catch (error) {
      showToast(error.network ? "This issue isn't downloaded. Connect to read it." : error.message, "error");
    }
  }
  function openSeries(series) {
    setSeriesParentCollection(null);
    setSelectedList(null);
    setRunCollectionId(null);
    setSelectedSeries(series);
  }
  function openList(list) {
    setSelectedSeries(null);
    setSeriesParentCollection(null);
    setRunCollectionId(null);
    setSelectedList(list);
  }

  // Downloading needs Flipparr; offline, the controls still show what is
  // here, and a press says why nothing starts.
  const [downloadGroup, setDownloadGroup] = useState(null);
  const issueName = (issue) => `${issue.contextLabel || "Issue"} #${issue.number}`;
  const downloadIssue = useCallback((issue) => {
    if (!online) { showToast("Connect to Flipparr to download.", "error"); return; }
    downloads.download(issue.fileId).then(() => showToast(`Downloading ${issueName(issue)}`),
      (error) => showToast(error.message || "Could not download", "error"));
  }, [online, downloads, showToast]);

  // Download and Remove download, in an issue's menu; its badge while it
  // downloads, or once it is here, where the tile has nothing else to say;
  // and Phase 6's controls seen without a menu: one on every issue, and
  // Download for a whole run, arc or collection beside its bookmark.
  const host = useMemo(() => ({
    api: apiRequest,
    // Panel fixes as on the web (the admin's), and only while Flipparr
    // answers: a fix saves to the server as it is made (the owner,
    // 2026-10-09; fixes made offline and sent later are a later step).
    canEditPanels: online && viewer.role === "admin",
    canManage: false,
    // A reader, not an acquisition tool: no owned counts, no missing issues.
    readerOnly: true,
    readable: ({ kind, id, fileId, issueIds }) => {
      if (online) return true;
      if (fileId) return ["ready", "stale"].includes(downloads.row(fileId)?.state);
      // A run, collection or arc offline: whether the shown catalog keeps it
      // (runs, collections), or any of its comics is here (arcs: groupKey).
      if (kind === "run") return catalog?.series?.some((run) => String(run.id) === String(id));
      if (kind === "collection") return catalog?.runCollections?.some((item) => String(item.id) === String(id));
      // An arc: any of its issues here, else anything downloaded for it.
      const here = (fileId) => ["ready", "stale"].includes(downloads.row(fileId)?.state);
      if (kind === "arc" && issueIds?.length) return issueIds.some((issueId) => issueFiles.get(String(issueId)) && here(issueFiles.get(String(issueId))));
      return downloads.rows.some((row) => row.groupKey === `${kind}:${id}` && here(row.fileId));
    },
    pageSource,
    issueExtras: (issue) => {
      if (!issue.fileId) return [];
      const row = downloads.row(issue.fileId);
      const label = `${issue.contextLabel || "Issue"} #${issue.number}`;
      if (row?.state === "ready" || row?.state === "stale" || row?.state === "downloading") {
        return [{ key: "offline-remove", label: row.state === "downloading" ? "Cancel download" : "Remove download", icon: <Trash size={16} />,
          onSelect: () => downloads.remove(issue.fileId).then(() => showToast(`${label} removed from this device`)) }];
      }
      if (row?.state === "failed") {
        return [{ key: "offline-retry", label: "Try the download again", icon: <ArrowClockwise size={16} />, onSelect: () => downloads.retry(issue.fileId) }];
      }
      return [{ key: "offline", label: "Download", icon: <DownloadSimple size={16} />, onSelect: () => downloadIssue(issue) }];
    },
    issueDownload: (issue) => {
      const row = downloads.row(issue.fileId);
      const label = issueName(issue);
      if (row?.state === "downloading") {
        return { busy: true, label: `Downloading ${label}, ${Math.round(progressOf(row) * 100)}%`, onPress: () => {} };
      }
      if (row?.state === "ready" || row?.state === "stale") {
        return { done: true, label: `${label} is on this device`,
          onPress: () => showToast(`${label} is on this device. Remove it from its \u2026 menu or from Downloads.`) };
      }
      if (row?.state === "failed") return { label: `Try downloading ${label} again`, onPress: () => downloads.retry(issue.fileId) };
      return { label: `Download ${label}`, onPress: () => downloadIssue(issue) };
    },
    coverStatus: (target) => coverStatusFor(downloads.rows, target, { issueFiles }),
    // A card's long-press menu (Read is the card's own): Download, the
    // reading list, and -- destructive, last -- Remove downloads.
    cardMenu: (target) => {
      const items = [];
      const listKind = target.kind === "run" ? "run" : target.kind === "arc" ? "arc" : "collection";
      const listed = isOnReadingList(actions.readingList, listKind, target.id);
      if (online) {
        items.push({ key: "download", label: "Download\u2026", icon: <DownloadSimple size={16} />,
          onSelect: () => setDownloadGroup({ kind: target.kind, id: target.id, title: target.title, runIds: target.runIds }) });
      }
      items.push({ key: "list", label: listed ? "Remove from reading list" : "Add to reading list", icon: <ReadingListIcon size={16} />,
        onSelect: () => actions.toggleReadingList(listKind, target.id, !listed) });
      const here = rowsFor(downloads.rows, target, { issueFiles }).filter((row) => row.state !== "failed");
      if (here.length) {
        items.push({ key: "remove", label: `Remove ${here.length} ${here.length === 1 ? "download" : "downloads"}`, icon: <Trash size={16} />,
          onSelect: async () => {
            for (const row of here) await downloads.remove(row.fileId);
            showToast(`Removed ${here.length} from this device`);
          } });
      }
      return items;
    },
    groupDownload: (group) => {
      const mine = downloads.rows.filter((row) => row.groupKey === `${group.kind}:${group.id}`);
      const busy = mine.some((row) => row.state === "downloading");
      const done = !busy && mine.some((row) => row.state === "ready");
      return {
        busy, done,
        label: busy ? `Downloading from ${group.title}` : done ? `Issues of ${group.title} are on this device; download more` : `Download ${group.title}`,
        onPress: () => (online ? setDownloadGroup(group) : showToast("Connect to Flipparr to download.", "error")),
      };
    },
    notice: offlineNotice,
    issueBadge: (issue) => {
      const row = issue.fileId ? downloads.row(issue.fileId) : null;
      if (row?.state === "downloading") return { label: `Downloading ${Math.round(progressOf(row) * 100)}%`, tone: "reading-progress" };
      return null;
    },
    // Downloaded is said by the issue's own control now (Phase 6), so the
    // mark beside its number is not drawn twice.
    // Downloaded, on Recommended: first while offline, when it is what can
    // be read; after Keep Reading otherwise.
    shelves: () => (readyRows.length ? [{
      id: "downloaded", title: "Downloaded", first: !online,
      items: readyRows.map((row) => ({
        key: `downloaded-${row.fileId}`, fileId: row.fileId, resume: true,
        title: `${row.seriesTitle}${row.issueNumber ? ` #${row.issueNumber}` : ""}`,
        note: row.finished ? "Read" : row.lastReadAt ? "In progress" : "Unread",
        cover: row.pageUrls?.[0],
      })),
    }] : []),
  // downloadVersion: the menu and badge follow the downloads as they change.
  }), [pageSource, downloads, downloadVersion, showToast, online, viewer.role, downloadIssue, catalog, issueFiles, actions.readingList, actions.toggleReadingList]);

  const header = useMemo(() => ({
    profile: {
      household: Boolean(status.household), method: status.method, version: 0,
      // Your own page is the web app's; here your tile just closes the menu.
      // Sign out always works, and always shows (see ReaderApp's signOut).
      onOpenProfile: () => {}, onSwitchTo, onPicker: onSwitch, onSignOut, signOutAlways: true,
    },
  }), [status.household, status.method, onSwitch, onSwitchTo, onSignOut]);

  const series = catalog?.series || [];
  const runCollections = useMemo(() => collectionCards(catalog?.runCollections, catalog?.series || []), [catalog?.runCollections, catalog?.series]);
  const runCollectionCard = runCollectionId ? runCollections.find((item) => String(item.id) === String(runCollectionId)) || null : null;
  const logicalSeriesCount = useMemo(() => logicalCatalogSeries(catalog, series).length, [catalog]);
  const { series: readingSeries, title: readingTitle, issue: readingIssue, next: readingNext } = readingContext(catalog, readFileId);
  const finished = Boolean(readFileId) && finishedFileId === readFileId;
  const reading = {
    readingList: actions.readingList || EMPTY_ENTRIES, onToggleReadingList: actions.toggleReadingList,
    readingVersion, onRead: readComic, onOpenSeries: openSeries,
  };

  return <Host.Provider value={host}><ViewerContext.Provider value={viewer}><CollectedEditionsContext.Provider value={Boolean(catalog?.collectedEditionsEnabled)}><HeaderContext.Provider value={header}>
    <div className="app-shell reader-app">
      <Nav items={NAV} active={page} onNavigate={(next) => { setPage(next); window.scrollTo({ top: 0 }); }} catalog={catalog} backendStatus={backendStatus}
        logicalSeriesCount={logicalSeriesCount} authStatus={status} onSignOut={onSignOut} scanning={false}
        onRefresh={refreshLibrary} refreshing={refreshing} />
      <main className="main-content"><div className="page-view" key={page}>
        {page === "library" ? <LibraryView readingList={actions.readingList} onToggleReadingList={actions.toggleReadingList}
          onOpenRunCollection={(card) => { setSelectedSeries(null); setSelectedList(null); setRunCollectionId(String(card.id)); }}
          tab={libraryTab} onTab={(next) => { setLibraryTab(next); window.scrollTo({ top: 0 }); }} onNavigate={setPage}
          onOpenSeries={openSeries} onOpenCollection={() => {}} onOpenList={openList} onRead={readComic}
          readingVersion={readingVersion} listsVersion={listsVersion} catalog={catalog} backendStatus={backendStatus} /> : null}
        {page === "downloads" ? <DownloadsPage downloads={downloads} onRead={readComic} notice={offlineNotice}
          onOpenSettings={() => { setPage("app-settings"); window.scrollTo({ top: 0 }); }} /> : null}
        {page === "app-settings" ? <AppSettingsPage client={client} status={status} viewer={viewer} downloads={downloads} online={online}
          settings={appSettings} onSettings={changeAppSettings} onSwitch={onSwitch} onSignOut={onSignOut} notice={offlineNotice} /> : null}
      </div></main>
      {readFileId ? <ReaderView fileId={readFileId} title={readingTitle} medium={readingSeries?.medium} directionOverride={readingSeries?.readingDirection}
        startPage={readFrom} behind={Boolean(selectedSeries) || Boolean(selectedList) || finished}
        onFinish={() => setFinishedFileId(readFileId)} onProgressSaved={noteReadingChanged}
        onOpenRun={readingSeries ? () => openSeries(readingSeries) : undefined}
        onClose={() => { setReadFileId(""); setReadFrom(null); setFinishedFileId(""); setReadingList(null); }} /> : null}
      {finished ? <FinishDrawer key={readFileId} fileId={readFileId} series={readingSeries} issue={readingIssue} nextIssue={readingNext}
        medium={readingSeries?.medium} title={readingTitle} readingVersion={readingVersion} list={readingList}
        onRateIssue={actions.rateIssue} onRead={readComic} onOpenSeries={openSeries} onClose={() => setFinishedFileId("")} /> : null}
      {runCollectionCard ? <RunCollectionDrawer key={runCollectionCard.id} card={runCollectionCard} {...reading}
        onClose={() => setRunCollectionId(null)} onMarkRuns={actions.markRunsRead} /> : null}
      {selectedSeries ? <SeriesDrawer key={selectedSeries.id} series={selectedSeries} families={catalog?.families || []} allSeries={series}
        {...reading} runCollections={runCollections}
        onOpenRunCollection={(card) => { setSelectedSeries(null); setRunCollectionId(String(card.id)); }}
        parentCollection={seriesParentCollection} onBack={() => setSelectedSeries(null)}
        onClose={() => { setSelectedSeries(null); setSeriesParentCollection(null); }}
        onMarkIssue={actions.markIssueRead} onMarkRun={actions.markRunRead} onRate={actions.rateSeries} onRateIssue={actions.rateIssue} /> : null}
      {selectedList ? <ReadingListDrawer key={selectedList.id} list={selectedList} allSeries={series} {...reading} groupTools={false}
        onClose={() => setSelectedList(null)} onMarkIssue={actions.markIssueRead} onRateIssue={actions.rateIssue}
        onMark={actions.markListRead} onGone={(message) => { setSelectedList(null); if (message) showToast(message); }} /> : null}
      {downloadGroup ? <DownloadSheet group={downloadGroup} downloads={downloads} onClose={() => setDownloadGroup(null)}
        onStarted={(message, tone) => { setDownloadGroup(null); showToast(message, tone); }} /> : null}
      {pullPill}
      {toastElement}
    </div>
  </HeaderContext.Provider></CollectedEditionsContext.Provider></ViewerContext.Provider></Host.Provider>;
}

/** A counter that moves whenever the downloads change, for what shows them. */
function useDownloadVersion(downloads) {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const changed = () => setVersion((value) => value + 1);
    downloads.addEventListener("change", changed);
    return () => downloads.removeEventListener("change", changed);
  }, [downloads]);
  return version;
}

function ConnectScreen({ error: startError, onConnected }) {
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(startError);
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const { origin } = await findServer(address);
      await onConnected(origin);
    } catch (err) {
      setError(err.message || "Could not connect");
    } finally {
      setBusy(false);
    }
  }
  return <div className="login-shell"><form className="login-card" onSubmit={submit}>
    <div className="login-brand"><FlipparrMark size={72} /></div>
    <label>
      <span>Your Flipparr's address</span>
      <input name="server" value={address} autoFocus inputMode="url" autoComplete="url"
        autoCapitalize="none" autoCorrect="off" spellCheck={false} placeholder="flipparr.example.com"
        onChange={(event) => setAddress(event.target.value)} />
    </label>
    {error ? <p className="login-error" role="alert">{error}</p> : null}
    <button className="primary-button login-submit" disabled={busy || !address.trim()} aria-busy={busy}>
      {busy ? <LoadingIndicator size="sm" /> : null} {busy ? "Connecting…" : "Connect"}
    </button>
  </form></div>;
}

function SignInScreen({ client, onSignedIn, onForget }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await client.api("/api/v1/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }),
      });
      await onSignedIn();
    } catch (err) {
      setError(err.message || "Could not sign in");
    } finally {
      setBusy(false);
    }
  }
  return <div className="login-shell"><form className="login-card" onSubmit={submit}>
    <div className="login-brand"><FlipparrMark size={72} /></div>
    <label>
      <span>Username</span>
      <input name="username" value={username} autoFocus autoComplete="username"
        autoCapitalize="none" autoCorrect="off" spellCheck={false} onChange={(event) => setUsername(event.target.value)} />
    </label>
    <label>
      <span>Password</span>
      <PasswordInput name="password" value={password} autoComplete="current-password"
        onChange={(event) => setPassword(event.target.value)} />
    </label>
    {error ? <p className="login-error" role="alert">{error}</p> : null}
    <button className="primary-button login-submit" disabled={busy || !username || !password} aria-busy={busy}>
      {busy ? <LoadingIndicator size="sm" /> : <ShieldCheck size={18} />} {busy ? "Signing in…" : "Sign in"}
    </button>
    <button type="button" className="ghost-button" onClick={onForget}>Use another server</button>
  </form></div>;
}

// "Who's reading?" on a shared iPad: the web app's tiles and PIN pad
// (components/ProfileTiles.jsx). A switch is answered with the new profile's
// session in the Flipparr-Session header, which the client keeps. With no
// connection (Phase 5), the profiles are the ones kept on the device, and a
// profile used on this iPad before opens with its own kept session -- its
// PIN checked against the hash kept after the server last accepted it
// (offline-pin.js); a password-locked profile waits for Flipparr.
function PickerScreen({ client, current, online = true, ask = null, onClose, onSwitched, onSwitchedOffline, onForget }) {
  const [profiles, setProfiles] = useState(null);
  const [offline, setOffline] = useState(false);
  const [asking, setAsking] = useState(null);
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const keys = tokenKeys(client.origin);
  useEffect(() => {
    let live = true;
    (async () => {
      const kept = await deviceStore.profiles();
      // Known to be offline: the kept list at once, not after the network gives up.
      if (!online && kept.length) { if (live) { setOffline(true); setProfiles(kept); } return; }
      try {
        const data = await client.api("/api/v1/profiles");
        const merged = mergeProfiles(kept, data.profiles || []);
        deviceStore.saveProfiles(merged).catch(() => {});
        if (live) setProfiles(merged);
      } catch (err) {
        if (!live) return;
        if (!err.network || !kept.length) { setError(err.message); return; }
        setOffline(true);
        setProfiles(kept);
      }
    })();
    return () => { live = false; };
  }, [client]);
  // A profile asked for from the header's menu, once the list says how.
  const asked = useRef(false);
  useEffect(() => {
    if (!ask || asked.current || profiles === null) return;
    asked.current = true;
    choose(profiles.find((profile) => String(profile.id) === String(ask.id)) || ask);
  // Once, when the list has loaded.
  }, [profiles]);
  async function enter(profile, proof = {}) {
    setBusy(true);
    setError("");
    try {
      await client.api("/api/v1/profiles/switch", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ userId: profile.id, ...proof }),
      });
      // The server took the PIN: kept hashed, so it can be checked offline.
      if (proof.pin) {
        try {
          const pinHash = await hashPin(proof.pin, await pinSalt(client.origin));
          await deviceStore.saveProfiles(withPinHash(await deviceStore.profiles(), profile.id, pinHash));
        } catch { /* offline switching to it waits for another PIN switch */ }
      }
      await onSwitched();
      return true;
    } catch (err) {
      setError(err.message);
      setBusy(false);
      return false;
    }
  }
  async function enterOffline(profile, pin = null) {
    setBusy(true);
    setError("");
    const token = await secureStore.get(keys.profileSession(profile.id));
    const answer = await deviceStore.profileStatus(profile.id);
    if (pin !== null && !(await verifyPin(pin, await pinSalt(client.origin), profile.pinHash))) {
      setError("That PIN isn\u2019t right");
      setBusy(false);
      return false;
    }
    await client.useSession(token);
    await onSwitchedOffline(answer);
    return true;
  }
  async function choose(profile) {
    if (String(profile.id) === String(current)) { onClose?.(); return; }
    setError("");
    if (offline) {
      const signedIn = Boolean(await secureStore.get(keys.profileSession(profile.id))) && Boolean((await deviceStore.profileStatus(profile.id))?.viewer);
      const choice = offlineChoice(profile, { signedIn });
      if (choice.refused) { setAsking(null); setError(choice.refused); return; }
      if (choice.ask) { setAsking(profile); return; }
      enterOffline(profile);
      return;
    }
    if (isLocked(profile)) { setAsking(profile); setSecret(""); } else enter(profile);
  }
  const back = () => { setAsking(null); setError(""); };
  return <div className="login-shell profile-picker">
    {onClose ? <button type="button" className="glass-button glass-button--icon profile-picker-close" onClick={onClose} aria-label="Close"><X size={20} /></button> : null}
    <div className="profile-picker-card">
      <FlipparrMark size={48} />
      <h1>{asking ? asking.name : "Who\u2019s reading?"}</h1>
      {asking?.lock === "pin" ? <div className="profile-picker-secret">
        <ProfileAvatar profile={asking} size="lg" />
        <PinPad length={asking.pinLength} busy={busy} error={error} onCancel={back} onInput={() => setError("")}
          onSubmit={(pin) => (offline ? enterOffline(asking, pin) : enter(asking, { pin }))} />
      </div> : asking ? <form className="profile-picker-secret" onSubmit={(event) => { event.preventDefault(); enter(asking, { password: secret }); }}>
        <ProfileAvatar profile={asking} size="lg" />
        <label className="form-field"><span>Password</span>
          <PasswordInput autoFocus value={secret} name="password" autoComplete="current-password" onChange={(event) => setSecret(event.target.value)} />
        </label>
        {error ? <p className="login-error" role="alert">{error}</p> : null}
        <div className="profile-picker-actions">
          <button type="button" className="secondary-button" onClick={back}>Back</button>
          <button className="primary-button" disabled={busy || !secret} aria-busy={busy}>{busy ? <LoadingIndicator size="sm" /> : null} Continue</button>
        </div>
      </form> : <>
        {offline ? <p className="settings-card-lead" role="status">Offline: profiles used on this iPad before can be opened.</p> : null}
        {profiles === null && !error ? <LoadingIndicator size="md" /> : null}
        <ProfileTiles profiles={profiles || []} current={current} onChoose={choose} busy={busy} />
        {error ? <p className="login-error" role="alert">{error}</p> : null}
        {onForget ? <button type="button" className="ghost-button" onClick={onForget}>Use another server</button> : null}
      </>}
    </div>
  </div>;
}

/** The download rows, re-rendered whenever the engine changes. */
function useDownloadRows(downloads) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const changed = () => setTick((tick) => tick + 1);
    downloads.addEventListener("change", changed);
    return () => downloads.removeEventListener("change", changed);
  }, [downloads]);
  return downloads.rows;
}

// What this device keeps for reading offline, in the web app's own Settings
// pattern: a page header over cards. Storage first (how much is used, and
// the limit -- the comics finished longest ago make way), then each download,
// which opens to read, with a way to take it off the device.
/**
 * The comics of a run, story arc or collection, in the order its drawer
 * shows them: `{fileId, finishedAt, readable}` for `nextUnread`.
 */
async function groupFiles(group) {
  const fromRun = (reading) => [...(reading?.issues || []), ...(reading?.volumes || [])]
    .map((file) => ({ fileId: file.id, finishedAt: file.finishedAt, readable: file.readable }));
  if (group.kind === "run") return fromRun(await apiRequest(`/api/v1/series/${group.id}/reading`));
  if (group.kind === "arc") {
    const arc = await apiRequest(`/api/v1/reading-lists/${group.id}`);
    return (arc?.items || []).map((item) => ({ fileId: item.fileId, finishedAt: item.finishedAt, readable: item.readable }));
  }
  const files = [];
  for (const runId of group.runIds || []) files.push(...fromRun(await apiRequest(`/api/v1/series/${runId}/reading`)));
  return files;
}

/**
 * Download for a whole run, story arc or collection (Phase 6): the next
 * unread, as many as chosen (5 for a run or collection, every one for an
 * arc), what they come to, and what would make way -- before anything
 * starts. One per press, in the drawer's order, under one groupKey.
 */
function DownloadSheet({ group, downloads, onClose, onStarted }) {
  const dialogRef = useDialog(onClose);
  const [files, setFiles] = useState(null);
  const [count, setCount] = useState(null);
  const [plan, setPlan] = useState(null);
  const [cap, setCap] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const onDevice = useMemo(() => new Set(downloads.rows
    .filter((row) => ["ready", "stale", "downloading"].includes(row.state)).map((row) => row.fileId)), [downloads]);
  const unread = useMemo(() => (files ? nextUnread(files, Infinity, { onDevice }) : []), [files, onDevice]);
  useEffect(() => {
    let live = true;
    groupFiles(group).then((found) => {
      if (!live) return;
      setFiles(found);
      const waiting = nextUnread(found, Infinity, { onDevice }).length;
      setCount(group.kind === "arc" ? waiting : Math.min(5, waiting));
    }, (failure) => { if (live) setError(failure.message || "Could not look at what to download"); });
    deviceStore.capBytes().then((bytes) => { if (live) setCap(bytes); });
    return () => { live = false; };
  // Once, for the group it was opened for.
  }, []);
  const chosen = useMemo(() => unread.slice(0, count || 0), [unread, count]);
  useEffect(() => {
    if (!chosen.length || !cap) { setPlan(null); return undefined; }
    let live = true;
    setPlan(null);
    (async () => {
      const estimates = [];
      // A few at a time: each is one small request to the server.
      for (let start = 0; start < chosen.length; start += 6) {
        const batch = await Promise.all(chosen.slice(start, start + 6).map((fileId) => apiRequest(`/api/v1/files/${fileId}/offline`)
          .then((manifest) => ({ fileId, bytes: Number(manifest.bytesEstimate) || 0 }), () => ({ fileId, bytes: 0 }))));
        estimates.push(...batch);
      }
      const rows = await deviceStore.allDownloads();
      if (live) setPlan(planDownloads(estimates, { capBytes: cap, rows }));
    })().catch((failure) => { if (live) setError(failure.message || "Could not size the download"); });
    return () => { live = false; };
  }, [chosen, cap]);
  const counts = [...new Set([1, 3, 5, 10, 20].filter((n) => n < unread.length).concat(unread.length))].filter(Boolean);
  const going = plan ? (plan.fits ? chosen.length : plan.fitting) : 0;
  async function start() {
    setBusy(true);
    const { started, error: failure } = await downloads.downloadMany(chosen.slice(0, going), { groupKey: `${group.kind}:${group.id}` });
    onStarted(failure && !started ? failure
      : `Downloading ${started} ${started === 1 ? "issue" : "issues"} from ${group.title}${failure ? `. ${failure}` : ""}`, failure && !started ? "error" : undefined);
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}>
    <section className="modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="download-sheet-title" onMouseDown={(event) => event.stopPropagation()}>
      <DialogCloseButton onClose={onClose} label="Close" />
      <span className="eyebrow">{group.kind === "run" ? "Run" : group.kind === "arc" ? "Story arc" : "Collection"}</span>
      <h2 id="download-sheet-title">Download {group.title}</h2>
      {error ? <p className="workbench-error" role="alert">{error}</p>
        : files === null ? <LoadingIndicator size="md" />
        : !unread.length ? <p className="settings-card-note">Every unread issue is on this device already.</p>
        : <>
          <p className="settings-card-note">The next unread issues, in reading order. They keep downloading while the iPad is locked.</p>
          <GlassSelect label="How many" value={count} onChange={(next) => setCount(Number(next))}
            options={counts.map((n) => ({ value: n, label: n === unread.length && n > 1 ? `All ${n} unread` : `Next ${n} unread` }))} />
          <p className="settings-card-note" role="status">{!plan ? "Working out the size\u2026"
            : plan.fits ? `${formatBytes(plan.total)}${plan.evict.length ? `. Makes room by removing ${plan.evict.length} finished ${plan.evict.length === 1 ? "comic" : "comics"}, read longest ago.` : "."}`
            : plan.fitting ? `Only ${plan.fitting} of ${chosen.length} fit under this device's ${formatBytes(cap)} limit. Raise it in Downloads to take them all.`
            : `Needs ${formatBytes(plan.shortfall)} more than this device's ${formatBytes(cap)} limit allows. Raise it in Downloads.`}</p>
        </>}
      <div className="metadata-edit-actions">
        <button type="button" className="ghost-button" onClick={onClose}>Cancel</button>
        <button type="button" className="primary-button" disabled={busy || !going} aria-busy={busy || undefined} onClick={start}>
          {busy ? <LoadingIndicator size="sm" /> : <DownloadSimple size={18} />} {going ? `Download ${going}` : "Download"}
        </button>
      </div>
    </section>
  </div>;
}

/**
 * Settings, Flipparr View's own section (Phase 7; the owner, 2026-10-09):
 * Reading, Storage, Account and About, from the web's Settings pieces
 * (SettingsCard, Toggle, GlassSelect, the shared About).
 */
/* global __VIEW_BUILD__ */
function AppSettingsPage({ client, status, viewer, downloads, online, settings, onSettings, onSwitch, onSignOut, notice = null }) {
  const [server, setServer] = useState(null);
  useEffect(() => {
    let live = true;
    apiRequest("/api/v1/app").then((answer) => { if (live) setServer(answer); }, () => {});
    return () => { live = false; };
  }, []);
  return <>
    <PageHeader title="Settings" />
    <div className="settings-pane">
      {notice}
      <SettingsCard title="Reading">
        <Toggle checked={settings.downloadOpened} onChange={(on) => onSettings({ downloadOpened: on })} title="Download comics you open"
          description="A comic you start reading is kept on this iPad as you read, so you can finish it offline. It counts against the download limit below." />
        <Toggle checked={settings.downloadNext} onChange={(on) => onSettings({ downloadNext: on })} title="Download the next issue too"
          description="Opening a comic also keeps the issue after it, ready when you finish." />
        <p className="settings-card-note">Panel view, the dimming around a panel and the rest are in the reader&rsquo;s own settings, while you read. They follow your profile to every device.</p>
      </SettingsCard>
      <StorageSettings downloads={downloads} viewer={viewer} />
      <SettingsCard title="Account">
        <p className="settings-card-lead">Reading as <strong>{viewer.name}</strong> from <strong>{client.origin.replace(/^https?:\/\//, "")}</strong>{online ? "." : ", which can\u2019t be reached right now."}</p>
        <div className="settings-card-actions">
          {status.household ? <button type="button" className="secondary-button" onClick={onSwitch}>Switch profile</button> : null}
          <button type="button" className="secondary-button" onClick={onSignOut}>Sign out</button>
        </div>
      </SettingsCard>
      <AboutSettings base={client.origin} privacy={APP_PRIVACY} buildLabel="Server build">
        <SettingsCard title="Flipparr View">
          {/* Two builds are in play, so each says whose it is: the app's
              here, the server's in the Flipparr card below. */}
          <p className="settings-card-lead">App build {typeof __VIEW_BUILD__ === "undefined" ? "dev" : __VIEW_BUILD__}.{server ? ` Server: Flipparr ${server.version}, build ${server.build}.` : ""}</p>
        </SettingsCard>
      </AboutSettings>
    </div>
  </>;
}

// What Flipparr View keeps: the server's own account, with the browser's
// section swapped for this device's (docs/PRIVACY.md, "On the device").
const APP_PRIVACY = [
  ...PRIVACY_SECTIONS.filter((section) => section.title !== "In your browser"),
  { title: "On this iPad", points: [
    "Comics you download, at reading size, with their panels, your places and changes waiting to be sent. Kept out of iCloud backups.",
    "The profiles you have used here, and a check of each PIN made on this iPad, so a profile can be opened offline. Never a password.",
    "Sign-ins in the iOS Keychain. Signing out, or using another server, removes what this iPad kept for that server.",
    "No analytics and no tracking. Flipparr View talks only to the Flipparr you connect it to.",
  ] },
];

/** The download limit, what it holds, and the page cache (Settings -> Storage). */
function StorageSettings({ downloads, viewer }) {
  const rows = useDownloadRows(downloads);
  const [cap, setCap] = useState(null);
  const [cache, setCache] = useState(null);
  const folder = `online/u${viewer.id}`;
  useEffect(() => {
    deviceStore.capBytes().then(setCap);
    // A limit this large removes nothing: it only measures.
    trimNative({ path: folder, maxBytes: Number.MAX_SAFE_INTEGER }).then(({ bytes }) => setCache(bytes), () => setCache(0));
  }, [folder]);
  const used = usageBytes(rows);
  return <SettingsCard title="Storage">
    <p className="settings-card-lead">Downloads use {formatBytes(used)} of {cap ? formatBytes(cap) : "\u2026"}. When a download would go over the limit, the comics you finished longest ago make way; one you are still reading never does. A new limit applies from the next download.</p>
    {cap ? <GlassSelect label="Keep up to" value={cap} onChange={async (next) => { await deviceStore.setCapBytes(Number(next)); setCap(Number(next)); }}
      options={CAP_CHOICES.map((choice) => ({ value: choice, label: `Keep up to ${formatBytes(choice)}` }))} /> : null}
    <p className="settings-card-note">Pages seen while reading online are kept too, up to {formatBytes(ONLINE_MAX_BYTES)}, so a page opens at once the second time: {cache === null ? "\u2026" : formatBytes(cache)} now.</p>
    <div className="settings-card-actions">
      <button type="button" className="secondary-button" disabled={!cache} onClick={() => trimNative({ path: folder, maxBytes: 0 }).then(() => setCache(0), () => {})}>
        <Trash size={16} /> Clear pages seen online
      </button>
    </div>
  </SettingsCard>;
}

function DownloadsPage({ downloads, onRead, notice = null, onOpenSettings }) {
  const rows = useDownloadRows(downloads);
  const [cap, setCap] = useState(null);
  useEffect(() => { deviceStore.capBytes().then(setCap); }, []);
  const used = usageBytes(rows);
  const finished = rows.filter((row) => row.state === "ready" && row.finished);
  return <>
    <PageHeader title="Downloads" />
    <div className="settings-pane">
      {notice}
      <SettingsCard title="On this device">
        <p className="settings-card-lead">{formatBytes(used)} of {cap ? formatBytes(cap) : "…"} used. When a download would go over the limit, the comics you finished longest ago make way; one you are still reading never does.</p>
        <div className="settings-card-actions">
          <button type="button" className="secondary-button" onClick={onOpenSettings}>Change the limit in Settings</button>
        </div>
        {finished.length ? <button type="button" className="secondary-button" onClick={async () => {
          if (!window.confirm(`Remove ${finished.length} finished ${finished.length === 1 ? "comic" : "comics"} from this device? Comics still being read stay.`)) return;
          await downloads.removeFinished();
        }}><Trash size={16} /> Remove finished downloads ({finished.length})</button> : null}
      </SettingsCard>
      <SettingsCard title="Downloaded comics">
        {rows.length ? <div className="profile-rows">
          {rows.map((row) => {
            const name = `${row.seriesTitle}${row.issueNumber ? ` #${row.issueNumber}` : ""}`;
            return <div className="profile-row" key={row.fileId}>
              <span><strong>{name}</strong><small role={row.state === "failed" ? "alert" : undefined}>
                {row.state === "ready" ? formatBytes(row.bytes)
                  : row.state === "stale" ? "Replaced on the server since it was downloaded"
                  : row.state === "failed" ? row.error
                  : `Downloading ${row.pagesDone.length} of ${row.pageCount} pages`}
              </small></span>
              {row.state === "ready" || row.state === "stale" ? <button type="button" className="secondary-button" onClick={() => onRead(row.fileId)}><BookOpen size={16} /> Read</button> : null}
              {row.state === "stale" ? <button type="button" className="secondary-button" onClick={() => downloads.redownload(row.fileId).catch(() => {})}><ArrowClockwise size={16} /> Download again</button> : null}
              {row.state === "failed" ? <button type="button" className="secondary-button" onClick={() => downloads.retry(row.fileId)}><ArrowClockwise size={16} /> Try again</button> : null}
              <button type="button" className="glass-button glass-button--icon" aria-label={`Remove ${name} from this device`} onClick={() => downloads.remove(row.fileId)}><Trash size={18} /></button>
            </div>;
          })}
        </div> : <p className="settings-card-lead">Nothing downloaded yet. In a run&rsquo;s Issues, open an issue&rsquo;s &hellip; menu and choose Download.</p>}
      </SettingsCard>
    </div>
  </>;
}
