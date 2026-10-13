// Settings > Library folders: damaged comics (the owner, 2026-10-13). "Check
// every comic" reads every page of every comic once, in the background, and
// marks the damaged ones in Library health; a comic already read whole is not
// read again until it changes. "Replace damaged comics automatically" (off by
// default) replaces proven damage through the wanted list, the damaged file
// kept until a good copy passes its checks.
import { useCallback, useEffect, useState } from "react";
import { MagnifyingGlass, Stop } from "@phosphor-icons/react";
import { apiRequest } from "../api.js";
import { SettingsCard, Toggle } from "../components/SettingsControls.jsx";
import { LoadingSpinner } from "../components/glass.jsx";

const comics = (count) => `${count} ${count === 1 ? "comic" : "comics"}`;

export function DamagedComics({ autoReplace, onAutoReplace, saving }) {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(() => apiRequest("/api/v1/library/archive-check").then(setStatus, () => {}), []);
  useEffect(() => { load(); }, [load]);
  // While it runs, how far it has got.
  useEffect(() => {
    if (!status?.running) return undefined;
    const timer = window.setInterval(load, 3000);
    return () => window.clearInterval(timer);
  }, [status?.running, load]);
  async function act(action) {
    setBusy(true);
    setError("");
    try {
      setStatus(await apiRequest("/api/v1/library/archive-check", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }),
      }));
    } catch (failure) {
      setError(failure.message || "That did not work");
    } finally {
      setBusy(false);
    }
  }
  const found = status ? status.damaged + status.unreadable : 0;
  return <SettingsCard title="Damaged comics">
    <p className="settings-card-lead">{status?.running
      ? `Checking ${status.done + 1} of ${status.total}${status.current ? `: ${status.current}` : ""}`
      : status?.candidates
        ? `${comics(status.candidates)} ${status.candidates === 1 ? "has" : "have"} not had every page read. A comic can be damaged where a scan cannot see it: a page that fails its checksum, or is cut short.`
        : status ? "Every comic has had its pages read, and damaged ones are listed under Library health." : "Checking…"}</p>
    {status?.finishedAt && !status.running ? <p className="settings-card-note">
      Last check: {comics(status.done)} read{found ? `, ${found} damaged (listed under Library health)` : ", none damaged"}
      {status.failed ? `, ${status.failed} could not be checked` : ""}.
    </p> : null}
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
    {status ? <div className="settings-card-actions">
      {status.running
        ? <button type="button" className="secondary-button" onClick={() => act("stop")} disabled={busy}><Stop size={18} /> Stop after this comic</button>
        : status.candidates ? <button type="button" className="primary-button" onClick={() => act("start")} disabled={busy} aria-busy={busy}>
          {busy ? <LoadingSpinner size={18} /> : <MagnifyingGlass size={18} />} Check {comics(status.candidates)}</button> : null}
    </div> : null}
    <Toggle checked={autoReplace} onChange={saving ? () => {} : onAutoReplace} title="Replace damaged comics automatically"
      description="Only comics proven damaged, through the wanted list, a few at a time. Each stays readable until a good copy passes its checks; an issue that already has a whole copy is never downloaded again." />
  </SettingsCard>;
}
