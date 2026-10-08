// Flipparr Reader: the app's shell around the shared reader (src/reader).
// Find the server, sign in, see what to read, read it. Search, requests and
// everything an admin manages stay in the web app.
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ShieldCheck, SignOut } from "@phosphor-icons/react";
import { FlipparrMark } from "../brand.jsx";
import { LoadingIndicator } from "../components/LoadingIndicator";
import { ReaderView } from "../reader/ReaderView.jsx";
import { ReaderHost, usePageSrc } from "../reader/host.js";
import { checkServer, createClient, serverOrigin } from "./server.js";
import { secureStore, tokenKeys } from "./secure-store.js";
import { createImageSource } from "./images.js";
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
    if (!(answer.authenticated && answer.viewer)) { setPhase("signin"); return; }
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
  if (phase === "signin") {
    return <SignInScreen client={client} onSignedIn={async () => {
      await settle(client, await client.api("/api/v1/auth/status"));
    }} onForget={async () => {
      await secureStore.remove("server");
      setPhase("connect");
    }} />;
  }
  return <Library client={client} viewer={status.viewer} onSignOut={async () => {
    try { await client.api("/api/v1/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch { /* signed out either way */ }
    setPhase("signin");
  }} />;
}

// Signed in: what to read. The reader's host is made here, so every image the
// home screen and the reader show comes through the app's image source.
function Library({ client, viewer, onSignOut }) {
  const host = useMemo(() => ({
    api: client.api,
    // The panel editor stays in the web app (the owner, 2026-10-08).
    canEditPanels: false,
    pageSource: createImageSource(client, viewer.id),
  }), [client, viewer.id]);
  const [run, setRun] = useState(null);
  const [reading, setReading] = useState(null);
  const [version, setVersion] = useState(0);
  return <ReaderHost.Provider value={host}>
    {run ? <RunScreen client={client} run={run} version={version} onBack={() => setRun(null)}
      onRead={(issue, detail) => setReading({ fileId: issue.id, title: `${run.title} #${issue.issueNumber || "?"}`, medium: detail.medium, direction: detail.readingDirection, run })} />
      : <HomeScreen client={client} viewer={viewer} version={version} onSignOut={onSignOut} onOpenRun={setRun}
        onRead={(item) => setReading({ fileId: item.fileId, title: `${item.seriesTitle} #${item.issueNumber || "?"}`, medium: item.medium })} />}
    {reading ? <ReaderView fileId={reading.fileId} title={reading.title} medium={reading.medium}
      directionOverride={reading.direction} startPage={null} behind={false}
      onFinish={() => {}} onProgressSaved={() => setVersion((current) => current + 1)}
      onOpenRun={reading.run ? () => { setReading(null); setRun(reading.run); } : undefined}
      onClose={() => { setReading(null); setVersion((current) => current + 1); }} /> : null}
  </ReaderHost.Provider>;
}

function Cover({ url, alt = "" }) {
  const src = usePageSrc();
  const shown = src(url);
  return shown ? <img className="app-card-cover" src={shown} alt={alt} loading="lazy" decoding="async" />
    : <span className="app-card-cover" aria-hidden="true" />;
}

function HomeScreen({ client, viewer, version, onSignOut, onOpenRun, onRead }) {
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
    }, (err) => live && setError(err.message || "Flipparr could not be reached"));
    return () => { live = false; };
  }, [client, version]);
  return <main className="app-home">
    <header className="app-header">
      <FlipparrMark size={36} />
      <h1>Flipparr</h1>
      <p>{viewer.name}</p>
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
          <small>Page {Number(item.page) + 1} of {item.pageCount}</small>
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

function RunScreen({ client, run, version, onBack, onRead }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    client.api(`/api/v1/series/${run.id}/reading`).then((answer) => live && setDetail(answer),
      (err) => live && setError(err.message || "Flipparr could not be reached"));
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
      {issues.map((issue) => <button type="button" className="app-issue" key={issue.id} onClick={() => onRead(issue, detail)}>
        <strong>#{issue.issueNumber || issue.filename}</strong>
        <small>{issue.finishedAt ? "Read" : issue.page ? `Page ${issue.page + 1} of ${issue.pageCount}` : "Unread"}</small>
      </button>)}
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
