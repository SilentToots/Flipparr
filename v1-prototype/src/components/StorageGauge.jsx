// How much of the device's download limit is used, as a gauge (Flipparr
// View, the owner 2026-10-10): the track is the limit, the fill what the
// downloads take, with a short label for each (Apple HIG, Gauges).
import { formatBytes } from "../reader-app/offline.js";

export function StorageGauge({ used, cap }) {
  const share = cap ? Math.min(1, Math.max(0, used / cap)) : 0;
  return <div className="storage-gauge">
    <div className="storage-gauge-labels">
      <strong>{formatBytes(used)} used</strong>
      <span>{cap ? `${formatBytes(cap)} limit` : "…"}</span>
    </div>
    <div className="storage-gauge-track" role="meter" aria-label="Downloads on this device"
      aria-valuemin={0} aria-valuemax={cap || 0} aria-valuenow={used}
      aria-valuetext={cap ? `${formatBytes(used)} of ${formatBytes(cap)}` : formatBytes(used)}>
      <i style={{ width: `${Math.round(share * 100)}%` }} />
    </div>
  </div>;
}
