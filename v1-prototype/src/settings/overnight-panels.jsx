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
const dollars = (amount) => (amount >= 10 ? `$${Math.round(amount)}` : `$${amount.toFixed(2)}`);

/** Each nightly limit with what it costs, at the vision model's measured price a page (the owner, 2026-10-16). */
export function limitOptions(choices, perPage) {
  return (choices || [0, 100, 300, 1000]).map((pages) => ({
    value: pages,
    label: pages ? `${pages.toLocaleString()} pages · about ${dollars(pages * perPage)} a night` : "No vision overnight",
  }));
}

export function budgetOptions(choices) {
  return (choices || [0]).map((amount) => ({ value: amount, label: amount ? `Stop once about $${amount} is spent` : "No budget" }));
}

/** What the overnight read has spent, said plainly, and how it was worked out. */
export function spentNote(cost, budget) {
  if (!cost) return "";
  const spent = `The overnight read has spent about ${dollars(cost.overnightSpent)} on the vision model`;
  const left = budget ? `, ${dollars(Math.max(0, budget - cost.overnightSpent))} of your $${budget} left` : "";
  const how = cost.measured
    ? `about ${dollars(cost.perPage)} a page, from the token counts of its last ${cost.measuredCalls.toLocaleString()} vision readings`
    : `about ${dollars(cost.perPage)} a page, an estimate until it has read enough pages to measure`;
  const earlier = cost.overnightEstimatedBefore ? ` (${dollars(cost.overnightEstimatedBefore)} of it estimated, from nights before costs were recorded)` : "";
  return `${spent}${left}${earlier}. Priced at ${how}.`;
}
const STOPPED = {
  done: "finished", window_end: "stopped when the window closed", cap: "stopped at the night's vision limit",
  vision_unavailable: "paused while the vision connector rested", stopped: "stopped when Flipparr restarted",
  error: "stopped by a problem (Settings › System)", interrupted: "cut short when Flipparr was stopped",
  budget: "stopped at the overnight budget",
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
  const [saving, setSaving] = useState(false);
  const load = useCallback(() => apiRequest("/api/v1/panels/overnight").then(setStatus, () => {}), []);
  useEffect(() => { load(); }, [load]);
  async function save(patch) {
    setError("");
    setSaving(true);
    try {
      await apiRequest("/api/v1/settings", {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
      });
      await load();
    } catch (failure) {
      setError(failure.message || "That did not save");
    } finally {
      setSaving(false);
    }
  }
  if (!status) return null;
  const noWindow = status.enabled && status.start === status.end;
  return <SettingsCard title="Overnight panel read" aria-busy={saving}>
    <p className="settings-card-lead">Reads the panels of the comics you are likely to open next while nobody is reading &mdash; Keep Reading and each run&rsquo;s next issue, new arrivals, and comics on your devices &mdash; so panel view opens at once. A page it has not reached is still read the moment you open it.</p>
    <Toggle checked={status.enabled} disabled={saving} onChange={(next) => save({ overnightPanels: next })} title="Read panels overnight"
      description="Uses this server's own clock; readers' comics are read by Flipparr itself, never on your key unless “Readers’ pages too” is on above." />
    {status.enabled ? <>
      <div className="form-field settings-language settings-scan-interval"><span>From</span>
        <GlassSelect label="From" disabled={saving} value={status.start} onChange={(next) => save({ overnightPanelsStart: Number(next) })} options={HOURS} /></div>
      <div className="form-field settings-language settings-scan-interval"><span>Until</span>
        <GlassSelect label="Until" disabled={saving} value={status.end} onChange={(next) => save({ overnightPanelsEnd: Number(next) })} options={HOURS} /></div>
      <div className="form-field settings-language settings-scan-interval"><span>Vision pages a night</span>
        <GlassSelect label="Vision pages a night" disabled={saving} value={status.visionPages} onChange={(next) => save({ overnightVisionPages: Number(next) })}
          options={limitOptions(status.visionChoices, status.cost?.perPage ?? 0.005)} /></div>
      {status.visionPages ? <div className="form-field settings-language settings-scan-interval"><span>Overnight budget</span>
        <GlassSelect label="Overnight budget" disabled={saving} value={status.visionBudget ?? 0} onChange={(next) => save({ overnightVisionBudget: Number(next) })}
          options={budgetOptions(status.budgetChoices)} /></div> : null}
      {status.cost ? <p className="settings-card-note">{spentNote(status.cost, status.visionBudget)}</p> : null}
      {noWindow ? <p className="workbench-error" role="alert">From and Until are the same hour, so there is no window: the read never runs. Choose different hours.</p> : null}
      <p className="settings-card-note">{lastNight(status.runs?.[0])} Tonight&rsquo;s list: {status.tonight.comics} {status.tonight.comics === 1 ? "comic" : "comics"}.</p>
    </> : null}
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
  </SettingsCard>;
}
