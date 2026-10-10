// A determinate ring: how much of something is done, filling clockwise from
// the top. One drawing for every download in Flipparr View -- a cover's, an
// issue row's, a run's Download button -- so they fill alike (Apple's HIG,
// Progress indicators: determinate where it can be, and one style). What it
// holds in its middle (a percentage, a stop square) is the caller's.
const RADIUS = 9;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function ProgressRing({ progress, className = "", children = null }) {
  const done = Math.max(0, Math.min(1, Number(progress) || 0));
  return <span className={`progress-ring${className ? ` ${className}` : ""}`}>
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle className="progress-ring-track" cx="12" cy="12" r={RADIUS} />
      <circle className="progress-ring-fill" cx="12" cy="12" r={RADIUS}
        strokeDasharray={CIRCUMFERENCE} strokeDashoffset={CIRCUMFERENCE * (1 - done)} />
    </svg>
    {children}
  </span>;
}

/** The square inside a ring that stops what it shows (Apple Books, Music). */
export function StopSquare() {
  return <i className="progress-ring-stop" aria-hidden="true" />;
}
