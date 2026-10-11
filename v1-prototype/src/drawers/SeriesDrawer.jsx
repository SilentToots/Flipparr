import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiRequest } from "../api.js";
import { SeriesCover } from "../components/CoverArt.jsx";
import { StatusBadge } from "../components/StatusBadge";
import { LoadingSpinner, useGlassIndicator } from "../components/glass.jsx";
import { CollectionIcon, FollowedIcon } from "../design-icons.jsx";
import { useDialog, useDrawerExit, useExternalDismiss, useSwipeToDismiss } from "../dialogs.jsx";
import { identityKey, useCollectedEditions } from "../editions.js";
import { MonitoringStatus, Ownership, PublicationStatus } from "../library/cards.jsx";
import { ratingTone } from "../member-requests.js";
import { RATING_LABELS } from "../profiles.js";
import { RATING_SOURCES, runRating } from "../ratings.js";
import { EMPTY_ENTRIES, isOnReadingList } from "../reading-list-tab.js";
import { READING_STATES } from "../reading-target.js";
import { collectionsFor } from "../run-collections.js";
import { orderedCreators, relatedRuns } from "../run-details.js";

import { ComicDrawerCreators, ComicDrawerIssueCard, ComicDrawerRow, ComicDrawerRunCard, DrawerTopBar, ReadRunButton, ReadWithDownload, ReadingListToggle, RunSynopsis, StarRating, toneProps, useArtTone } from "./frame.jsx";
import { CollectionCoverage, CollectionManagement, EDIT_SECTIONS, FileInventory, GroupedIssueInventory, IssueCatalogCard, SeriesEditPanel, VolumeInventory } from "./inventory.jsx";
import { ArrowCounterClockwise, ArrowLeft, ArrowsClockwise, Books, CheckCircle, Hourglass, ImageSquare, PencilSimple, Plus, Trash } from "@phosphor-icons/react";
import { useManages, useImageSrc } from "../host.js";

export function SeriesDrawer({ series, families, allSeries, parentCollection, dismissSignal, onBack, onClose, onRead, onMarkIssue, onMarkRun, readingVersion = 0,
  coverBusy = false, coverError = "", onSelectSeriesCover, onUploadSeriesCover,
  backdropBusy = false, backdropError = "", onSaveBackdrop, onSetDirection, onRate, onRateIssue, onRequest, onViewRequests, requestBusy, onAddAlias, onSyncIssues, onFindRun, onMergeRun, onRebuildRun, rebuilding = false, rebuildResult = "", onCreateFamily, onSetFamily, onOpenWorkbench, onOpenCover, onChangeSeriesCover, onFixSeriesMatch, onOpenContents, onChangeRun, onEditIssue, onReplace, onUnfollow, unfollowBusy = false, onSetFormat, onRemove, onOpenSeries, onChangeBackdrop, backdropVersion = 0, requested = false, onSetAgeRating,
  runCollections = [], onOpenRunCollection, onAddToCollection,
  readingList = EMPTY_ENTRIES, onToggleReadingList, onAddToArc, onAddIssuesToArc }) {
  const admin = useManages();
  const imageSrc = useImageSrc();
  const inCollections = collectionsFor(series.id, runCollections);
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  const editionsOn = useCollectedEditions();
  useSwipeToDismiss(dialogRef, requestClose);
  useExternalDismiss(dismissSignal, requestClose);
  const [alias, setAlias] = useState("");
  const [savingAlias, setSavingAlias] = useState(false);
  const [aliasError, setAliasError] = useState("");
  const [syncingIssues, setSyncingIssues] = useState(false);
  const [syncError, setSyncError] = useState("");
  const [lastSyncResult, setLastSyncResult] = useState(null);
  const autoRepairKey = useRef(null);
  const [tab, setTab] = useState("overview");
  // null when not editing, "" for the list of sections, else the section. It
  // is drawer state, so opening another comic leaves editing behind.
  const [edit, setEdit] = useState(null);
  const [coverOptions, setCoverOptions] = useState(null);
  const loadCoverOptions = useCallback(() => {
    if (!series?.id) return;
    apiRequest(`/api/v1/series/${series.id}/cover`)
      .then(setCoverOptions).catch(() => setCoverOptions(null));
  }, [series?.id]);
  useEffect(() => {
    if (edit !== "cover") return;
    setCoverOptions(null);
    loadCoverOptions();
  }, [edit, loadCoverOptions]);
  // Asked for when the drawer opens: the blurb is not in the catalog, and
  // most runs are never opened.
  const [synopsis, setSynopsis] = useState({ state: "loading", text: null, source: null });
  useEffect(() => {
    if (!series?.id) return undefined;
    let live = true;
    setSynopsis({ state: "loading", text: null, source: null });
    apiRequest(`/api/v1/series/${series.id}/synopsis`)
      .then((data) => { if (live) setSynopsis({ state: "done", text: data.synopsis, source: data.providerName }); })
      .catch(() => { if (live) setSynopsis({ state: "done", text: null, source: null }); });
    return () => { live = false; };
  }, [series?.id]);
  // What can be read here, and where each comic was left. Its own request
  // rather than the catalog's: reading changes on every page turn, and the
  // catalog is refetched on scans and downloads, so folding one into the
  // other guarantees a progress bar that lies.
  const [reading, setReading] = useState(null);
  useEffect(() => {
    if (!series?.id) return undefined;
    let live = true;
    setReading(null);
    apiRequest(`/api/v1/series/${series.id}/reading`)
      .then((data) => { if (live) setReading(data); })
      .catch(() => { if (live) setReading(null); });
    return () => { live = false; };
  }, [series?.id, readingVersion]);
  const readingFiles = useMemo(() => Object.fromEntries(
    [...(reading?.issues || []), ...(reading?.volumes || [])].map((file) => [String(file.id), file]),
  ), [reading]);
  const issueCatalog = series?.issueCatalog || { status: "unknown" };
  const catalogKnown = ["complete", "complete_to_date"].includes(issueCatalog.status);
  const repairableIssueDetails = (series?.issues || []).filter((issue) => (
    (issueCatalog.titlePolicy !== "numbered_only" && !String(issue.title || "").trim() && issue.releaseState !== "upcoming")
    || issue.publicationYear == null
  )).length;
  const lastCheckedAge = issueCatalog.lastSyncedAt ? Date.now() - Date.parse(issueCatalog.lastSyncedAt) : Infinity;
  const currentRepairKey = `${series?.id || "none"}:${issueCatalog.lastSyncedAt || "never"}`;
  useEffect(() => {
    if (!series || !catalogKnown || !repairableIssueDetails || lastCheckedAge < 6 * 60 * 60 * 1000 || autoRepairKey.current === currentRepairKey) return;
    autoRepairKey.current = currentRepairKey;
    syncIssues(true);
  }, [series?.id, catalogKnown, repairableIssueDetails, currentRepairKey]);
  useEffect(() => {
    // Volumes and Collection disappear when collected editions are turned off.
    if (!editionsOn && (tab === "editions" || tab === "family")) setTab("overview");
  }, [editionsOn, tab]);
  // The comic drawer (node-id=72-1306): a page from the comic behind the
  // header -- the one the reader chose, or a spread found for them -- and the
  // runs here that share a maker or a publisher. The cover stands in when
  // there is no page to use, or the page will not load.
  const [backdrop, setBackdrop] = useState({ forId: null, url: null, source: null, fileId: null, page: null });
  const [backdropFailed, setBackdropFailed] = useState(false);
  useEffect(() => {
    if (!series?.id) return undefined;
    let live = true;
    setBackdropFailed(false);
    apiRequest(`/api/v1/series/${series.id}/backdrop`)
      .then((data) => { if (live) setBackdrop({ ...data, forId: series.id }); })
      .catch(() => { if (live) setBackdrop({ forId: series.id, url: null, source: "none", fileId: null, page: null }); });
    return () => { live = false; };
  }, [series?.id, backdropVersion]);
  const shownBackdrop = backdrop.forId === series?.id ? backdrop : null;
  const coverArt = series?.coverCandidates?.[0] || series?.cover || null;
  const pageArt = shownBackdrop?.url && !backdropFailed ? shownBackdrop.url : null;
  // Nothing until this run's answer arrives, so the last run's page never
  // flashes behind a new title.
  const heroArt = shownBackdrop ? pageArt || coverArt : null;
  const toned = toneProps(useArtTone(coverArt));
  const related = useMemo(() => (series ? relatedRuns(series, allSeries || []) : null), [series, allSeries]);
  const creators = useMemo(() => orderedCreators(series?.creators), [series?.creators]);
  // `edit` is a dependency because the strip is unmounted while editing: the
  // pill has to be measured again against the node that comes back.
  const [tabsRef, tabGlass] = useGlassIndicator("button.active", [tab, series?.id, editionsOn, edit]);
  useEffect(() => { dialogRef.current?.scrollTo?.({ top: 0 }); }, [series?.id]);
  if (!series) return null;
  const identityStrength = series.identityConfidence == null ? "Unknown" : series.identityConfidence >= 85 ? "High" : series.identityConfidence >= 65 ? "Medium" : "Low";
  async function saveAlias(event) {
    event.preventDefault();
    if (!alias.trim()) return;
    setSavingAlias(true);
    setAliasError("");
    const result = await onAddAlias(series.id, alias.trim());
    if (result.ok) setAlias("");
    else setAliasError(result.error);
    setSavingAlias(false);
  }
  async function syncIssues(automatic = false) {
    setSyncingIssues(true);
    setSyncError("");
    const result = await onSyncIssues(series.id, automatic);
    if (!result.ok) setSyncError(result.error);
    else setLastSyncResult(result.result);
    setSyncingIssues(false);
  }
  const groupedIssues = (series.issues || []).map((issue) => ({
    ...issue,
    contextRunId: series.id,
    contextLabel: series.title,
    contextYear: series.year,
    contextGroupName: series.title,
    contextType: "main",
  }));
  const isFollowing = series.monitoringStatus === "monitored";
  // Starting over is offered once the run has been started at all; before
  // that there is nothing to go back to.
  const restartFile = reading?.resume
    && [READING_STATES.continue, READING_STATES.next, READING_STATES.finished].includes(reading.resume.state)
    ? reading.issues?.find((file) => file.readable) || null
    : null;
  // Marking the whole run: read when any of it is not, unread once all of it is.
  const readableFiles = [...(reading?.issues || []), ...(reading?.volumes || [])].filter((file) => file.readable);
  const runAllRead = readableFiles.length > 0 && readableFiles.every((file) => file.finishedAt && !file.stale);
  // The run's own status in the hero, in the issue badge's words: "Read", or
  // "In progress" with how many are read, once any of it has been opened.
  const runReadCount = readableFiles.filter((file) => file.finishedAt && !file.stale).length;
  const runStarted = readableFiles.some((file) => file.pageCount && !file.stale);
  const runReadingBadge = runAllRead ? { tone: "green", text: "Read" }
    : runStarted ? { tone: "violet", text: `In progress · ${runReadCount} of ${readableFiles.length} ${readableFiles.length === 1 ? "comic" : "comics"} read` } : null;
  const wantedIssueCount = Math.max(0, Number(series.releaseSummary?.releasedMissing ?? series.unowned ?? Math.max(0, (series.total || 0) - (series.owned || 0))));
  // Volumes and Collection belong to collected-edition support; the counts
  // live on the tabs, so the header does not repeat them.
  const tabs = [
    ["overview", "Overview"],
    ["issues", `Issues (${series.issues?.length ?? 0})`],
    ...(editionsOn ? [["editions", `Volumes (${series.editions?.length ?? 0})`]] : []),
    // Files, Collection and Advanced are where a run is repaired: the admin's.
    ...(admin ? [["files", `Files (${series.fileDetails?.length ?? 0})`]] : []),
    ...(editionsOn && admin ? [["family", "Family"]] : []),
    ...(admin ? [["advanced", "Advanced"]] : []),
  ];
  const alternateTitles = (series.aliases || []).filter((item) => identityKey(item.name) !== identityKey(series.title));
  // A new tab's content slides in from the side it lies on, so moving right
  // reads as moving right. Nothing slides on the drawer's first render.
  const shownTab = useRef(tab);
  const tabMove = useRef("");
  if (shownTab.current !== tab) {
    const order = tabs.map(([id]) => id);
    tabMove.current = order.indexOf(tab) > order.indexOf(shownTab.current) ? "next" : "back";
    shownTab.current = tab;
  }
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}><aside className={`series-drawer comic-drawer${toned.className}${edit !== null ? " comic-drawer--editing" : ""} ${closing ? "closing" : ""}`} style={toned.style} ref={dialogRef} data-in-address="" role="dialog" aria-modal="true" aria-labelledby="series-drawer-title" onMouseDown={(event) => event.stopPropagation()}>
    <DrawerTopBar
      title={edit ? (EDIT_SECTIONS.find(([id]) => id === edit)?.[1] || series.title) : edit === "" ? "Edit run" : series.title}
      onClose={requestClose}
      onBack={edit !== null ? () => setEdit(edit ? "" : null) : parentCollection ? onBack : undefined}
      stacked={edit !== null}
      closeLabel="Close series details">
      {edit === null && onToggleReadingList ? <ReadingListToggle on={isOnReadingList(readingList, "run", series.id)} title={series.title}
        onChange={(on) => onToggleReadingList("run", series.id, on)} /> : null}
      {edit === null && admin ? <>
        <button type="button" className={`glass-button glass-button--icon comic-drawer-follow-button${isFollowing ? " active" : ""}`}
          onClick={() => (isFollowing ? onUnfollow(series) : onRequest())}
          disabled={requestBusy || unfollowBusy} aria-pressed={isFollowing}
          aria-label={isFollowing ? `Stop following ${series.title}` : `Follow ${series.title}`}
          title={isFollowing ? "Following" : "Follow run"}>
          {requestBusy || unfollowBusy ? <LoadingSpinner size={18} /> : <FollowedIcon size={20} />}
          <b>{isFollowing ? "Unfollow" : "Follow"}</b>
        </button>
        <button type="button" className="glass-button glass-button--icon comic-drawer-edit-button" onClick={() => setEdit("")} aria-label={`Edit ${series.title}`} title="Edit"><PencilSimple size={20} /></button>
      </> : null}
      {/* A reader asks for a run to be followed; the admin decides. Only
          where the host takes requests: Flipparr View passes no onRequest. */}
      {edit === null && !admin && !isFollowing && onRequest ? <button type="button"
        className={`glass-button glass-button--icon comic-drawer-follow-button${requested ? " active" : ""}`}
        onClick={() => onRequest()} disabled={requestBusy || requested}
        aria-label={requested ? `Following ${series.title} is requested` : `Request a follow of ${series.title}`}
        title={requested ? "Waiting for approval" : "Ask for this run to be followed: new issues as they come out"}>
        {requestBusy ? <LoadingSpinner size={18} /> : requested ? <Hourglass size={20} /> : <FollowedIcon size={20} />}
        <b>{requested ? "Follow requested" : "Request follow"}</b>
      </button> : null}
    </DrawerTopBar>
    <header className="comic-drawer-hero">
      {heroArt ? <><img className="comic-drawer-backdrop" src={imageSrc(heroArt)} alt="" aria-hidden="true" key={heroArt} onError={pageArt ? () => setBackdropFailed(true) : undefined} /><img className="comic-drawer-backdrop blurred" src={imageSrc(heroArt)} alt="" aria-hidden="true" key={`${heroArt}-blurred`} /></> : null}
      <span className="comic-drawer-scrim" aria-hidden="true" />
      {parentCollection ? <button type="button" className="drawer-back-link" onClick={onBack}><ArrowLeft size={17} /><span>Back to <strong>{parentCollection.name}</strong></span></button> : null}
      <div className="comic-drawer-identity">
        <div className="comic-drawer-cover">{onChangeSeriesCover && admin ? <button type="button" className="drawer-cover-button" onClick={() => onChangeSeriesCover(series)} aria-label={`Change the cover for ${series.title}`}><SeriesCover series={series} /><span className="drawer-cover-hint"><ImageSquare size={15} /> Change cover</span></button> : <SeriesCover series={series} />}</div>
        <div className="comic-drawer-copy">
          <div className="comic-drawer-titles">
            <h2 id="series-drawer-title">{series.title}</h2>
            <p>{[series.publisher, series.year].filter(Boolean).join(" • ")}</p>
          </div>
          <div className="comic-drawer-statuses"><PublicationStatus series={series} /><MonitoringStatus series={series} />{series.ageRating ? <StatusBadge tone={ratingTone(series.ageRating.replace("_", " "))}>Rated {RATING_LABELS[series.ageRating]}</StatusBadge> : null}{runReadingBadge ? <StatusBadge tone={runReadingBadge.tone}>{runReadingBadge.text}</StatusBadge> : null}{editionsOn && series.family ? <button type="button" className="family-link-chip" onClick={() => setTab("family")}><Books size={14} /> {series.family.name}</button> : null}</div>
          <Ownership series={series} compact />
          {isFollowing && wantedIssueCount && admin ? <button type="button" className="comic-drawer-link" onClick={onViewRequests}>View {wantedIssueCount} wanted issue{wantedIssueCount === 1 ? "" : "s"}</button> : null}
        </div>
      </div>
    </header>
    {/* Reading and following, together, above the tabs and on every one of
        them. They are what this drawer is for; everything below is detail. */}
    {edit !== null ? <div className="comic-drawer-body comic-drawer-edit">
      <SeriesEditPanel
       
        series={series} section={edit} onSection={setEdit} shownBackdrop={shownBackdrop} medium={series.medium}
        readingFiles={readingFiles}
        cover={coverOptions} coverBusy={coverBusy} coverError={coverError}
        onSelectCover={(source, url, fileId) => onSelectSeriesCover?.(source, url, fileId, series.id).then(loadCoverOptions)}
        onUploadCover={(file) => onUploadSeriesCover?.(file, series.id).then(loadCoverOptions)}
        backdropBusy={backdropBusy} backdropError={backdropError}
        onChooseBackdrop={(fileId, page) => onSaveBackdrop?.({ fileId, page }, "Header background updated", series.id)}
        onAutomaticBackdrop={() => onSaveBackdrop?.({ source: "auto" }, "Automatic background restored", series.id)}
        alternateTitles={alternateTitles} onFixMatch={onFixSeriesMatch} onSetFormat={onSetFormat}
        directionOverride={series.readingDirection} onSetDirection={onSetDirection} onSetAgeRating={onSetAgeRating}
        aliasForm={<form className="alias-form" onSubmit={saveAlias}><label><span>Add a title alias</span><div><input value={alias} onChange={(event) => setAlias(event.target.value)} placeholder="Alternate series title…" /><button disabled={savingAlias || !alias.trim()} aria-busy={savingAlias}>{savingAlias ? <LoadingSpinner size={18} /> : <Plus size={18} />} Add</button></div></label>{aliasError ? <small className="form-error" role="alert">{aliasError}</small> : null}</form>}
      />
    </div> : <>
    <div className="comic-drawer-actions">
      <ReadWithDownload group={{ kind: "run", id: String(series.id), title: series.title }}>
        <ReadRunButton reading={reading} title={series.title} medium={series.medium} onRead={onRead} />
      </ReadWithDownload>
    </div>
    <nav className="drawer-tabs comic-drawer-tabs" aria-label="Series details" ref={tabsRef}><span className="comic-drawer-tab-glass glass-indicator" aria-hidden="true" style={tabGlass || { opacity: 0 }} />{tabs.map(([id, label]) => <button type="button" className={tab === id ? "active" : ""} aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)} key={id}>{label}</button>)}</nav>
    <div className="comic-drawer-body" key={tab} data-tab-move={tabMove.current || undefined}>
      {tab === "overview" ? <>
        {/* The stars sit here, not in the header: there they outweighed the cover on a phone. */}
        <section className="comic-drawer-rating" aria-label={runRating(series).source === RATING_SOURCES.average ? "Your issues' average" : "Your rating"}><h3>{runRating(series).source === RATING_SOURCES.average ? "Your issues\u2019 average" : "Your rating"}</h3><StarRating rating={runRating(series)} title={series.title} size={24} onRate={(value) => onRate?.(series, value)} /></section>
        <RunSynopsis loading={synopsis.state === "loading"} text={synopsis.text} source={synopsis.source} sourcePrefix="Source:" key={series.id} />
        {series.issues?.length ? <ComicDrawerRow title="Issues" count={series.issues.length}>{series.issues.map((issue) => <ComicDrawerIssueCard issue={issue} medium={series.medium} onOpen={() => setTab("issues")} onRead={onRead} readingFiles={readingFiles} key={issue.id || issue.number} />)}</ComicDrawerRow> : null}
        {creators.length ? <ComicDrawerCreators creators={creators} key={`creators-${series.id}`} /> : null}
        {/* Its collections, and "Add to collection" under them, as an arc's
            shelf has "Add issues" (the same sheet an issue's "…" opens). */}
        {inCollections.length ? <div className="comic-drawer-shelf-group">
          <ComicDrawerRow title="In Collections" count={inCollections.length}>{inCollections.map((card) => <ComicDrawerRunCard
            run={{ id: card.key, title: card.name, cover: card.cover, coverCandidates: card.coverCandidates }}
            onOpen={() => onOpenRunCollection?.(card)} key={card.key} />)}</ComicDrawerRow>
          {onAddToCollection ? <button type="button" className="glass-button comic-drawer-add" onClick={() => onAddToCollection(series)}><CollectionIcon size={18} /> Add to collection</button> : null}
        </div> : onAddToCollection ? <button type="button" className="glass-button comic-drawer-add" onClick={() => onAddToCollection(series)}><CollectionIcon size={18} /> Add to collection</button> : null}
        {related?.moreBy ? <ComicDrawerRow title={`More From ${related.moreBy.name}`} count={related.moreBy.runs.length}>{related.moreBy.runs.map((run) => <ComicDrawerRunCard run={run} onOpen={(item) => onOpenSeries?.(item)} key={run.id} />)}</ComicDrawerRow> : null}
        {related?.publisher ? <ComicDrawerRow title={`More From ${related.publisher.name}`} count={related.publisher.runs.length}>{related.publisher.runs.map((run) => <ComicDrawerRunCard run={run} onOpen={(item) => onOpenSeries?.(item)} key={run.id} />)}</ComicDrawerRow> : null}
      </> : null}
      {tab === "issues" ? <GroupedIssueInventory issues={groupedIssues} onEditIssue={admin ? onEditIssue : undefined} onRead={onRead} onRate={onRateIssue} onMark={onMarkIssue} onAddToArc={onAddToArc} onAddIssuesToArc={onAddIssuesToArc ? (chosen) => onAddIssuesToArc(chosen, series.title) : undefined} onMarkRun={onMarkRun ? (read) => onMarkRun(series, read) : undefined} runRead={runAllRead} runStarted={runStarted} readingFiles={readingFiles} medium={series.medium} /> : null}
      {tab === "editions" && editionsOn ? <VolumeInventory editions={series.editions} /> : null}
      {tab === "files" ? <FileInventory files={series.fileDetails} readingFiles={readingFiles} onRead={onRead} onOpenWorkbench={onOpenWorkbench} onOpenCover={onOpenCover} onOpenContents={onOpenContents} onChangeRun={onChangeRun} onReplace={onReplace} /> : null}
      {tab === "family" && editionsOn ? <CollectionManagement series={series} families={families} allSeries={allSeries} onCreateFamily={onCreateFamily} onSetFamily={onSetFamily} /> : null}
      {tab === "advanced" ? <div className="advanced-tools">
        {/* Moved off the header: useful when repairing a run, noise when reading one. */}
        <div className="drawer-facts"><span><strong>{series.fileDetails?.length ?? series.owned}</strong>Comic files</span><span><strong>{series.inventory?.directIssueFiles ?? 0}</strong>Single issues</span>{editionsOn ? <span><strong>{series.inventory?.editionCount ?? series.editions?.length ?? 0}</strong>Volumes</span> : null}<span><strong>{identityStrength}</strong>Match confidence</span></div>
        {/* Single issues are already a fact above; the volume split only means something with editions on. */}
        {editionsOn ? <CollectionCoverage series={series} editionsOn={editionsOn} /> : null}
        <IssueCatalogCard series={series} catalogKnown={catalogKnown} syncing={syncingIssues} error={syncError} lastResult={lastSyncResult} onSync={syncIssues} onReviewFiles={() => setTab("files")} onFindRun={onFindRun} />
        {/* Covers, titles and the match moved into Edit. Advanced keeps what
            repairs a run rather than what dresses it. */}
        <section className="advanced-card">
          <div><strong>Edit this run</strong><p>Its cover, header background, alternate titles, how it is filed, and the run it is matched to.</p></div>
          <button type="button" onClick={() => setEdit("")}><PencilSimple size={16} /> Edit</button>
        </section>
        {restartFile && onRead ? <section className="advanced-card">
          <div><strong>Start from the beginning</strong><p>Opens the run&rsquo;s first issue from its first page. Your place in the run moves once you turn a page there.</p></div>
          <button type="button" onClick={() => onRead({ id: restartFile.id, fromStart: true })}><ArrowCounterClockwise size={16} /> Restart</button>
        </section> : null}
        {readableFiles.length && onMarkRun ? <section className="advanced-card">
          <div><strong>Reading state</strong><p>{runAllRead
            ? "Every issue is read. Marking the run unread forgets your place in all of them, for your profile only."
            : runStarted
              ? "Some of this run is read or in progress. Mark it all read, or unread to forget every place -- someone else's reading on your profile included."
              : "Marks every issue of this run read, for your profile only. Each issue's menu does the same for one."}</p></div>
          <div className="advanced-card-actions">
            {!runAllRead ? <button type="button" onClick={() => onMarkRun(series, true)}><CheckCircle size={16} /> Mark read</button> : null}
            {runAllRead || runStarted ? <button type="button" onClick={() => onMarkRun(series, false)}><ArrowCounterClockwise size={16} /> Mark unread</button> : null}
          </div>
        </section> : null}
        <section className="advanced-card">
          <div><strong>Combine duplicate run</strong><p>One run split into two entries? Merge them. Files on disk aren&rsquo;t changed.</p></div>
          <button type="button" onClick={() => onMergeRun(series)}><Books size={16} /> Combine</button>
        </section>
        <section className="advanced-card">
          <div><strong>Rebuild this run</strong><p>Clear the titles, dates and covers Flipparr worked out, and work them out again from these files. Your corrections are kept.</p>{rebuildResult ? <small className="rebuild-result">{rebuildResult}</small> : null}</div>
          <button type="button" onClick={() => onRebuildRun(series)} disabled={rebuilding}>{rebuilding ? <LoadingSpinner size={16} /> : <ArrowsClockwise size={16} />} {rebuilding ? "Rebuilding…" : "Rebuild"}</button>
        </section>
        <section className="advanced-card danger">
          <div><strong>Remove from library</strong><p>Removes the run and deletes its comic files from disk. You&rsquo;ll see what goes first. This can&rsquo;t be undone.</p></div>
          <button type="button" onClick={() => onRemove?.(series)}><Trash size={16} /> Remove</button>
        </section>
      </div> : null}
    </div>
    </>}
  </aside></div>;
}
