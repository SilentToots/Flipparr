import { useEffect, useState } from "react";
import { BookOpen } from "@phosphor-icons/react";
import { useImageSrc } from "../host.js";
import { coverToTry } from "../cover-pick.js";

// Covers, shared by the web app and Flipparr View. Each image loads through
// the host (src/host.js): its own address on the web, the app's local copy
// in Flipparr View. Until the app's copy is ready the <img> has no source,
// so the card keeps its size.

// What failed is remembered by address, not by a place in the list: a cover
// that arrives later under the same comic (a provider adds the image after
// the first look, a new cover is chosen) is a new address and is tried, where
// a count of failures carried over skipped it until the comic changed (the
// owner's run drawer, 2026-11-27: the header showed the cover, the slot the
// placeholder).
export function CoverArt({ id, title, cover, coverCandidates, decorative = false, placeholderSize = 28 }) {
  const candidates = coverCandidates?.length ? coverCandidates : (cover ? [cover] : []);
  const [failed, setFailed] = useState(() => new Set());
  const imageSrc = useImageSrc();
  useEffect(() => setFailed(new Set()), [id]);
  const shown = coverToTry(candidates, failed);
  if (shown) return <img src={imageSrc(shown)} alt={decorative ? "" : `${title} cover`} onError={() => setFailed((current) => new Set(current).add(shown))} />;
  return <span className="cover-placeholder" role="img" aria-label={decorative ? undefined : `No cover available for ${title}`}><BookOpen size={placeholderSize} weight="duotone" /></span>;
}

export function SeriesCover({ series, decorative = false }) {
  return <CoverArt id={series.id} title={series.title} cover={series.cover} coverCandidates={series.coverCandidates} decorative={decorative} />;
}

// A cover that 404s must not leave a broken-image glyph in a row of art.
export function DiscoverCover({ src, alt, className = "", glyph = 22 }) {
  const [failed, setFailed] = useState(null);
  const imageSrc = useImageSrc();
  return <span className={`discover-cover ${className}`.trim()}>
    {src && failed !== src
      ? <img src={imageSrc(src)} alt={alt} loading="lazy" onError={() => setFailed(src)} />
      : <BookOpen size={glyph} weight="duotone" aria-hidden="true" />}
  </span>;
}
