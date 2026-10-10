// A cover's download state, in its top-right corner (Flipparr View, the
// owner, 2026-10-10): a ring filling as a comic downloads, a mark once it is
// on the device. Circular throughout and always in the same place, as
// Apple's HIG asks of progress. The host decides (`coverStatus(target)`);
// the web gives none, and this draws nothing.
import { useContext } from "react";
import { DownloadSimple } from "@phosphor-icons/react";
import { Host } from "../host.js";

const RADIUS = 9;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/** `target`: `{kind: "issue", fileId}` or `{kind: "run" | "collection" | "arc", id}`. */
export function CoverStatus({ target }) {
  const describe = useContext(Host)?.coverStatus;
  const status = describe && target ? describe(target) : null;
  if (!status) return null;
  if (status.state === "downloading") {
    const progress = Math.max(0, Math.min(1, Number(status.progress) || 0));
    return <span className="cover-status downloading" role="img" aria-label={`Downloading, ${Math.round(progress * 100)}%`}>
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <circle className="cover-status-track" cx="12" cy="12" r={RADIUS} />
        <circle className="cover-status-fill" cx="12" cy="12" r={RADIUS}
          strokeDasharray={CIRCUMFERENCE} strokeDashoffset={CIRCUMFERENCE * (1 - progress)} />
      </svg>
    </span>;
  }
  return <span className="cover-status done" role="img" aria-label={status.label || "On this device"}>
    <DownloadSimple size={13} weight="bold" aria-hidden="true" />
  </span>;
}
