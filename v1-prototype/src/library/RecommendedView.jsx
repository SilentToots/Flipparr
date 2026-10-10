import { useContext, useEffect, useMemo, useState } from "react";
import { apiRequest, lastAnswer } from "../api.js";
import { ShelfBackIcon, ShelfNextIcon } from "../design-icons.jsx";
import { keepReading, localDay, recentlyAddedIssues, recentlyAddedRuns, recentlyReleased } from "../recommended.js";
import { CatalogEmpty } from "./LibraryView.jsx";
import { LibraryShelf } from "./shelves.jsx";
import { Host, useReadable } from "../host.js";

/**
 * Comics' home: what to pick up, as shelves (Komga's, and Plex's
 * Recommended). Keep Reading is the server's carry-on list; the rest are read
 * off the catalog this profile was sent, so a reader's shelves never hold a
 * run above their limit. An empty shelf is left out.
 */
export function RecommendedView({ series, catalog, backendStatus, readingVersion, onOpenSeries, onRead, onAdd, onSeeInProgress, onSeeAdded }) {
  const [carryOn, setCarryOn] = useState(() => lastAnswer("/api/v1/reading")?.items ?? null);
  useEffect(() => {
    let live = true;
    apiRequest("/api/v1/reading")
      .then((data) => { if (live) setCarryOn(data.items || []); })
      .catch(() => { if (live) setCarryOn([]); });
    return () => { live = false; };
  }, [readingVersion]);
  // Offline in Flipparr View, only comics on the device can be picked up.
  const readable = useReadable();
  const own = useMemo(() => [
    { id: "keep", title: "Keep Reading", items: keepReading((carryOn || []).filter((item) => !item.fileId || readable({ kind: "issue", fileId: String(item.fileId) })), series), onSeeAll: onSeeInProgress },
    { id: "released", title: "Recently Released", items: recentlyReleased(series, localDay()) },
    { id: "issues", title: "Recently Added Issues", items: recentlyAddedIssues(series) },
    { id: "runs", title: "Recently Added Runs", items: recentlyAddedRuns(series), onSeeAll: onSeeAdded },
  ], [carryOn, series, onSeeInProgress, onSeeAdded, readable]);
  // The host's own shelves (Flipparr View's Downloaded): first where the
  // host says so, else after Keep Reading. The web has none.
  const hosted = useContext(Host)?.shelves?.() || [];
  const shelves = [...hosted.filter((shelf) => shelf.first), own[0], ...hosted.filter((shelf) => !shelf.first), ...own.slice(1)];
  // Shelves while it loads, as it will be -- the Library's grid skeleton
  // it showed was another tab's shape (owner, 2026-10-03).
  if ((backendStatus === "loading" && !catalog) || carryOn === null) return <RecommendedSkeleton />;
  if (!series.length) return <CatalogEmpty onAdd={onAdd} />;
  const byId = new Map(series.map((run) => [String(run.id), run]));
  function open(card) {
    // Keep Reading opens the comic where it was left; any other card opens
    // its run, where the issue is one tap away.
    if (card.resume) {
      onRead({ id: card.fileId });
      return;
    }
    const run = card.run || byId.get(String(card.runId));
    if (run) onOpenSeries(run);
  }
  return <div className="recommended-shelves">
    {shelves.map((shelf) => <LibraryShelf key={shelf.id} title={shelf.title} items={shelf.items} onSeeAll={shelf.onSeeAll} onOpen={open} />)}
  </div>;
}

/** One Recommended shelf card's box, empty: cover, title, its note. */
export function ShelfCardSkeleton() {
  return <div className="pull-card shelf-card pull-card-skeleton" aria-hidden="true">
    <span className="discover-cover" />
    <div className="pull-card-body"><h3><i /></h3><small><i /></small></div>
  </div>;
}

/** The Recommended tab before its shelves arrive: shelves of the same boxes, so nothing moves. */
export function RecommendedSkeleton() {
  return <div className="recommended-shelves" role="status" aria-busy="true">
    <span className="sr-only">Loading your shelves</span>
    {[0, 1, 2].map((shelf) => <section className="release-shelf library-shelf" key={shelf} aria-hidden="true">
      <header>
        <div className="release-shelf-heading"><h2><i className="shelf-heading-skeleton" /></h2></div>
        <div className="shelf-scroll shelf-scroll--placeholder"><button type="button" tabIndex={-1} disabled><ShelfBackIcon /></button><button type="button" tabIndex={-1} disabled><ShelfNextIcon /></button></div>
      </header>
      <div className="shelf-row">{[0, 1, 2, 3, 4, 5, 6, 7].map((card) => <ShelfCardSkeleton key={card} />)}</div>
    </section>)}
  </div>;
}
