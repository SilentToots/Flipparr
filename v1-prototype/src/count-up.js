// The arithmetic behind a counter that counts to its new value (useCountUp).
// Kept out of the component so it can be checked without a browser.

/** Long enough to read a jump, never long enough to wait for: 220ms, plus
 *  12ms a step, capped at 900ms. */
export function countUpDuration(from, to) {
  return Math.min(900, 220 + Math.abs(to - from) * 12);
}

/** Where the number is `elapsed` into its run: fast first, settling at the
 *  end, so it lands on the value rather than stopping at it. */
export function countUpValue(from, to, elapsed, duration) {
  if (!(duration > 0)) return to;
  const t = Math.min(1, Math.max(0, elapsed / duration));
  return Math.round(from + (to - from) * (1 - (1 - t) ** 3));
}
