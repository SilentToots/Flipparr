// Settings > Library: the library's existing RAR comics stored as CBZ, on the
// admin's word (docs/READING_STATE_PLAN.md step 6). Each keeps its record and
// everything read, rated, fixed or chosen with it; the original is kept in
// .flipparr/converted unless the admin says not to. Started and stopped by
// hand; one comic at a time in the background.
import { useCallback, useEffect, useState } from "react";
import { ArrowsClockwise, Stop } from "@phosphor-icons/react";
import { apiRequest } from "../api.js";
import { SettingsCard, Toggle } from "../components/SettingsControls.jsx";
import { LoadingSpinner } from "../components/glass.jsx";

export function LibraryCbzConversion() {
  const [status, setStatus] = useState(null);
  const [keepOriginals, setKeepOriginals] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(() => apiRequest("/api/v1/library/cbz-conversion").then(setStatus, () => {}), []);
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
      setStatus(await apiRequest("/api/v1/library/cbz-conversion", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, keepOriginals }),
      }));
    } catch (failure) {
      setError(failure.message || "That did not work");
    } finally {
      setBusy(false);
    }
  }
  if (!status || (!status.candidates && !status.running && !status.finishedAt)) return null;
  const finished = status.done + status.kept + status.failed;
  return <SettingsCard title="Existing CBR comics">
    <p className="settings-card-lead">{status.running
      ? `Converting ${finished + 1} of ${status.total}${status.current ? `: ${status.current}` : ""}`
      : status.candidates
        ? `${status.candidates} ${status.candidates === 1 ? "comic is" : "comics are"} stored as CBR (or 7z). Converting stores them as CBZ, every page as it was; what you have read, rated, fixed or chosen stays with each comic.`
        : "Every comic is stored as CBZ."}</p>
    {status.finishedAt && !status.running ? <p className="settings-card-note">
      Last run: {status.done} converted, {status.kept} left as they are{status.failed ? `, ${status.failed} failed` : ""}.
      {status.lastError ? ` ${status.lastError}.` : status.lastKept ? ` ${status.lastKept}.` : ""}
    </p> : null}
    {!status.running && status.candidates ? <Toggle checked={keepOriginals} onChange={setKeepOriginals} title="Keep the originals"
      description="Each original CBR is moved to .flipparr/converted in its library folder, where you can delete it once you are happy. Off, it is deleted once its CBZ is checked." /> : null}
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
    <div className="settings-card-actions">
      {status.running
        ? <button type="button" className="secondary-button" onClick={() => act("stop")} disabled={busy}><Stop size={18} /> Stop after this comic</button>
        : status.candidates ? <button type="button" className="primary-button" onClick={() => act("start")} disabled={busy} aria-busy={busy}>
          {busy ? <LoadingSpinner size={18} /> : <ArrowsClockwise size={18} />} Convert {status.candidates} to CBZ</button> : null}
    </div>
  </SettingsCard>;
}
