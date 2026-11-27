// The cover to show of a comic's candidates: the first not known to fail.
// Failures are remembered by address (src/components/CoverArt.jsx), so a
// candidate that arrives later is tried even after earlier ones failed.
export function coverToTry(candidates, failed) {
  return (candidates || []).find((candidate) => candidate && !failed.has(candidate)) || null;
}
