// Two plain card edges stacked above a card: a run's comics kept together
// (Flipparr View's Downloads and its Downloaded shelf, after Kindle). Always
// two, however many comics (the owner, 2026-10-11). Placed first inside a
// `.download-stack`, before the card they stand behind.
export function StackEdges() {
  return <>{[2, 1].map((depth) => <span className={`download-stack-layer depth-${depth}`} aria-hidden="true" key={depth} />)}</>;
}
