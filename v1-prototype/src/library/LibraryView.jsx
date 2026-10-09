import { useEffect, useMemo, useRef, useState } from "react";
import { apiRequest, lastAnswer } from "../api.js";
import { SeriesCover } from "../components/CoverArt.jsx";
import { GlassSegmented, LoadingSpinner } from "../components/glass.jsx";
import { CollectionIcon, GridViewIcon, ListViewIcon, ReadingListIcon, ViewOptionsIcon } from "../design-icons.jsx";
import { SheetGrabber, slideSheetAway, useDialog, useDrawerExit, usePhoneWidth } from "../dialogs.jsx";
import { libraryRunMatches } from "../discover.js";
import { FollowSwitch, useRunReading } from "../drawers/frame.jsx";
import { identityKey } from "../editions.js";
import { PageHeader, SegmentedTabs } from "../frame/header.jsx";
import { LIBRARY_DEFAULTS, SORT_OPTIONS, inProgress, loadLibraryPrefs, saveLibraryPrefs, sortLibrary } from "../library.js";
import { can } from "../profiles.js";
import { EMPTY_ENTRIES, readingListItems, sortReadingList } from "../reading-list-tab.js";
import { foldArcRuns, listCard } from "../reading-list.js";
import { collectionCards, collectionMatches, collectionPlaces, libraryGridOrder } from "../run-collections.js";
import { arcPublisher } from "../run-details.js";
import { useViewer } from "../viewer.js";
import { AlphaRail } from "./AlphaRail.jsx";
import { RecommendedView } from "./RecommendedView.jsx";
import { ArcGrid, CollectionCard, Ownership, SeriesList, publicationState } from "./cards.jsx";
import { ArrowRight, BookOpen, Books, Check, CheckCircle, FolderOpen, ListBullets, ListNumbers, MagnifyingGlass, Plus, UploadSimple, WarningCircle, X } from "@phosphor-icons/react";
import { useManages } from "../host.js";

// Shown only when the server cannot be reached ("Showing sample comics"):
// invented titles and publishers, and no cover art -- Flipparr ships no
// comics or publishers' artwork (2026-10-03).
export const DEMO_SERIES = [
  {
    id: "sample-starlight-couriers", title: "Starlight Couriers", year: "2024", publisher: "Sample Press",
    run: "2024 – Ongoing", tags: ["Science fiction"], owned: 3, total: 3,
    ownership: "Complete to date", format: "Single issues", updated: "Aug 26, 2026", time: "10:14 AM",
    cover: null, status: "complete",
  },
  {
    id: "sample-hollow-lantern", title: "The Hollow Lantern", year: "2014", publisher: "Sample Press",
    run: "2014 – 2017", tags: ["Fantasy", "Adventure"], owned: 2, total: 50,
    ownership: "48 issues missing", format: "Single issues", updated: "Aug 26, 2026", time: "10:02 AM",
    cover: null, status: "partial",
  },
  {
    id: "sample-ironwood", title: "Ironwood", year: "2008", publisher: "Example Comics",
    run: "Volume 2", tags: ["Horror", "Volume"], owned: 1, total: 1,
    ownership: "Volume owned", format: "EPUB volume", updated: "Aug 26, 2026", time: "9:58 AM",
    cover: null, status: "complete",
  },
  {
    id: "sample-night-shift", title: "Night Shift", year: "2014", publisher: "Example Comics",
    run: "Volume 2", tags: ["Crime", "Volume"], owned: 1, total: 1,
    ownership: "Needs a metadata fix", format: "CBZ volume", updated: "Aug 26, 2026", time: "9:41 AM",
    cover: null, status: "warning",
  },
];

export function familyRepresentsSeries(family) {
  const runs = family?.runs || [];
  const arcs = family?.storyArcs || [];
  if (family?.structureStatus !== "confirmed" || !runs.length || !arcs.length) return false;
  const groupedRunIds = new Set(arcs.flatMap((arc) => arc.runIds || []).map(String));
  if (!runs.every((run) => groupedRunIds.has(String(run.id)))) return false;
  const root = identityKey(family.name);
  if (!root) return false;
  const alignedRuns = runs.filter((run) => identityKey(run.title).includes(root));
  return alignedRuns.length / runs.length >= 0.75;
}

export function collectionSeriesEntry(family) {
  const runs = family.runs || [];
  const runLifecycle = runs.map((run) => publicationState(run).tone);
  const directIssueFiles = runs.reduce((count, run) => count + (run.inventory?.directIssueFiles || 0), 0);
  const editionCount = runs.reduce((count, run) => count + (run.inventory?.editionCount || 0), 0);
  const fileDetails = runs.flatMap((run) => run.fileDetails || []);
  const issues = runs.flatMap((run) => run.issues || []);
  const ownedIssueCount = issues.filter((issue) => issue.ownership !== "unowned").length;
  const missingIssueCount = Math.max(0, issues.length - ownedIssueCount);
  const latestRun = [...runs].sort((a, b) => `${b.updated || ""} ${b.time || ""}`.localeCompare(`${a.updated || ""} ${a.time || ""}`))[0] || {};
  const storyArcCount = family.mainArcCount || 0;
  const specialCount = family.specialGroupCount || 0;
  return {
    ...family,
    id: `collection-series-${family.id}`,
    title: family.name,
    run: `${storyArcCount} run${storyArcCount === 1 ? "" : "s"}${specialCount ? ` · ${specialCount} special${specialCount === 1 ? "" : "s"}` : ""}`,
    tags: ["Series"],
    format: "Series",
    updated: latestRun.updated || "",
    time: latestRun.time || "",
    inventory: { directIssueFiles, editionCount },
    fileDetails,
    issues,
    owned: ownedIssueCount,
    total: issues.length,
    unowned: missingIssueCount,
    catalogKnown: issues.length > 0,
    publicationStatus: runLifecycle.includes("ongoing")
      ? "ongoing"
      : runLifecycle.length && runLifecycle.every((state) => state === "completed")
        ? "completed" : "unknown",
    status: issues.length ? (missingIssueCount ? "partial" : "complete") : family.status,
    ownership: issues.length ? (missingIssueCount ? `${missingIssueCount} issues missing` : "All issues owned") : family.ownership,
    files: fileDetails.map((file) => file.path),
    isCollectionSeries: true,
    collection: family,
    searchText: `${family.name} ${family.publisher} ${runs.map((run) => run.title).join(" ")} ${(family.storyArcs || []).map((arc) => arc.name).join(" ")}`,
  };
}

export function logicalCatalogSeries(catalog, fallback = []) {
  const runs = catalog?.series ?? fallback;
  const logicalFamilies = (catalog?.families || []).filter(familyRepresentsSeries);
  const hiddenRunIds = new Set(logicalFamilies.flatMap((family) => family.runIds || []).map(String));
  return [
    ...runs.filter((run) => !hiddenRunIds.has(String(run.id))),
    ...logicalFamilies.map(collectionSeriesEntry),
  ].sort((a, b) => a.title.localeCompare(b.title));
}

export function CollectionGroups({ families, onOpenCollection }) {
  return <div className="family-groups">
    {families.map((family) => {
      const display = { ...family, id: `family-${family.id}`, title: family.name };
      return <section className="family-group" key={family.id}>
        <button className="family-summary" onClick={() => onOpenCollection(family)}>
          <span className="family-cover"><SeriesCover series={display} decorative /></span>
          <span className="family-copy"><b>Series family</b><strong>{family.name}</strong><small>{family.year} · {family.publisher} · {family.runCount} publication run{family.runCount === 1 ? "" : "s"} · {family.mainArcCount || 0} story arc{family.mainArcCount === 1 ? "" : "s"}</small></span>
          <Ownership series={family} />
          <ArrowRight size={20} />
        </button>
      </section>;
    })}
  </div>;
}

/** A title-sorted grid with its A-Z rail beside it, or the grid alone. */
export function AlphaScroller({ items, enabled, children }) {
  const ref = useRef(null);
  if (!enabled) return children;
  return <div className="has-alpha-rail">
    <div className="alpha-content" ref={ref}>{children}</div>
    <AlphaRail items={items} containerRef={ref} />
  </div>;
}

// A tab's action at the end of the tab row: labelled while the row has room,
// its icon alone (named, with a tooltip) once the tabs need the space, so it
// never drops to a row of its own.
export function TabAction({ icon, label, onClick }) {
  return <button type="button" className="glass-button tab-action" onClick={onClick} aria-label={label} title={label}>
    {icon}<span className="tab-action-label" aria-hidden="true">{label}</span>
  </button>;
}

// `tools`: the New button above the grid, on a phone; wider, it sits at the
// end of the tab row.
export function CollectionsTab({ cards, reading, onOpen, onRead, onNew, tools = true, loading = false }) {
  // Collections come with the catalog: until it is in, the grid is loading,
  // not empty -- a refresh showed "No collections yet" for seconds.
  if (loading) return <LibraryLoadingSkeleton view="grid" />;
  // Any profile makes its own (schema 63), as with story arcs -- where the
  // host makes them: Flipparr View passes no onNew.
  return <>
    {tools && onNew && cards.length ? <div className="arc-tools arc-tools--single">
      <button type="button" className="glass-button" onClick={onNew}><Plus size={17} /> New collection</button>
    </div> : null}
    {cards.length ? <div className="series-grid">{cards.map((card, index) => <CollectionCard card={card} index={index} reading={reading} onOpen={onOpen} onRead={onRead} key={card.key} />)}</div>
      : <div className="empty-state"><CollectionIcon size={35} /><strong>No collections yet</strong>
        <span>A collection is runs that belong together, read in your order: a character&rsquo;s essentials, a creator&rsquo;s work.{onNew ? " Make one, then add runs." : ""}</span>
        {onNew ? <button className="ghost-button" onClick={onNew}><Plus size={17} /> New collection</button> : null}</div>}
  </>;
}

/**
 * Story arcs -- reading orders across runs -- with a way to make one, as the
 * Collections tab has. Any profile makes its own; the admin also imports.
 */
export function ArcsTab({ lists, reading, loading, admin, onOpen, onRead, onNew, onImport, tools: showTools = true }) {
  if (loading) return <LibraryLoadingSkeleton view="grid" />;
  // A phone fits each button to a column of the grid below it, in words
  // short enough for a 320px screen's column; the tab already says "story".
  // Where the host makes them: Flipparr View passes no onNew or onImport.
  const importing = admin && Boolean(onImport);
  const tools = onNew || importing ? <>
    {onNew ? <button type="button" className={lists.length ? "glass-button" : "ghost-button"} onClick={onNew}><Plus size={17} /> <span className="label-wide">New story arc</span><span className="label-narrow">New arc</span></button> : null}
    {importing ? <button type="button" className={lists.length ? "glass-button" : "ghost-button"} onClick={onImport}><UploadSimple size={17} /> <span className="label-wide">Import a story arc</span><span className="label-narrow">Import arc</span></button> : null}
  </> : null;
  return lists.length ? <>
    {showTools && tools ? <div className={`arc-tools ${onNew && importing ? "arc-tools--pair" : "arc-tools--single"}`}>{tools}</div> : null}
    <ArcGrid lists={lists} reading={reading} onOpen={onOpen} onRead={onRead} />
  </> : <div className="empty-state"><ListNumbers size={35} weight="duotone" /><strong>No story arcs yet</strong>
    <span>A story arc is a reading order across runs: a crossover, or your own path through a character.{onNew ? " Make one, then add issues from any run." : ""}</span>
    {tools}</div>;
}

/**
 * What this profile added to read: runs, story arcs and collections, newest
 * first, each with a way off the list. In-progress reading is Keep Reading's.
 */
export function ReadingListTab({ items, reading, listReading = {}, loading, onOpen, onRead, onRemove, onBrowse }) {
  if (loading) return <LibraryLoadingSkeleton view="grid" />;
  if (!items.length) return <div className="empty-state"><ReadingListIcon size={35} /><strong>Nothing on your reading list</strong>
    <span>Tap the bookmark on a run, story arc or collection, and it waits here for when you want something new.</span>
    <button className="ghost-button" onClick={onBrowse}>Browse the library</button></div>;
  // Arcs' places are keyed by their card's id, as the Library grid keys them.
  const places = { ...(reading || {}) };
  for (const { item } of items) if (item.kind === "arc" && listReading[item.listId]) places[item.id] = listReading[item.listId];
  return <SeriesList series={items.map((entry) => entry.item)} view="grid" reading={places} onOpen={onOpen} onRead={onRead}
    cardAction={(item) => {
      const kind = item.kind === "collection" ? "collection" : item.kind === "arc" ? "arc" : "run";
      const id = kind === "arc" ? item.listId : item.id;
      const title = item.title ?? item.name;
      return <button type="button" className="glass-button glass-button--icon card-remove" onClick={() => onRemove(kind, id)}
        aria-label={`Take ${title} off your reading list`} title="Take off your reading list"><X size={16} /></button>;
    }} />;
}

export function CollectionEmpty({ query }) {
  return <div className="empty-state"><Books size={35} weight="duotone" /><strong>{query ? `No series families match “${query}”` : "No series families yet"}</strong><span>{query ? "Try another family or run title." : "Open a run and use its Family tab to group related publication runs."}</span></div>;
}

export function searchQueryParts(value) {
  const cleaned = String(value || "").trim().replace(/\s+/g, " ");
  const match = cleaned.match(/^(.+?)(?:\s+|\s*\()((?:19|20)\d{2})\)?$/);
  return match ? { title: match[1].trim(), year: match[2] } : { title: cleaned, year: "" };
}

// A scan in progress and a first page load look identical to someone arriving:
// nothing is on screen yet. The difference worth telling them is why, and how
// far along it is -- so the skeleton carries the scan's own count when there is
// one, rather than a generic "loading".
export function scanCounts(scan) {
  const total = Number(scan?.total_files || 0);
  const processed = Number(scan?.processed_files || 0);
  return { total, processed, percent: total ? Math.min(99, Math.round((processed / total) * 100)) : 0 };
}

export function LibraryLoadingSkeleton({ scan = null, view = "grid" }) {
  const { total, processed } = scanCounts(scan);
  return <div className="library-loading" role="status" aria-live="polite" aria-busy="true">
    <section className="library-loading-message">
      <span className="library-loading-icon"><LoadingSpinner size={21} /></span>
      {scan
        ? <span><strong>Scanning your library…</strong><small>{total ? `Comic ${Math.min(processed + 1, total)} of ${total} · your runs appear as they are found.` : "Counting your comic files. Nothing is renamed or moved."}</small></span>
        : <span><strong>Loading your library…</strong><small>Bringing in your comic runs, covers, and collection status.</small></span>}
    </section>
    <div className="library-loading-tools" aria-hidden="true"><i /><i /><i /></div>
    {/* The shape the comics will arrive in. It drew a table whichever view
        was chosen, left over from when the list was the default, so a grid
        library rearranged itself the moment it loaded. */}
    {view === "grid"
      ? <section className="library-loading-grid series-grid" aria-hidden="true">
        {[0, 1, 2, 3, 4, 5, 6, 7].map((item) => <span className="library-loading-card" key={item}>
          <span className="library-loading-cover" />
          <span className="library-loading-copy"><i /><i /></span>
          <span className="library-loading-progress"><i /><i /></span>
        </span>)}
      </section>
      : <section className="library-loading-table" aria-hidden="true">
        <header><i /><i /><i /><i /></header>
        {[0, 1, 2, 3, 4].map((item) => <article key={item}><span className="library-loading-cover" /><span className="library-loading-copy"><i /><i /><i /></span><span className="library-loading-progress"><i /><i /></span><span className="library-loading-cell" /><span className="library-loading-cell short" /></article>)}
      </section>}
  </div>;
}

// A catalog that has not arrived yet, as opposed to a backend that could not
// supply one: offline keeps its own fallback rather than a spinner that never ends.
export function catalogPending(catalog, backendStatus) {
  return !catalog && backendStatus !== "offline";
}

export function LibraryViewSheet({ view, onView, sort, onSort, followingOnly, onFollowingOnly, inProgressOnly, onInProgressOnly, groupArcRuns = true, onGroupArcRuns = null, showCollections = true, onShowCollections = null, scopeItems = [], scope, onScope, onClose }) {
  const phone = usePhoneWidth();
  // Above the phone width the same controls sit in a drawer from the right,
  // as a run's details do; on a phone they are a sheet from the bottom.
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(phone ? onClose : requestClose);
  const controls = <>
      <header>
        <h2 id="library-sheet-title">View &amp; sort</h2>
        <button type="button" className="glass-button glass-button--icon library-sheet-close"
          onClick={(event) => (phone ? slideSheetAway(event.currentTarget.closest(".library-sheet"), onClose) : requestClose())} aria-label="Close"><X size={20} /></button>
      </header>
      {/* The desktop tools row's controls: segmented, with the sliding thumb. */}
      {scopeItems.length > 1 ? <fieldset>
        <legend>Show</legend>
        <GlassSegmented label="Show" value={scope} onChange={onScope} className="library-sheet-segmented" items={scopeItems} />
      </fieldset> : null}
      {scope === "runs" ? <fieldset>
        <legend>View</legend>
        <GlassSegmented label="View" value={view} onChange={onView} className="library-sheet-segmented"
          items={[{ id: "grid", label: "Grid", icon: <GridViewIcon /> }, { id: "list", label: "List", icon: <ListViewIcon /> }]} />
      </fieldset> : null}
      <fieldset>
        <legend>Sort by</legend>
        <div className="library-sheet-options" role="radiogroup" aria-label="Sort by">
          {SORT_OPTIONS.map((option) => <button type="button" role="radio" aria-checked={option.value === sort} onClick={() => onSort(option.value)} key={option.value}>
            {option.label}
            {option.value === sort ? <Check size={18} weight="bold" aria-hidden="true" /> : null}
          </button>)}
        </div>
      </fieldset>
      {scope === "runs" ? <FollowSwitch following={followingOnly} label="Following only" onChange={onFollowingOnly} /> : null}
      {scope === "runs" && onShowCollections ? <FollowSwitch following={showCollections} label="Show collections in Library" onChange={onShowCollections} /> : null}
      {scope !== "collections" ? <FollowSwitch following={inProgressOnly} label="In progress only" onChange={onInProgressOnly} /> : null}
      {scope === "runs" && onGroupArcRuns ? <fieldset>
        <legend>Story arcs</legend>
        <FollowSwitch following={groupArcRuns} label="Collect story arc issues" onChange={onGroupArcRuns} />
        <p className="library-sheet-hint">Issues only associated with a story arc will be collected and not displayed as a separate run.</p>
      </fieldset> : null}
      <button type="button" className="library-sheet-done" onClick={phone ? onClose : requestClose}>Done</button>
  </>;
  if (phone) {
    return <div className="modal-backdrop library-sheet-backdrop" onMouseDown={onClose}>
      <section className="library-sheet" role="dialog" aria-modal="true" aria-labelledby="library-sheet-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}>
        <SheetGrabber onClose={onClose} detents pullAnywhere />
        {controls}
      </section>
    </div>;
  }
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer library-drawer ${closing ? "closing" : ""}`} role="dialog" aria-modal="true" aria-labelledby="library-sheet-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}>
      <section className="library-sheet library-sheet--drawer">{controls}</section>
    </aside>
  </div>;
}

// The same map for story arcs: where each saved arc was left.
export function useListReading(version) {
  const [lists, setLists] = useState(() => lastAnswer("/api/v1/reading/lists")?.lists ?? null);
  useEffect(() => {
    let live = true;
    apiRequest("/api/v1/reading/lists")
      .then((data) => { if (live) setLists(data.lists || {}); })
      .catch(() => { if (live) setLists((current) => current || {}); });
    return () => { live = false; };
  }, [version]);
  return lists;
}

// The story arcs saved to read across runs. Null until answered, so the grid
// does not draw a Story arcs scope and then take it away. Each carries the
// one publisher its runs share, if they do, for its card's byline.
export function useReadingLists(version, series = null) {
  const [lists, setLists] = useState(() => lastAnswer("/api/v1/reading-lists")?.lists ?? null);
  useEffect(() => {
    let live = true;
    apiRequest("/api/v1/reading-lists")
      .then((data) => { if (live) setLists(data.lists || []); })
      .catch(() => { if (live) setLists((current) => current || []); });
    return () => { live = false; };
  }, [version]);
  return useMemo(() => {
    if (!lists || !series) return lists;
    const byId = new Map(series.map((run) => [String(run.id), run]));
    return lists.map((list) => ({ ...list, publisher: arcPublisher(list, byId) }));
  }, [lists, series]);
}

// Comics' tabs, as Plex and Komga have them. Recommended is the page's home.
export const COMICS_TABS = [
  { id: "recommended", label: "Recommended" }, { id: "all", label: "Library" }, { id: "collections", label: "Collections" },
  { id: "arcs", label: "Story arcs" }, { id: "reading", label: "Reading list" },
];

export function LibraryView({ tab = "", onTab, readingList = EMPTY_ENTRIES, onToggleReadingList, onNavigate, onOpenSeries, onOpenCollection, onOpenRunCollection, onNewRunCollection, onOpenList, onImportList, onNewArc, onSearch, onRead, readingVersion, listsVersion = 0, catalog, backendStatus }) {
  const libraryViewer = useViewer();
  const libraryAdmin = useManages();
  const [viewSheetOpen, setViewSheetOpen] = useState(false);
  // The page's own search: it narrows what you already have, as you type.
  const [query, setQuery] = useState("");
  const queryParts = useMemo(() => searchQueryParts(query), [query]);
  const searching = query.trim().length > 0;
  // How the grid was left last time, in this browser. Recent and covers by
  // default: covers are the point of a comic library, and Recent is Kindle's
  // answer to "where was I" -- one grid, with what you are reading on top.
  const [prefs] = useState(loadLibraryPrefs);
  const [view, setView] = useState(prefs.view);
  const [scope, setScope] = useState(prefs.scope);
  const [sort, setSort] = useState(prefs.sort);
  const [followingOnly, setFollowingOnly] = useState(prefs.followingOnly);
  const [inProgressOnly, setInProgressOnly] = useState(prefs.inProgressOnly);
  const [groupArcRuns, setGroupArcRuns] = useState(prefs.groupArcRuns);
  const [showCollections, setShowCollections] = useState(prefs.showCollections);
  useEffect(() => {
    saveLibraryPrefs({ view, scope, sort, followingOnly, inProgressOnly, groupArcRuns, showCollections });
  }, [view, scope, sort, followingOnly, inProgressOnly, groupArcRuns, showCollections]);
  const fallbackSeries = backendStatus === "offline" ? DEMO_SERIES : [];
  const series = useMemo(() => logicalCatalogSeries(catalog, fallbackSeries), [catalog, backendStatus]);
  const families = useMemo(() => (catalog?.families || []).map((family) => ({ ...family, runCount: family.runs?.length || family.runCount || 0 })), [catalog?.families]);
  const runReading = useRunReading(readingVersion);
  const readingLists = useReadingLists(listsVersion, series);
  const listReading = useListReading(readingVersion);
  // Nothing is drawn until what decides its order is in: the catalog, and
  // for Recent and In progress the reading map as well.
  const initialLoading = (backendStatus === "loading" && !catalog) || (runReading === null && (sort === "recent" || inProgressOnly))
    || (readingLists === null && groupArcRuns);
  // The server has always reported this; nothing read it, so arriving during a
  // scan showed the "no comics yet" empty state on a library that was filling.
  const activeScan = catalog?.activeScan || null;
  const editionsOn = Boolean(catalog?.collectedEditionsEnabled);
  // Story arcs are a tab of their own, as Collections are.
  const effectiveScope = scope === "families" && editionsOn ? "families" : "runs";
  const scopeItems = [
    { id: "runs", label: "Runs", icon: <ListBullets size={17} /> },
    ...(editionsOn ? [{ id: "families", label: "Series families", icon: <Books size={17} /> }] : []),
  ];
  const scopedSeries = useMemo(() => editionsOn ? series : series.filter((item) => !item.isCollectionSeries), [editionsOn, series]);
  // A run kept only for a story arc -- not followed, every owned issue an
  // arc's -- is the arc's card, as a confirmed collection's runs are the
  // collection's. A search looks at everything, folded or not.
  const folded = useMemo(() => groupArcRuns && !searching ? foldArcRuns(scopedSeries, readingLists || []) : null, [groupArcRuns, searching, scopedSeries, readingLists]);
  const gridSeries = folded ? folded.series : scopedSeries;
  const gridArcs = folded ? folded.arcs : [];
  // One reading map for the mixed shelf: runs by their id, arcs by the card's.
  const gridReading = useMemo(() => ({
    ...(runReading || {}), ...Object.fromEntries(gridArcs.map((arc) => [arc.id, listReading?.[arc.listId]]).filter(([, place]) => place)),
  }), [runReading, listReading, gridArcs]);
  // Fetched before the filters and the sort need it: In progress and Recent
  // both read from where each run was left, which is this map's to know.
  const followedSeries = useMemo(() => followingOnly ? gridSeries.filter((item) => item.monitoringStatus === "monitored") : [...gridSeries, ...gridArcs], [followingOnly, gridSeries, gridArcs]);
  const readingSeries = useMemo(() => inProgressOnly ? followedSeries.filter((item) => inProgress(gridReading?.[String(item.id)])) : followedSeries, [inProgressOnly, followedSeries, gridReading]);
  const filteredSeries = useMemo(() => searching ? readingSeries.filter((item) => libraryRunMatches(item, queryParts)) : readingSeries, [readingSeries, searching, queryParts]);
  const displayedSeries = useMemo(() => sortLibrary(filteredSeries, sort, gridReading), [filteredSeries, sort, gridReading]);
  // A search is the library's at every tab: typing on Recommended shows what matches.
  const showLibrary = tab === "all" || searching;
  // The household's collections, drawn from the runs this profile was sent.
  const collectionItems = useMemo(() => collectionCards(catalog?.runCollections, catalog?.series || []), [catalog?.runCollections, catalog?.series]);
  // They sit in the Library grid among the runs, as Plex shows "collections
  // and their items", sorted by the same sort as everything else (a pinned
  // first place ignored A-Z, owner 2026-10-03) -- unless the grid is
  // narrowed to followed or in-progress runs.
  const gridCollections = useMemo(() => effectiveScope === "runs" && showCollections && !followingOnly && !inProgressOnly
    ? collectionItems.filter((card) => card.runCount && collectionMatches(card, searching ? query : ""))
    : [], [effectiveScope, showCollections, followingOnly, inProgressOnly, collectionItems, searching, query]);
  const gridItems = useMemo(() => libraryGridOrder(displayedSeries, gridCollections, sort, gridReading, runReading),
    [gridCollections, displayedSeries, sort, gridReading, runReading]);
  // The View & sort button marks when the library isn't showing its default.
  const viewCustomized = view !== LIBRARY_DEFAULTS.view || sort !== LIBRARY_DEFAULTS.sort || followingOnly || inProgressOnly || effectiveScope !== "runs" || !groupArcRuns || !showCollections;
  const sortedFamilies = useMemo(() => {
    const needle = queryParts.title.toLowerCase();
    const matching = searching ? families.filter((family) => String(family.name || "").toLowerCase().includes(needle)) : families;
    return sortLibrary(matching, sort);
  }, [families, sort, searching, queryParts]);
  const sortedCollections = useMemo(() => sortLibrary(collectionItems, sort, collectionPlaces(collectionItems, runReading)), [collectionItems, sort, runReading]);
  // The A-Z rail: in Title A-Z, once there are more cards than a screen or two.
  const alphaOn = (items) => sort === "title" && (items?.length || 0) >= 20;
  // The Story arcs tab: the library's sort, over each arc's own reading map.
  const displayedLists = useMemo(() => sortLibrary((readingLists || []).map(listCard), sort, listReading), [readingLists, sort, listReading]);
  // What the tab shown does, at the far end of the tab row (Notion's and
  // Linear's views; Apple's "actions for the view at the trailing end"):
  // the title row keeps what is the whole app's -- search, the bell, the
  // profile. A phone's tabs fill their row, so there View & sort stays by
  // the bell and a tab's buttons sit above its grid (2026-10-01).
  const phone = usePhoneWidth();
  const viewButton = showLibrary ? <button
    type="button" className="glass-button glass-button--icon library-view-button"
    aria-label={viewCustomized ? "View and sort, changed from the default" : "View and sort"}
    aria-haspopup="dialog" aria-expanded={viewSheetOpen} onClick={() => setViewSheetOpen(true)}
  ><ViewOptionsIcon />{viewCustomized ? <span className="library-view-dot" aria-hidden="true" /> : null}</button> : null;
  const tabActions = phone ? null
    : showLibrary ? viewButton
    : tab === "collections" && collectionItems.length && onNewRunCollection ? <TabAction icon={<Plus size={17} />} label="New collection" onClick={onNewRunCollection} />
    : tab === "arcs" && (readingLists?.length || 0) > 0 ? <>
      {onNewArc ? <TabAction icon={<Plus size={17} />} label="New story arc" onClick={onNewArc} /> : null}
      {libraryAdmin && onImportList ? <TabAction icon={<UploadSimple size={17} />} label="Import a story arc" onClick={onImportList} /> : null}
    </> : null;
  return (
    <>
      {/* No field of its own: searching the library is search's, from the
          sidebar or the header's button, as everywhere else (2026-10-01). */}
      <PageHeader
        title="Comics"
        tools={<><SegmentedTabs label="Comics" items={COMICS_TABS} value={tab || "recommended"}
          onChange={(next) => onTab?.(next === "recommended" ? "" : next)} />
          {tabActions ? <div className="page-header-tab-actions" data-count={tab === "arcs" && onNewArc && libraryAdmin && onImportList ? 2 : 1}>{tabActions}</div> : null}</>}
        actions={phone ? viewButton : null}
      />
      {/* One button on every width (owner, 2026-09-29): view, sort and the
          filters are a sheet on a phone and a drawer on a desktop, with the
          same controls in each. The desktop's row of segmented controls and
          its glass capsule are gone. */}
      {viewSheetOpen ? <LibraryViewSheet
        view={view} onView={setView} sort={sort} onSort={setSort}
        followingOnly={followingOnly} onFollowingOnly={setFollowingOnly}
        inProgressOnly={inProgressOnly} onInProgressOnly={setInProgressOnly}
        groupArcRuns={groupArcRuns} onGroupArcRuns={(readingLists?.length || 0) > 0 ? setGroupArcRuns : null}
        showCollections={showCollections} onShowCollections={collectionItems.length ? setShowCollections : null}
        scopeItems={scopeItems} scope={effectiveScope} onScope={setScope}
        onClose={() => setViewSheetOpen(false)}
      /> : null}
      <div className="dashboard-body">
      {/* Sorted as the Story arcs tab is: the library's sort, "recently read" by a collection's latest run. */}
      {!showLibrary && tab === "collections" ? <AlphaScroller items={sortedCollections} enabled={alphaOn(sortedCollections)}>
        <CollectionsTab cards={sortedCollections}
        reading={runReading || {}} onOpen={onOpenRunCollection} onRead={onRead} onNew={onNewRunCollection} tools={phone} loading={catalogPending(catalog, backendStatus)} /></AlphaScroller> : null}
      {!showLibrary && tab === "reading" ? <ReadingListTab items={sortReadingList(readingListItems(readingList, {
          series, lists: readingLists || [], collections: collectionItems, runReading: runReading || {}, listReading: listReading || {},
        }), sort === "title" ? "title" : "added")} reading={runReading || {}} listReading={listReading || {}} loading={readingList === null || readingLists === null || catalogPending(catalog, backendStatus)}
        onOpen={(item) => item.kind === "collection" ? onOpenRunCollection(item) : item.kind === "arc" ? onOpenList({ ...item, id: item.listId }) : onOpenSeries(item)}
        onRead={onRead} onRemove={(kind, id) => onToggleReadingList?.(kind, id, false)} onBrowse={() => onTab?.("all")} /> : null}
      {!showLibrary && tab === "arcs" ? <AlphaScroller items={displayedLists} enabled={alphaOn(displayedLists)}>
        <ArcsTab lists={displayedLists} reading={listReading} loading={readingLists === null} admin={libraryAdmin} tools={phone}
        onOpen={onOpenList} onRead={onRead} onNew={onNewArc} onImport={onImportList} /></AlphaScroller> : null}
      {!showLibrary && tab !== "collections" && tab !== "reading" && tab !== "arcs" ? <RecommendedView series={series} catalog={catalog} backendStatus={backendStatus} readingVersion={readingVersion}
        onOpenSeries={onOpenSeries} onRead={onRead} onAdd={() => onNavigate("import")}
        // Sorted Recent, not filtered to In progress: what you are reading
        // rises to the top, and no filter is left behind to hide a later search.
        onSeeInProgress={() => { setScope("runs"); setSort("recent"); onTab?.("all"); }}
        onSeeAdded={() => { setScope("runs"); setSort("added"); onTab?.("all"); }} /> : null}
      {showLibrary ? <>
      {initialLoading ? <LibraryLoadingSkeleton view={view} /> : null}
      {!initialLoading && activeScan && !series.length ? <LibraryLoadingSkeleton scan={activeScan} view={view} /> : null}
      {!initialLoading && !(activeScan && !series.length) ? <>
      {backendStatus === "offline" ? <div className="backend-banner"><WarningCircle size={19} weight="fill" /> Showing sample comics because your library is unavailable.</div> : null}
      {effectiveScope === "families" ? (sortedFamilies.length ? <CollectionGroups families={sortedFamilies} onOpenCollection={onOpenCollection} /> : <CollectionEmpty query={query.trim()} />) : gridItems.length ? <AlphaScroller items={gridItems} enabled={alphaOn(gridItems)}><SeriesList series={gridItems} onOpen={(item) => item.kind === "collection" ? onOpenRunCollection(item) : item.kind === "arc" ? onOpenList({ ...item, id: item.listId }) : item.isCollectionSeries && editionsOn ? onOpenCollection(item.collection) : onOpenSeries(item)} onRead={onRead} reading={gridReading} view={view} /></AlphaScroller> : searching ? <div className="empty-state"><MagnifyingGlass size={35} weight="duotone" /><strong>No comics match “{query.trim()}”</strong><span>{can(libraryViewer, "discover.search") ? "The comic catalogs may have it." : "Try another title or creator."}</span>{can(libraryViewer, "discover.search") ? <button className="ghost-button" onClick={() => onSearch(query)}>Search the catalogs</button> : null}</div> : inProgressOnly ? <div className="empty-state"><BookOpen size={35} weight="duotone" /><strong>Nothing in progress</strong><span>Start a run and it shows up here.</span><button className="ghost-button" onClick={() => setInProgressOnly(false)}>Show all runs</button></div> : followingOnly ? <div className="empty-state"><CheckCircle size={35} weight="duotone" /><strong>No followed runs</strong><span>{libraryAdmin ? "Open any run and choose Follow run to monitor future issues." : "Open any run and choose Request follow to ask for its new issues."}</span><button className="ghost-button" onClick={() => setFollowingOnly(false)}>Show all runs</button></div> : <CatalogEmpty onAdd={() => onNavigate("import")} />}
      </> : null}
      </> : null}
      </div>
    </>
  );
}

export function CatalogEmpty({ onAdd }) {
  // Adding folders is the admin's, where the library is managed; a reader is
  // told what to expect instead.
  if (!useManages()) {
    return <div className="empty-state"><Books size={35} weight="duotone" /><strong>No comics yet</strong><span>Comics appear here once the library&rsquo;s admin adds them.</span></div>;
  }
  return <div className="empty-state"><Books size={35} weight="duotone" /><strong>Your library is ready for its first comics</strong><span>Choose a folder to scan without changing your files.</span><button className="primary-button" onClick={onAdd}><FolderOpen size={18} /> Import library</button></div>;
}
