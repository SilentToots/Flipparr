// Which rating a run shows, and whose it is.
//
// A run carries two separate numbers: the rating you gave the run itself, and
// what the issues you rated average to. They are not interchangeable. A run
// nobody has rated, whose issues average four stars, has not been given four
// stars by anyone -- so when the average is what is on screen it says so, and
// the stars are drawn differently. Getting this wrong would have the app
// inventing an opinion and attributing it to the reader.
//
// Nothing here comes from a provider. Comic Vine carries no quality score,
// Metron's rating is an age rating, GCD is bibliographic, and Open Library's
// ratings only cover ISBN'd trades. Every number in this file is the reader's.

export const RATING_SOURCES = { yours: "yours", average: "average", none: "none" };
export const MAX_RATING = 5;

/**
 * What to show for a run.
 *
 * Your own rating wins whenever you have given one, even if its issues
 * average to something else: you rated the run, and that is the answer to
 * "what do you think of this run".
 */
export function runRating(series) {
  const yours = Number(series?.yourRating) || 0;
  if (yours) return { value: yours, source: RATING_SOURCES.yours, count: 0 };
  const summary = series?.issueRating;
  const average = Number(summary?.average) || 0;
  if (!average) return { value: 0, source: RATING_SOURCES.none, count: 0 };
  return { value: average, source: RATING_SOURCES.average, count: Number(summary.count) || 0 };
}

/** How many stars to fill: an average rounds to the nearest half. */
export function filledStars(value) {
  const rating = Math.min(MAX_RATING, Math.max(0, Number(value) || 0));
  return Math.round(rating * 2) / 2;
}

/**
 * What pressing a star means.
 *
 * Pressing the one you already gave takes the rating back, which is the only
 * way to undo without a second control. Anything else sets it.
 */
export function ratingForPress(star, current) {
  return star === current ? null : star;
}

/** Said in words, because stars alone are not read aloud. */
export function ratingLabel(rating, title) {
  if (rating.source === RATING_SOURCES.none) return `Rate ${title}`;
  if (rating.source === RATING_SOURCES.yours) {
    return `Your rating for ${title}: ${rating.value} of ${MAX_RATING} stars`;
  }
  return `${title} averages ${rating.value} of ${MAX_RATING} stars across ${rating.count} rated issue${rating.count === 1 ? "" : "s"}`;
}

/** Runs you rated highest first; an average stands in, and unrated sink. */
export function byRating(a, b) {
  return runRating(b).value - runRating(a).value;
}
