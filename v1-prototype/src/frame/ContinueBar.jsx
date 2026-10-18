// The comic to pick up, floating over the page (Flipparr View, the owner's
// Kindle example, 2026-10-10; the web's phones too, as Music's mini player
// above its tab bar, 2026-10-18): the first of the server's carry-on list --
// the comic last left part-read, or a run's next one -- with its cover, how
// far in it is, and a tap that opens it where it was left. Offline it is
// drawn from the kept answer, and only a comic on the device counts.
import { useEffect, useMemo, useState } from "react";
import { BookOpen } from "@phosphor-icons/react";
import { apiRequest, lastAnswer } from "../api.js";
import { DiscoverCover } from "../components/CoverArt.jsx";
import { useReadable } from "../host.js";
import { keepReading } from "../recommended.js";

export function ContinueBar({ series, readingVersion, onRead, className = "" }) {
  const [items, setItems] = useState(() => lastAnswer("/api/v1/reading")?.items ?? []);
  useEffect(() => {
    let live = true;
    apiRequest("/api/v1/reading")
      .then((data) => { if (live) setItems(data.items || []); })
      .catch(() => {});
    return () => { live = false; };
  }, [readingVersion]);
  const readable = useReadable();
  const card = useMemo(() => keepReading(
    items.filter((item) => item.fileId && readable({ kind: "issue", fileId: String(item.fileId) })), series, 1,
  )[0] || null, [items, series, readable]);
  if (!card) return null;
  const percent = card.progress ? Math.max(1, Math.round(card.progress * 100)) : 0;
  const detail = percent ? `${card.note} · ${percent}%` : card.note;
  return <div className={`continue-bar ${className}`.trim()}>
    <button type="button" className="continue-bar-button" onClick={() => onRead({ id: card.fileId })}
      aria-label={`${percent ? "Continue" : "Begin"} ${card.title}, ${detail}`}>
      <DiscoverCover src={card.cover} alt="" className="continue-bar-cover" glyph={18} />
      <span className="continue-bar-text"><strong>{card.title}</strong><small>{detail}</small></span>
      <BookOpen className="continue-bar-icon" size={22} weight="fill" aria-hidden="true" />
    </button>
  </div>;
}
