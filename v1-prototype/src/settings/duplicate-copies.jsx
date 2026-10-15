// Settings > Library health: one copy per issue (the owner, 2026-10-14).
// Issues with two copies are listed with the copy Flipparr would keep and why;
// "Keep the best of each" shows what it will do and moves the other copies to
// .flipparr/duplicates on the admin's word, never deleting them. A row's
// "Keep the other" swaps which copy stays. What a person read, rated or fixed
// with either copy stays with the issue.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Copy, ArrowsLeftRight } from "@phosphor-icons/react";
import { apiRequest } from "../api.js";
import { SettingsCard } from "../components/SettingsControls.jsx";
import { LoadingSpinner } from "../components/glass.jsx";

const issueName = (group) => `${group.runTitle}${group.runYear ? ` (${group.runYear})` : ""} #${group.issueNumber}`;
const sizeOf = (bytes) => `${Math.max(1, Math.round(bytes / 1e6))} MB`;

export function DuplicateCopies({ onChanged }) {
  const [issues, setIssues] = useState(null);
  const [chosen, setChosen] = useState({});
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(() => apiRequest("/api/v1/library/duplicates")
    .then((data) => { setIssues(data.issues || []); setChosen({}); },
      (failure) => { setIssues([]); setError(failure.message || "The duplicate copies could not be read"); }), []);
  useEffect(() => { load(); }, [load]);
  const plan = useMemo(() => (issues || []).map((group) => {
    const keep = chosen[group.issueId] || group.keepFileId;
    const kept = group.copies.find((copy) => copy.fileId === keep) || group.copies[0];
    return { group, kept, others: group.copies.filter((copy) => copy !== kept), swapped: keep !== group.keepFileId };
  }), [issues, chosen]);
  if (!issues || (!issues.length && !result && !error)) return null;
  async function keepThem() {
    setBusy(true);
    setError("");
    try {
      const done = await apiRequest("/api/v1/library/duplicates", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keep: plan.map(({ group, kept }) => ({ issueId: group.issueId, keepFileId: kept.fileId })) }),
      });
      setResult(`${done.setAside} ${done.setAside === 1 ? "copy" : "copies"} moved to .flipparr/duplicates${done.failed.length ? `; ${done.failed.length} could not be moved` : ""}.`);
      setConfirming(false);
      await load();
      onChanged?.();
    } catch (failure) {
      setError(failure.message || "That did not work");
    } finally {
      setBusy(false);
    }
  }
  const setAside = plan.reduce((count, row) => count + row.others.length, 0);
  return <SettingsCard title="Duplicate copies">
    <p className="settings-card-lead">{issues.length
      ? `${issues.length} ${issues.length === 1 ? "issue has" : "issues have"} more than one copy. Flipparr keeps one copy per issue; the others are moved to .flipparr/duplicates in the library folder, never deleted. What you have read, rated or fixed stays with the issue.`
      : "Every issue has one copy."}</p>
    {result ? <p className="settings-card-note" role="status">{result}</p> : null}
    {issues.length ? <details>
      <summary className="settings-card-note">Show the {issues.length} {issues.length === 1 ? "issue" : "issues"}</summary>
      <div className="settings-root-list">
        {plan.map(({ group, kept, others, swapped }) => <span key={group.issueId}>
          <Copy size={18} />
          <strong title={`Keeps ${kept.filename} (${kept.extension.slice(1).toUpperCase()}, ${sizeOf(kept.sizeBytes)}${kept.damaged ? ", damaged" : ""}); sets aside ${others.map((copy) => copy.filename).join(", ")}`}>
            {issueName(group)}: keeps {swapped ? "the copy you chose" : group.why}
          </strong>
          {group.copies.length === 2 ? <button type="button" className="secondary-button" disabled={busy}
            onClick={() => setChosen((current) => ({ ...current, [group.issueId]: others[0].fileId }))}>
            <ArrowsLeftRight size={16} /> Keep the other</button> : <small>{group.copies.length} copies</small>}
        </span>)}
      </div>
    </details> : null}
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
    {issues.length ? <div className="settings-card-actions">
      {confirming
        ? <>
          <button type="button" className="primary-button" onClick={keepThem} disabled={busy} aria-busy={busy}>
            {busy ? <LoadingSpinner size={18} /> : <Copy size={18} />} Move {setAside} {setAside === 1 ? "copy" : "copies"} aside</button>
          <button type="button" className="secondary-button" onClick={() => setConfirming(false)} disabled={busy}>Cancel</button>
          <small>One copy of each issue stays where it is.</small>
        </>
        : <button type="button" className="secondary-button" onClick={() => setConfirming(true)}>
          <Copy size={18} /> Keep one copy of each ({issues.length})</button>}
    </div> : null}
  </SettingsCard>;
}
