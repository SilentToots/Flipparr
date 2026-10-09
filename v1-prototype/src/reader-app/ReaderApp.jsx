// Flipparr View: the app around the web app's own screens -- the Comics page,
// its drawers and the reader (Phase 3b: one UI). Find the server, sign in,
// see what to read, read it, keep some on the device. Search, requests and
// everything an admin manages stay in the web app.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowClockwise, BookOpen, DownloadSimple, ShieldCheck, Trash, X } from "@phosphor-icons/react";
import { FlipparrMark } from "../brand.jsx";
import { LoadingIndicator } from "../components/LoadingIndicator";
import { SettingsCard } from "../components/SettingsControls.jsx";
import { GlassSelect } from "../components/glass.jsx";
import { useToast } from "../components/Toast.jsx";
import { PinPad, ProfileAvatar, ProfileTiles } from "../components/ProfileTiles.jsx";
import { ComicsIcon, DownloadsNavIcon } from "../design-icons.jsx";
import { FinishDrawer } from "../drawers/FinishDrawer.jsx";
import { ReadingListDrawer } from "../drawers/ReadingListDrawer.jsx";
import { RunCollectionDrawer } from "../drawers/RunCollectionDrawer.jsx";
import { SeriesDrawer } from "../drawers/SeriesDrawer.jsx";
import { CollectedEditionsContext } from "../editions.js";
import { Nav } from "../frame/Nav.jsx";
import { HeaderContext, PageHeader } from "../frame/header.jsx";
import { Host } from "../host.js";
import { apiRequest, forgetAnswers, lastAnswer, setApiTransport } from "../api.js";
import { readableFile, readingContext, useReadingActions } from "../library/actions.js";
import { LibraryView, logicalCatalogSeries } from "../library/LibraryView.jsx";
import { isLocked, profileStorage, setStorageProfile } from "../profiles.js";
import { EMPTY_ENTRIES } from "../reading-list-tab.js";
import { ReaderView } from "../reader/ReaderView.jsx";
import { collectionCards } from "../run-collections.js";
import { ViewerContext } from "../viewer.js";
import { checkServer, createClient, serverOrigin } from "./server.js";
import { secureStore, tokenKeys } from "./secure-store.js";
import { createImageSource } from "./images.js";
import { Downloads } from "./downloads.js";
import { deviceStore } from "./device-store.js";
import { CAP_CHOICES, formatBytes, progressOf, usageBytes } from "./offline.js";

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
  async function enter(origin) {
    const next = await openClient(origin);
    setApiTransport((path, options) => next.api(path, options).catch((error) => {
      if (error.status === 401) lostRef.current();
      throw error;
    }));
    setClient(next);
    await settle(next, await next.api("/api/v1/auth/status"));
  }

  // Signed in or not, from /auth/status. Signed in, the profile's own
  // browser storage is chosen (as the web app does, profiles.js) and its
  // reader settings are taken from the server, where they follow the person
  // between devices -- without this the reader fell back to a phone's
  // default, panel view on.
  async function settle(next, answer) {
    // Another profile, or none, sees nothing answered for the last one: the
    // shared screens draw from api.js's last answers while they ask again,
    // and a reader must never glimpse a library above their rating limit (as
    // the web app's enterProfile forgets them).
    const viewerId = answer.viewer?.id ?? null;
    if (viewerId !== settledViewer.current) { forgetAnswers(); settledViewer.current = viewerId; }
    setStatus(answer);
    // A shared device (the admin signed in on it) that has not been told
    // who is reading asks, as the web app's picker does.
    if (!(answer.authenticated && answer.viewer)) { setPhase(answer.household && answer.profileRequired ? "picker" : "signin"); return; }
    setStorageProfile(answer.viewer.id);
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
    if (isLocked(profile)) { setAsking(profile); setPhase("picker"); return; }
    try {
      await client.api("/api/v1/profiles/switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ userId: profile.id }) });
      await settle(client, await client.api("/api/v1/auth/status"));
    } catch (err) { setError(err.message); }
  }, [client]);
  const signOut = useCallback(async () => {
    try { await client.api("/api/v1/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch { /* signed out either way */ }
    // On a shared iPad the device stays shared: back to "Who's reading?".
    await lostRef.current();
  }, [client]);

  if (phase === "starting") return <div className="login-shell"><LoadingIndicator size="lg" label="Starting" /></div>;
  if (phase === "connect") {
    return <ConnectScreen error={error} onConnected={async (origin) => {
      await secureStore.set("server", origin);
      setError("");
      await enter(origin);
    }} />;
  }
  if (phase === "picker") {
    return <PickerScreen client={client} current={status?.viewer?.id ?? null} ask={asking}
      onClose={status?.viewer ? () => { setAsking(null); setPhase("ready"); } : null}
      onSwitched={async () => settle(client, await client.api("/api/v1/auth/status"))} />;
  }
  if (phase === "signin") {
    return <SignInScreen client={client} onSignedIn={async () => {
      await settle(client, await client.api("/api/v1/auth/status"));
    }} onForget={async () => {
      await secureStore.remove("server");
      setPhase("connect");
    }} />;
  }
  return <Library key={status.viewer.id} client={client} status={status} onSwitch={openPicker} onSwitchTo={switchTo} onSignOut={signOut} />;
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
];

function Library({ client, status, onSwitch, onSwitchTo, onSignOut }) {
  const viewer = status.viewer;
  const [toastElement, showToast] = useToast();
  const downloads = useMemo(() => new Downloads({ client, profileId: viewer.id }), [client, viewer.id]);
  useEffect(() => () => downloads.stop(), [downloads]);
  const downloadVersion = useDownloadVersion(downloads);
  const pageSource = useMemo(() => createImageSource(client, viewer.id, {
    local: (url) => downloads.localPage(url),
    localUri: (path) => deviceStore.uri(path),
  }), [client, viewer.id, downloads]);

  const [catalog, setCatalog] = useState(() => lastAnswer("/api/v1/catalog"));
  const [backendStatus, setBackendStatus] = useState(() => (lastAnswer("/api/v1/catalog") ? "live" : "loading"));
  const loadCatalog = useCallback(async () => {
    try {
      const data = await apiRequest("/api/v1/catalog");
      setCatalog(data);
      setBackendStatus("live");
      return data;
    } catch (error) {
      // A 401 is the shell's to answer (sign in again, or "Who's reading?").
      if (error.status !== 401) showToast(error.message || "Flipparr could not be reached", "error");
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

  const actions = useReadingActions({
    notify: showToast, viewerId: viewer.id,
    afterRating: async () => {
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === selectedSeries?.id);
      if (refreshed) setSelectedSeries(refreshed);
    },
  });
  const { readingVersion, noteReadingChanged } = actions;
  useEffect(() => { downloads.reading = readFileId || null; }, [downloads, readFileId]);
  useEffect(() => { if (!readFileId) noteReadingChanged(); }, [readFileId, noteReadingChanged]);

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
    };
    const known = typeof target === "string" ? target : target?.id;
    if (known) { open(known); return; }
    if (!target?.runId && !target?.listId) return;
    try {
      const fileId = await readableFile(target);
      if (fileId) open(fileId);
      else showToast("There is nothing here that can be read yet.", "error");
    } catch (error) { showToast(error.message, "error"); }
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

  // Download and Remove download, in an issue's menu; its badge while it
  // downloads, or once it is here, where the tile has nothing else to say.
  const host = useMemo(() => ({
    api: apiRequest,
    canEditPanels: false,
    canManage: false,
    pageSource,
    issueExtras: (issue) => {
      if (!issue.fileId) return [];
      const row = downloads.row(issue.fileId);
      const label = `${issue.contextLabel || "Issue"} #${issue.number}`;
      if (row?.state === "ready" || row?.state === "downloading") {
        return [{ key: "offline-remove", label: row.state === "ready" ? "Remove download" : "Cancel download", icon: <Trash size={16} />,
          onSelect: () => downloads.remove(issue.fileId).then(() => showToast(`${label} removed from this device`)) }];
      }
      if (row?.state === "failed") {
        return [{ key: "offline-retry", label: "Try the download again", icon: <ArrowClockwise size={16} />, onSelect: () => downloads.retry(issue.fileId) }];
      }
      return [{ key: "offline", label: "Download", icon: <DownloadSimple size={16} />,
        onSelect: () => downloads.download(issue.fileId).then(() => showToast(`Downloading ${label}`), (error) => showToast(error.message || "Could not download", "error")) }];
    },
    issueBadge: (issue) => {
      const row = issue.fileId ? downloads.row(issue.fileId) : null;
      if (row?.state === "ready") return { label: "Downloaded", tone: "reading-read" };
      if (row?.state === "downloading") return { label: `Downloading ${Math.round(progressOf(row) * 100)}%`, tone: "reading-progress" };
      return null;
    },
  // downloadVersion: the menu and badge follow the downloads as they change.
  }), [pageSource, downloads, downloadVersion, showToast]);

  const header = useMemo(() => ({
    profile: {
      household: Boolean(status.household), method: status.method, version: 0,
      // Your own page is the web app's; here your tile just closes the menu.
      onOpenProfile: () => {}, onSwitchTo, onPicker: onSwitch, onSignOut,
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
    <div className="app-shell">
      <Nav items={NAV} active={page} onNavigate={(next) => { setPage(next); window.scrollTo({ top: 0 }); }} catalog={catalog} backendStatus={backendStatus}
        logicalSeriesCount={logicalSeriesCount} authStatus={status} onSignOut={onSignOut} scanning={false} />
      <main className="main-content"><div className="page-view" key={page}>
        {page === "library" ? <LibraryView readingList={actions.readingList} onToggleReadingList={actions.toggleReadingList}
          onOpenRunCollection={(card) => { setSelectedSeries(null); setSelectedList(null); setRunCollectionId(String(card.id)); }}
          tab={libraryTab} onTab={(next) => { setLibraryTab(next); window.scrollTo({ top: 0 }); }} onNavigate={setPage}
          onOpenSeries={openSeries} onOpenCollection={() => {}} onOpenList={openList} onRead={readComic}
          readingVersion={readingVersion} listsVersion={0} catalog={catalog} backendStatus={backendStatus} /> : null}
        {page === "downloads" ? <DownloadsPage downloads={downloads} onRead={readComic} /> : null}
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
      const origin = serverOrigin(address);
      await checkServer(origin);
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
      <input name="password" type="password" value={password} autoComplete="current-password"
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
// session in the Flipparr-Session header, which the client keeps.
function PickerScreen({ client, current, ask = null, onClose, onSwitched }) {
  const [profiles, setProfiles] = useState(null);
  const [asking, setAsking] = useState(ask);
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    client.api("/api/v1/profiles").then((data) => setProfiles(data.profiles || []), (err) => setError(err.message));
  }, [client]);
  async function enter(profile, proof = {}) {
    setBusy(true);
    setError("");
    try {
      await client.api("/api/v1/profiles/switch", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ userId: profile.id, ...proof }),
      });
      await onSwitched();
      return true;
    } catch (err) {
      setError(err.message);
      setBusy(false);
      return false;
    }
  }
  function choose(profile) {
    if (profile.id === current) { onClose?.(); return; }
    if (isLocked(profile)) { setAsking(profile); setSecret(""); setError(""); } else enter(profile);
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
          onSubmit={(pin) => enter(asking, { pin })} />
      </div> : asking ? <form className="profile-picker-secret" onSubmit={(event) => { event.preventDefault(); enter(asking, { password: secret }); }}>
        <ProfileAvatar profile={asking} size="lg" />
        <label className="form-field"><span>Password</span>
          <input type="password" autoFocus value={secret} name="password" autoComplete="current-password" onChange={(event) => setSecret(event.target.value)} />
        </label>
        {error ? <p className="login-error" role="alert">{error}</p> : null}
        <div className="profile-picker-actions">
          <button type="button" className="secondary-button" onClick={back}>Back</button>
          <button className="primary-button" disabled={busy || !secret} aria-busy={busy}>{busy ? <LoadingIndicator size="sm" /> : null} Continue</button>
        </div>
      </form> : <>
        {profiles === null && !error ? <LoadingIndicator size="md" /> : null}
        <ProfileTiles profiles={profiles || []} current={current} onChoose={choose} busy={busy} />
        {error ? <p className="login-error" role="alert">{error}</p> : null}
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
function DownloadsPage({ downloads, onRead }) {
  const rows = useDownloadRows(downloads);
  const [cap, setCap] = useState(null);
  useEffect(() => { deviceStore.capBytes().then(setCap); }, []);
  const used = usageBytes(rows);
  return <>
    <PageHeader title="Downloads" />
    <div className="settings-pane">
      <SettingsCard title="On this device">
        <p className="settings-card-lead">{formatBytes(used)} of {cap ? formatBytes(cap) : "…"} used. When a download would go over the limit, the comics you finished longest ago make way; one you are still reading never does.</p>
        {cap ? <GlassSelect label="Keep up to" value={cap} onChange={async (next) => { await deviceStore.setCapBytes(Number(next)); setCap(Number(next)); }}
          options={CAP_CHOICES.map((choice) => ({ value: choice, label: `Keep up to ${formatBytes(choice)}` }))} /> : null}
      </SettingsCard>
      <SettingsCard title="Downloaded comics">
        {rows.length ? <div className="profile-rows">
          {rows.map((row) => {
            const name = `${row.seriesTitle}${row.issueNumber ? ` #${row.issueNumber}` : ""}`;
            return <div className="profile-row" key={row.fileId}>
              <span><strong>{name}</strong><small role={row.state === "failed" ? "alert" : undefined}>
                {row.state === "ready" ? formatBytes(row.bytes)
                  : row.state === "failed" ? row.error
                  : `Downloading ${row.pagesDone.length} of ${row.pageCount} pages`}
              </small></span>
              {row.state === "ready" ? <button type="button" className="secondary-button" onClick={() => onRead(row.fileId)}><BookOpen size={16} /> Read</button> : null}
              {row.state === "failed" ? <button type="button" className="secondary-button" onClick={() => downloads.retry(row.fileId)}><ArrowClockwise size={16} /> Try again</button> : null}
              <button type="button" className="glass-button glass-button--icon" aria-label={`Remove ${name} from this device`} onClick={() => downloads.remove(row.fileId)}><Trash size={18} /></button>
            </div>;
          })}
        </div> : <p className="settings-card-lead">Nothing downloaded yet. In a run&rsquo;s Issues, open an issue&rsquo;s &hellip; menu and choose Download.</p>}
      </SettingsCard>
    </div>
  </>;
}
