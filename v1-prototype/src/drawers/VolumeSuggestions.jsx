// A manga run's volumes the indexers carry and no catalog lists yet, offered
// to the admin (the owner, 2026-11-15: Comic Vine knew two of the
// Mandalorian manga's four volumes). Nothing is added unasked: Add makes them
// issues of the run, searched with it; Not now stops offering them. Found
// once a day by the server's volume scan (app.py, scan_indexer_volumes).
import { useEffect, useState } from "react";
import { Books } from "@phosphor-icons/react";
import { apiRequest } from "../api.js";

/** "Volume 3", "Volumes 3 and 4", "Volumes 3, 4 and 6". */
export function volumesLabel(numbers) {
  const list = [...numbers];
  if (list.length === 1) return `Volume ${list[0]}`;
  return `Volumes ${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

export function VolumeSuggestions({ series, onAdded }) {
  const [suggestions, setSuggestions] = useState([]);
  const [busy, setBusy] = useState("");
  const [said, setSaid] = useState("");
  const path = `/api/v1/series/${series.id}/volume-suggestions`;
  useEffect(() => {
    let live = true;
    setSuggestions([]);
    setSaid("");
    apiRequest(path).then((data) => { if (live) setSuggestions(data.suggestions || []); }, () => {});
    return () => { live = false; };
  }, [path]);
  async function decide(action) {
    if (busy) return;
    setBusy(action);
    try {
      const numbers = suggestions.map((item) => item.number);
      const data = await apiRequest(path, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [action]: numbers }),
      });
      setSuggestions(data.suggestions || []);
      if (action === "accept" && data.added?.length) {
        setSaid(`${volumesLabel(data.added)} added. ${data.added.length === 1 ? "It is" : "They are"} searched with the run.`);
        onAdded?.();
      }
    } catch (error) {
      setSaid(error.message || "That did not save");
    } finally {
      setBusy("");
    }
  }
  if (!suggestions.length) {
    return said ? <p className="discover-note" role="status">{said}</p> : null;
  }
  const numbers = suggestions.map((item) => item.number);
  return <div className="shelf-message volume-suggestions" role="status">
    <Books size={18} aria-hidden="true" />
    <span>
      <strong>{volumesLabel(numbers)} {numbers.length === 1 ? "is" : "are"} on your indexers</strong>
      <small>No catalog lists {numbers.length === 1 ? "it" : "them"} yet. Add {numbers.length === 1 ? "it" : "them"} to download with the run.</small>
    </span>
    <div className="volume-suggestions-actions">
      <button type="button" onClick={() => decide("accept")} disabled={Boolean(busy)}>{busy === "accept" ? "Adding…" : "Add"}</button>
      <button type="button" onClick={() => decide("dismiss")} disabled={Boolean(busy)}>Not now</button>
    </div>
  </div>;
}
