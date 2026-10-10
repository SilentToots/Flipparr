// Search's library half, shared by the web app's Search page and Flipparr
// View's (the owner, 2026-10-10: the app needs search, library only): what
// in the library answers a query as it is typed -- runs, collections and
// saved story arcs -- as the "In Your Library" row, and the recent searches
// shown before typing (after the Apple TV app's).
import { useMemo } from "react";
import { MagnifyingGlass } from "@phosphor-icons/react";
import { DiscoverCover } from "../components/CoverArt.jsx";
import { libraryRunMatches } from "../discover.js";
import { DEMO_SERIES, logicalCatalogSeries, searchQueryParts, useReadingLists } from "../library/LibraryView.jsx";
import { arcMatches, arcYears, listCard } from "../reading-list.js";
import { collectionMatches } from "../run-collections.js";

/** What in the library matches `liveQuery`: runs, collections and arcs. */
export function useLibraryMatches({ catalog, backendStatus, runCollections = [], liveQuery }) {
  const allSeries = useMemo(
    () => logicalCatalogSeries(catalog, backendStatus === "offline" ? DEMO_SERIES : []),
    [catalog, backendStatus]);
  const savedArcs = useReadingLists(0, allSeries);
  const libraryMatches = useMemo(() => {
    if (!liveQuery) return [];
    const parts = searchQueryParts(liveQuery);
    return allSeries.filter((item) => libraryRunMatches(item, parts));
  }, [allSeries, liveQuery]);
  // Collections and saved story arcs answer too: everything in the library.
  const collectionMatchCards = useMemo(() => liveQuery
    ? runCollections.filter((card) => card.runCount && collectionMatches(card, liveQuery)) : [], [runCollections, liveQuery]);
  const arcMatchCards = useMemo(() => liveQuery
    ? (savedArcs || []).map(listCard).filter((list) => arcMatches(list, liveQuery.toLowerCase())) : [], [savedArcs, liveQuery]);
  return { allSeries, libraryMatches, collectionMatchCards, arcMatchCards };
}

/** "In Your Library": the matches as covers, or a word that there are none. */
export function LibraryMatches({ state, count, liveQuery, libraryMatches, collectionMatchCards, arcMatchCards, onOpenRunCollection, onOpenRun, onOpenList }) {
  return <>
    {/* No count until there is one: "0" before the library has arrived is
        a claim, not a placeholder. */}
    <header><h2>{state === "loading" ? null : <>{count} </>}<span>In Your Library</span></h2></header>
    {state === "loading" ? <div className="library-match-row" role="status" aria-busy="true">
      <span className="sr-only">Reading your library</span>
      {[0, 1, 2].map((item) => <LibraryMatchSkeleton key={item} />)}
    </div> : count ? <div className="library-match-row">
      {collectionMatchCards.map((card) => <LibraryMatchCard series={{ id: card.key, title: card.name, cover: card.cover, year: `${card.runCount} run${card.runCount === 1 ? "" : "s"}` }}
        onOpen={() => onOpenRunCollection?.(card)} key={card.key} />)}
      {libraryMatches.map((series) => <LibraryMatchCard series={series} onOpen={onOpenRun} key={series.id} />)}
      {arcMatchCards.map((list) => <LibraryMatchCard series={{ id: `arc-${list.id}`, title: list.name, cover: list.cover, year: arcYears(list) || "Story arc" }}
        onOpen={() => onOpenList?.(list)} key={`arc-${list.id}`} />)}
    </div> : <p className="discover-note">Nothing in your library matches “{liveQuery}”.</p>}
  </>;
}

export function LibraryMatchCard({ series, onOpen }) {
  return <button type="button" className="library-match" onClick={() => onOpen(series)}>
    <span className="library-match-art">
      <DiscoverCover src={series.cover} alt={`${series.title} cover`} />
      {series.year ? <b>{series.year}</b> : null}
    </span>
    <h3 title={series.title}>{series.title}</h3>
  </button>;
}

// The Search page's "Recently Searched", after the Apple TV app's: what was
// opened from results, as cards of a cover, a title and a line under it. A
// library run no longer in the library drops out.
export function RecentSearches({ entries, allSeries, onClear, onSearch, onOpenSeries, onOpenRun }) {
  const cards = entries.map((entry) => {
    if (entry.kind === "query") {
      return { key: entry.key, title: entry.text, detail: "Search", query: true, open: () => onSearch(entry.text) };
    }
    if (entry.kind === "series") {
      const series = allSeries.find((item) => String(item.id) === String(entry.id));
      return series && { key: entry.key, title: series.title, cover: series.cover,
        detail: [series.publisher, series.year].filter(Boolean).join(" · ") || "In your library",
        open: () => onOpenSeries(series) };
    }
    const run = entry.item;
    return { key: entry.key, title: run.title, cover: run.cover,
      detail: [run.publisher, run.yearLabel || run.yearBegan].filter(Boolean).join(" · ") || "Comic catalog",
      open: () => onOpenRun(run) };
  }).filter(Boolean);
  if (!cards.length) return null;
  return <section className="recent-searches" aria-labelledby="recent-searches-title">
    <header>
      <h2 id="recent-searches-title">Recently Searched</h2>
      <button type="button" className="recent-searches-clear" onClick={onClear}>Clear</button>
    </header>
    <div className="recent-search-grid">
      {cards.map((card) => <button type="button" className="recent-search" key={card.key} onClick={card.open}>
        {card.query
          ? <span className="recent-search-art recent-search-query" aria-hidden="true"><MagnifyingGlass size={20} /></span>
          : <DiscoverCover src={card.cover} alt="" className="recent-search-art" glyph={18} />}
        <span className="recent-search-copy">
          <strong title={card.title}>{card.title}</strong>
          <small>{card.detail}</small>
        </span>
      </button>)}
    </div>
  </section>;
}

export function LibraryMatchSkeleton() {
  return <div className="library-match-skeleton" aria-hidden="true">
    <span className="discover-cover" />
    <h3><i /></h3>
  </div>;
}
