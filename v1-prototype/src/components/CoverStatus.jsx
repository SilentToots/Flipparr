// A cover's download state (Flipparr View; the owner's examples, 2026-10-10).
// A comic on its way dims, with a ring and its percentage in the middle, as
// Infuse draws one; a run, collection or arc with some of its comics on the
// way gets the ring small in the bottom-right corner, since the rest of it
// still reads. Once here, a check in a dark circle in that corner, Kindle's
// mark. The host decides (`coverStatus(target)`); the web gives none, and
// this draws nothing.
import { useContext } from "react";
import { Check } from "@phosphor-icons/react";
import { Host } from "../host.js";
import { ProgressRing } from "./ProgressRing.jsx";

/** `target`: `{kind: "issue", fileId}` or `{kind: "run" | "collection" | "arc", id}`. */
export function CoverStatus({ target }) {
  const describe = useContext(Host)?.coverStatus;
  const status = describe && target ? describe(target) : null;
  if (!status) return null;
  if (status.state === "downloading") {
    const percent = Math.round(Math.max(0, Math.min(1, Number(status.progress) || 0)) * 100);
    const label = `Downloading, ${percent}%`;
    if (target.kind === "issue") {
      return <span className="cover-status downloading" role="img" aria-label={label}>
        <ProgressRing progress={status.progress} className="cover-status-ring"><b>{percent}%</b></ProgressRing>
      </span>;
    }
    return <span className="cover-status corner" role="img" aria-label={label}>
      <ProgressRing progress={status.progress} />
    </span>;
  }
  return <span className="cover-status corner done" role="img" aria-label={status.label || "On this device"}>
    <Check size={13} weight="bold" aria-hidden="true" />
  </span>;
}
