import { useEffect, useState } from "react";
import { BookOpen } from "@phosphor-icons/react";
import { useImageSrc } from "../host.js";

// Covers, shared by the web app and Flipparr View. Each image loads through
// the host (src/host.js): its own address on the web, the app's local copy
// in Flipparr View. Until the app's copy is ready the <img> has no source,
// so the card keeps its size.

export function CoverArt({ id, title, cover, coverCandidates, decorative = false, placeholderSize = 28 }) {
  const candidates = coverCandidates?.length ? coverCandidates : (cover ? [cover] : []);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const imageSrc = useImageSrc();
  useEffect(() => setCandidateIndex(0), [id]);
  if (candidates[candidateIndex]) return <img src={imageSrc(candidates[candidateIndex])} alt={decorative ? "" : `${title} cover`} onError={() => setCandidateIndex((index) => index + 1)} />;
  return <span className="cover-placeholder" role="img" aria-label={decorative ? undefined : `No cover available for ${title}`}><BookOpen size={placeholderSize} weight="duotone" /></span>;
}

export function SeriesCover({ series, decorative = false }) {
  return <CoverArt id={series.id} title={series.title} cover={series.cover} coverCandidates={series.coverCandidates} decorative={decorative} />;
}

// A cover that 404s must not leave a broken-image glyph in a row of art.
export function DiscoverCover({ src, alt, className = "", glyph = 22 }) {
  const [failed, setFailed] = useState(false);
  const imageSrc = useImageSrc();
  return <span className={`discover-cover ${className}`.trim()}>
    {src && !failed
      ? <img src={imageSrc(src)} alt={alt} loading="lazy" onError={() => setFailed(true)} />
      : <BookOpen size={glyph} weight="duotone" aria-hidden="true" />}
  </span>;
}
