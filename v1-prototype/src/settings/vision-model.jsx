// Settings > Reader > Vision assistant: which Claude model reads panels (the
// owner, 2026-10-16: "how would a general user make the best choice"). Only
// models Flipparr has measured on hand-fixed pages are offered, each with what
// a night of the overnight read costs on it; the release's pick is marked.
import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "../api.js";
import { SettingsField } from "../components/SettingsControls.jsx";
import { GlassSelect } from "../components/glass.jsx";

const dollars = (amount) => (amount >= 10 ? `$${Math.round(amount)}` : amount >= 0.1 ? `$${amount.toFixed(2)}` : `$${amount.toFixed(3)}`);

/** A choice as the select names it: the model and its price, short enough for a phone's field. */
export function modelOption(choice, nightlyPages) {
  const price = nightlyPages ? `about ${dollars(choice.perNight)} a night` : `about ${dollars(choice.perPage)} a page`;
  return { value: choice.id, label: `${choice.name.replace(/^Claude /, "")} · ${price}` };
}

/** The line under it: what the chosen model is for, and whether it is this release's pick. */
export function modelHint(choice) {
  return `${choice.label}${choice.default ? ", recommended" : ""}. ${choice.detail}`;
}

export function VisionModelField() {
  const [status, setStatus] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(() => apiRequest("/api/v1/vision/models").then(setStatus, () => {}), []);
  useEffect(() => { load(); }, [load]);
  if (!status) return null;
  async function choose(id) {
    setSaving(true);
    setError("");
    try {
      await apiRequest("/api/v1/settings", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ visionModel: id }) });
      await load();
    } catch (failure) {
      setError(failure.message || "That did not save");
    } finally {
      setSaving(false);
    }
  }
  const current = status.choices.find((choice) => choice.id === status.current);
  return <>
    <SettingsField label="Model" hint={current ? modelHint(current) : `Set on the server: ${status.current}.`}>
      <GlassSelect label="Model" disabled={saving} value={current ? current.id : ""} placeholder={status.current}
        onChange={choose} options={status.choices.map((choice) => modelOption(choice, status.nightlyPages))} />
    </SettingsField>
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
  </>;
}
