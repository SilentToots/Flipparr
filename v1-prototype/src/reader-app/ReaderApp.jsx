// Flipparr Reader: the app's shell around the shared reader (src/reader).
// Find the server, sign in, see what to read, read it. Search, requests and
// everything an admin manages stay in the web app.
import { useEffect, useMemo, useState } from "react";
import { ArrowClockwise, ArrowLeft, CheckCircle, DownloadSimple, ShieldCheck, SignOut, Trash, X } from "@phosphor-icons/react";
import { FlipparrMark } from "../brand.jsx";
import { LoadingIndicator } from "../components/LoadingIndicator";
import { ReaderView } from "../reader/ReaderView.jsx";
import { Host, useImageSrc } from "../host.js";
import { checkServer, createClient, serverOrigin } from "./server.js";
import { secureStore, tokenKeys } from "./secure-store.js";
import { createImageSource } from "./images.js";
import { Downloads } from "./downloads.js";
import { deviceStore } from "./device-store.js";
import { CAP_CHOICES, formatBytes, progressOf, usageBytes } from "./offline.js";
import { PinPad, ProfileAvatar, ProfileTiles } from "../components/ProfileTiles.jsx";
import { isLocked } from "../profiles.js";
import { profileStorage, setStorageProfile } from "../profiles.js";
import "./reader-app.css";

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

  async function enter(origin) {
    const next = await openClient(origin);
    setClient(next);
    await settle(next, await next.api("/api/v1/auth/status"));
  }

  // Signed in or not, from /auth/status. Signed in, the profile's own
  // browser storage is chosen (as the web app does, profiles.js) and its
  // reader settings are taken from the server, where they follow the person
  // between devices -- without this the reader fell back to a phone's
  // default, panel view on.
  async function settle(next, answer) {
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

  if (phase === "starting") return <div className="login-shell"><LoadingIndicator size="lg" label="Starting" /></div>;
  if (phase === "connect") {
    return <ConnectScreen error={error} onConnected={async (origin) => {
      await secureStore.set("server", origin);
      setError("");
      await enter(origin);
    }} />;
  }
  if (phase === "picker") {
    return <PickerScreen client={client} current={status?.viewer?.id ?? null}
      onClose={status?.viewer ? () => setPhase("ready") : null}
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
  // A session that ends while the app is open -- a new PIN, the admin's
  // first reader, "forget shared devices" -- answers 401: ask the server where
  // that leaves this device (the picker on a shared one, else sign-in).
  async function lost() {
    try { await settle(client, await client.api("/api/v1/auth/status")); } catch { setPhase("signin"); }
  }
  return <Library key={status.viewer.id} client={client} viewer={status.viewer} household={status.household}
    onSwitch={() => setPhase("picker")} onLost={lost} onSignOut={async () => {
    try { await client.api("/api/v1/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch { /* signed out either way */ }
    // On a shared iPad the device stays shared: back to "Who's reading?".
    await lost();
  }} />;
}

// Signed in: what to read. The reader's host is made here, so every image the
// home screen and the reader show comes through the app's image source.
function Library({ client, viewer, household, onSwitch, onSignOut, onLost }) {
  const downloads = useMemo(() => new Downloads({ client, profileId: viewer.id }), [client, viewer.id]);
  useEffect(() => () => downloads.stop(), [downloads]);
  const host = useMemo(() => ({
    api: client.api,
    // The panel editor stays in the web app (the owner, 2026-10-08).
    canEditPanels: false,
    pageSource: createImageSource(client, viewer.id, {
      local: (url) => downloads.localPage(url),
      localUri: (path) => deviceStore.uri(path),
    }),
  }), [client, viewer.id, downloads]);
  const [showDownloads, setShowDownloads] = useState(false);
  const [run, setRun] = useState(null);
  const [reading, setReading] = useState(null);
  const [version, setVersion] = useState(0);
  useEffect(() => { downloads.reading = reading?.fileId ?? null; }, [downloads, reading]);
  return <Host.Provider value={host}>
    {showDownloads ? <DownloadsScreen downloads={downloads} onBack={() => setShowDownloads(false)} />
      : run ? <RunScreen client={client} run={run} version={version} downloads={downloads} onLost={onLost} onBack={() => setRun(null)}
      onRead={(issue, detail) => setReading({ fileId: issue.id, title: `${run.title} #${issue.issueNumber || "?"}`, medium: detail.medium, direction: detail.readingDirection, run })} />
      : <HomeScreen client={client} viewer={viewer} household={household} version={version} onLost={onLost} onSignOut={onSignOut} onSwitch={onSwitch} onOpenRun={setRun}
        onDownloads={() => setShowDownloads(true)}
        onRead={(item) => setReading({ fileId: item.fileId, title: `${item.seriesTitle} #${item.issueNumber || "?"}`, medium: item.medium })} />}
    {reading ? <ReaderView fileId={reading.fileId} title={reading.title} medium={reading.medium}
      directionOverride={reading.direction} startPage={null} behind={false}
      onFinish={() => {}} onProgressSaved={() => setVersion((current) => current + 1)}
      onOpenRun={reading.run ? () => { setReading(null); setRun(reading.run); } : undefined}
      onClose={() => { setReading(null); setVersion((current) => current + 1); }} /> : null}
  </Host.Provider>;
}

function Cover({ url, alt = "" }) {
  const src = useImageSrc();
  const shown = src(url);
  return shown ? <img className="app-card-cover" src={shown} alt={alt} loading="lazy" decoding="async" />
    : <span className="app-card-cover" aria-hidden="true" />;
}

function HomeScreen({ client, viewer, household, version, onLost, onSignOut, onSwitch, onOpenRun, onRead, onDownloads }) {
  const [shelf, setShelf] = useState(null);
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    Promise.all([client.api("/api/v1/reading"), client.api("/api/v1/catalog")]).then(([reading, catalog]) => {
      if (!live) return;
      setShelf(reading.items || []);
      // Runs with a comic to read; what is only wanted is the web app's.
      setRuns((catalog.series || []).filter((series) => series.owned > 0));
    }, (err) => {
      if (!live) return;
      if (err.status === 401) { onLost(); return; }
      setError(err.message || "Flipparr could not be reached");
    });
    return () => { live = false; };
  }, [client, version]);
  return <main className="app-home">
    <header className="app-header">
      <FlipparrMark size={36} />
      <h1>Flipparr</h1>
      {/* Profiles are switched only on a shared device, as in the web app. */}
      {household ? <button type="button" className="app-profile" onClick={onSwitch} aria-label={`${viewer.name}. Switch profile`}>
        <ProfileAvatar profile={viewer} size="sm" /><span>{viewer.name}</span>
      </button> : <p>{viewer.name}</p>}
      <button type="button" className="glass-button glass-button--icon" aria-label="Downloads" onClick={onDownloads}><DownloadSimple size={20} /></button>
      <button type="button" className="glass-button glass-button--icon" aria-label="Sign out" onClick={onSignOut}><SignOut size={20} /></button>
    </header>
    {error ? <p className="login-error" role="alert">{error}</p> : null}
    {shelf === null && !error ? <LoadingIndicator size="lg" label="Loading your comics" /> : null}
    {shelf?.length ? <section className="app-section">
      <h2>Keep reading</h2>
      <div className="app-row">
        {shelf.map((item) => <button type="button" className="app-card" key={item.fileId} onClick={() => onRead(item)}>
          <Cover url={`/api/v1/files/${item.fileId}/pages/0`} />
          <strong>{item.seriesTitle} #{item.issueNumber || "?"}</strong>
          {/* "next" is the issue after one just finished: not started, so it
              has no page count to show. */}
          <small>{item.resume === "next" || !item.pageCount ? "Up next" : `Page ${Number(item.page) + 1} of ${item.pageCount}`}</small>
        </button>)}
      </div>
    </section> : null}
    {runs ? <section className="app-section">
      <h2>Your library</h2>
      {runs.length ? <div className="app-grid">
        {runs.map((series) => <button type="button" className="app-card" key={series.id} onClick={() => onOpenRun(series)}>
          <Cover url={series.cover} />
          <strong>{series.title}</strong>
          <small>{series.year ? `${series.year} · ` : ""}{series.owned} {series.owned === 1 ? "comic" : "comics"}</small>
        </button>)}
      </div> : <p className="app-empty">Nothing to read yet. Comics you add in Flipparr show up here.</p>}
    </section> : null}
  </main>;
}

function RunScreen({ client, run, version, downloads, onLost, onBack, onRead }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  useDownloadRows(downloads);
  useEffect(() => {
    let live = true;
    client.api(`/api/v1/series/${run.id}/reading`).then((answer) => live && setDetail(answer), (err) => {
      if (!live) return;
      if (err.status === 401) { onLost(); return; }
      setError(err.message || "Flipparr could not be reached");
    });
    return () => { live = false; };
  }, [client, run.id, version]);
  const issues = [...(detail?.issues || []), ...(detail?.volumes || [])].filter((issue) => issue.readable);
  return <main className="app-run">
    <header className="app-header">
      <button type="button" className="glass-button glass-button--icon" aria-label="Back" onClick={onBack}><ArrowLeft size={20} /></button>
      <h1>{run.title}</h1>
    </header>
    {error ? <p className="login-error" role="alert">{error}</p> : null}
    {detail === null && !error ? <LoadingIndicator size="lg" label="Loading issues" /> : null}
    {detail ? <div className="app-issues">
      {issues.map((issue) => <div className="app-issue" key={issue.id}>
        <button type="button" className="app-issue-open" onClick={() => onRead(issue, detail)}>
          <strong>#{issue.issueNumber || issue.filename}</strong>
          <small>{issue.finishedAt ? "Read" : issue.page && issue.pageCount ? `Page ${issue.page + 1} of ${issue.pageCount}` : "Unread"}</small>
        </button>
        <DownloadControl downloads={downloads} fileId={issue.id} label={`#${issue.issueNumber || issue.filename}`}
          groupKey={`run:${run.id}`} onError={setError} />
      </div>)}
    </div> : null}
  </main>;
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
function PickerScreen({ client, current, onClose, onSwitched }) {
  const [profiles, setProfiles] = useState(null);
  const [asking, setAsking] = useState(null);
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

/** One comic's download, in its row: download, progress, done, or retry. */
function DownloadControl({ downloads, fileId, label, groupKey, onError }) {
  const row = downloads.row(fileId);
  const [asking, setAsking] = useState(false);
  if (row?.state === "ready") {
    return <span className="app-download app-download--ready" role="img" aria-label={`${label} is downloaded`}><CheckCircle size={22} weight="fill" /></span>;
  }
  if (row?.state === "downloading") {
    const percent = Math.round(progressOf(row) * 100);
    return <span className="app-download" role="progressbar" aria-label={`Downloading ${label}`} aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>{percent}%</span>;
  }
  if (row?.state === "failed") {
    return <button type="button" className="app-download" aria-label={`Try downloading ${label} again`} onClick={() => downloads.retry(fileId)}>
      <ArrowClockwise size={20} />
    </button>;
  }
  return <button type="button" className="app-download" aria-label={`Download ${label}`} disabled={asking} onClick={async () => {
    setAsking(true);
    onError("");
    try { await downloads.download(fileId, { groupKey }); } catch (err) { onError(err.message || "Could not download"); }
    setAsking(false);
  }}>{asking ? <LoadingIndicator size="sm" /> : <DownloadSimple size={20} />}</button>;
}

// What this device keeps for reading offline: each download, how much room it
// takes, and the device's limit (the oldest finished comics make way first).
function DownloadsScreen({ downloads, onBack }) {
  const rows = useDownloadRows(downloads);
  const [cap, setCap] = useState(null);
  useEffect(() => { deviceStore.capBytes().then(setCap); }, []);
  const used = usageBytes(rows);
  return <main className="app-run">
    <header className="app-header">
      <button type="button" className="glass-button glass-button--icon" aria-label="Back" onClick={onBack}><ArrowLeft size={20} /></button>
      <h1>Downloads</h1>
    </header>
    <section className="app-section">
      <label className="app-cap">
        <span>{formatBytes(used)} of {cap ? formatBytes(cap) : "…"} used on this device</span>
        <select value={cap ?? ""} onChange={async (event) => {
          const next = Number(event.target.value);
          await deviceStore.setCapBytes(next);
          setCap(next);
        }}>
          {CAP_CHOICES.map((choice) => <option key={choice} value={choice}>Keep up to {formatBytes(choice)}</option>)}
        </select>
      </label>
      <p className="app-empty">When a download would go over the limit, the comics you finished longest ago make way. One you are still reading never does.</p>
    </section>
    {rows.length ? <div className="app-issues">
      {rows.map((row) => <div className="app-issue" key={row.fileId}>
        <div className="app-issue-open">
          <strong>{row.seriesTitle}{row.issueNumber ? ` #${row.issueNumber}` : ""}</strong>
          <small role={row.state === "failed" ? "alert" : undefined}>
            {row.state === "ready" ? formatBytes(row.bytes)
              : row.state === "failed" ? row.error
              : `Downloading ${row.pagesDone.length} of ${row.pageCount} pages`}
          </small>
        </div>
        {row.state === "failed" ? <button type="button" className="app-download" aria-label="Try again" onClick={() => downloads.retry(row.fileId)}><ArrowClockwise size={20} /></button> : null}
        <button type="button" className="app-download" aria-label={`Remove ${row.seriesTitle} #${row.issueNumber || ""}`} onClick={() => downloads.remove(row.fileId)}>
          <Trash size={20} />
        </button>
      </div>)}
    </div> : <p className="app-empty">Nothing downloaded yet. Open a run and tap the download button beside an issue.</p>}
  </main>;
}
