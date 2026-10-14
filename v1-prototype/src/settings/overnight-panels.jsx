// Settings > Reader: the overnight panel read (the owner, 2026-10-14). Between
// the hours set, the panels of the comics each profile is likely to open next
// are read ahead -- Keep Reading and each run's next issue, new arrivals,
// comics on Flipparr View devices -- so opening one is instant. A page nothing
// has read yet is still read the moment it is opened.
import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "../api.js";
import { SettingsCard, Toggle } from "../components/SettingsControls.jsx";
import { GlassSelect } from "../components/glass.jsx";

const hourLabel = (hour) => {
  if (hour === 0) return "Midnight";
  if (hour === 12) return "Noon";
  return hour < 12 ? `${hour} am` : `${hour - 12} pm`;
};
const HOURS = Array.from({ length: 24 }, (_, hour) => ({ value: hour, label: hourLabel(hour) }));
const LIMITS = [
  { value: 0, label: "No vision overnight" }, { value: 100, label: "100 pages" },
  { value: 300, label: "300 pages" }, { value: 1000, label: "1,000 pages" },
];
const STOPPED = {
  done: "finished", window_end: "stopped when the window closed", cap: "stopped at the night's vision limit",
  vision_unavailable: "paused while the vision connector rested", stopped: "stopped when Flipparr restarted",
  error: "stopped by a problem (Settings › System)", interrupted: "cut short when Flipparr was stopped",
};

function lastNight(run) {
  if (!run) return "It has not run yet.";
  const read = `${run.pagesRead.toLocaleString()} ${run.pagesRead === 1 ? "page" : "pages"} read`;
  const vision = run.visionPages ? `, ${run.visionPages.toLocaleString()} by vision` : "";
  return `Last run (${run.night}): ${read}${vision} across ${run.comics} ${run.comics === 1 ? "comic" : "comics"}; ${STOPPED[run.stopped] || run.stopped || "running"}.`;
}

export function OvernightPanels() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const load = useCallback(() => apiRequest("/api/v1/panels/overnight").then(setStatus, () => {}), []);
  useEffect(() => { load(); }, [load]);
  async function save(patch) {
    setError("");
    try {
      await apiRequest("/api/v1/settings", {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
      });
      await load();
    } catch (failure) {
      setError(failure.message || "That did not save");
    }
  }
  if (!status) return null;
  return <SettingsCard title="Overnight panel read">
    <p className="settings-card-lead">Reads the panels of the comics you are likely to open next while nobody is reading &mdash; Keep Reading and each run&rsquo;s next issue, new arrivals, and comics on your devices &mdash; so panel view opens at once. A page it has not reached is still read the moment you open it.</p>
    <Toggle checked={status.enabled} onChange={(next) => save({ overnightPanels: next })} title="Read panels overnight"
      description="Uses this server's own clock; readers' comics are read by Flipparr itself, never on your key unless readers' pages are allowed above." />
    {status.enabled ? <>
      <div className="form-field settings-language settings-scan-interval"><span>From</span>
        <GlassSelect label="From" value={status.start} onChange={(next) => save({ overnightPanelsStart: Number(next) })} options={HOURS} /></div>
      <div className="form-field settings-language settings-scan-interval"><span>Until</span>
        <GlassSelect label="Until" value={status.end} onChange={(next) => save({ overnightPanelsEnd: Number(next) })} options={HOURS} /></div>
      <div className="form-field settings-language settings-scan-interval"><span>Vision pages a night</span>
        <GlassSelect label="Vision pages a night" value={status.visionPages} onChange={(next) => save({ overnightVisionPages: Number(next) })} options={LIMITS} /></div>
      <p className="settings-card-note">{lastNight(status.runs?.[0])} Tonight&rsquo;s list: {status.tonight.comics} {status.tonight.comics === 1 ? "comic" : "comics"}.</p>
    </> : null}
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
  </SettingsCard>;
}
