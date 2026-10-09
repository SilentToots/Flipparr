import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiRequest } from "../api.js";
import { DiscoverCover } from "../components/CoverArt.jsx";
import { StatusBadge } from "../components/StatusBadge";
import { LoadingSpinner, useGlassIndicator } from "../components/glass.jsx";
import { useDialog, useDrawerExit, useSwipeToDismiss } from "../dialogs.jsx";
import { identityKey } from "../editions.js";
import { Ownership, arcOwnership } from "../library/cards.jsx";

import { EMPTY_ENTRIES, isOnReadingList } from "../reading-list-tab.js";
import { ARC_SORTS, arcOwnerLine, arcYears } from "../reading-list.js";
import { arcPublisher } from "../run-details.js";

import { ComicDrawerIssueCard, ComicDrawerRow, ComicDrawerRunCard, DrawerTabs, DrawerTopBar, PullCardSkeleton, ReadRunButton, ReadWithDownload, ReadingListToggle, RunSynopsis, formatLongDate, toneProps, useArtTone } from "./frame.jsx";
import { BackdropPicker, GroupArrange, GroupArrangeCard, GroupCoverPicker, GroupDeleteCard, GroupEditScreen, GroupReadingCards, GroupShareCard, useGroupBackdrop } from "./groups.jsx";
import { FileInventory, GroupedIssueInventory, downloadFrom } from "./inventory.jsx";
import { ArrowsClockwise, CloudArrowDown, DownloadSimple, ImageSquare, ListNumbers, PencilSimple, Plus, WarningCircle } from "@phosphor-icons/react";
import { useManages, useImageSrc } from "../host.js";

// A saved story arc, drawn as a run is: the same top bar, hero, Read button,
// tabs, issue tiles and menus, files and Advanced tools -- so knowing one
// drawer is knowing the other (owner, 2026-09-28). Its issues are in the
// household's order across runs; each says whether it is here, on the way
// or missing, and whether this profile has read it. The admin can rename it,
// choose its cover from its own comics, put the issues in another order or
// take some out, refresh it from where it came, pull what is missing, or
// delete it.
export function ReadingListDrawer({ list, allSeries = [], readingVersion = 0, readingList = EMPTY_ENTRIES, onToggleReadingList, onClose, onRead, onOpenSeries, onMarkIssue, onRateIssue, onEditIssue,
  onPullMissing, onRequestMissing, onRefresh, onDelete, onMark, onChanged, onAddToArc, onGone, onAddIssues,
  onOpenWorkbench, onOpenCover, onOpenContents, onChangeRun, onReplace, groupTools = true }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  useSwipeToDismiss(dialogRef, requestClose);
  const admin = useManages();
  const imageSrc = useImageSrc();
  const [detail, setDetail] = useState({ state: "loading", data: null, error: "" });
  const [tab, setTab] = useState("overview");
  // null: reading; "": Edit; "cover" | "backdrop": a picker, a step in -- as a collection's.
  const [edit, setEdit] = useState(null);
  const [busy, setBusy] = useState("");
  const load = useCallback(async () => {
    try {
      setDetail({ state: "done", data: await apiRequest(`/api/v1/reading-lists/${list.id}`), error: "" });
    } catch (error) {
      setDetail({ state: "error", data: null, error: error.message });
    }
  }, [list.id]);
  useEffect(() => { load(); }, [load, readingVersion]);
  const data = detail.data;
  const items = data?.items || [];
  const name = data?.name || list.name;
  // Whose the arc is decides who changes it: the household's are the
  // admin's, a hand-made one its maker's. The admin may also take a shared
  // one back out of the household's view, or delete it.
  // `groupTools`: the arc's own tools -- Edit, Arrange, Share, Export and
  // Delete -- where the host offers them. Flipparr View reads arcs and
  // changes nothing, as with collections (it passes groupTools={false}).
  const canEdit = Boolean(data?.editable) && groupTools;
  const canManage = Boolean(data?.manageable) && groupTools && Boolean(onDelete);
  const ownerLine = arcOwnerLine(data || list);
  const context = { listId: list.id, listName: name };
  // Until the detail is in, the card the drawer opened from already knows
  // the arc's names and counts, so the frame is drawn at once, as a run's
  // is, and the detail only fills it.
  const seriesNames = data ? [...new Set(items.map((issue) => issue.seriesTitle))] : (list.seriesTitles || []);
  const multiSeries = seriesNames.length > 1;
  const counts = {
    issues: data ? items.length : (list.issueCount ?? 0),
    owned: data ? (data.owned ?? 0) : (list.owned ?? 0),
    missing: data ? (data.missing ?? 0) : (list.missing ?? 0),
  };
  // Where the order came from: Metron, or a reading list someone made.
  const sourceHost = (() => { try { return data?.sourceUrl ? new URL(data.sourceUrl).hostname.replace(/^www\./, "") : ""; } catch { return ""; } })();
  const origin = data?.source === "cbl" ? `From a reading list${sourceHost ? ` · ${sourceHost}` : ""}` : data?.provider === "metron" ? "From Metron" : null;
  // The arc's comics as the reader knows them, keyed by file, in the shape
  // the run drawer's tiles and cards read (`issueReadState`).
  const readingFiles = useMemo(() => Object.fromEntries(items.filter((item) => item.fileId).map((item) => [String(item.fileId), {
    id: item.fileId, readable: item.readable, page: item.page, pageCount: item.pageCount, finishedAt: item.finishedAt, stale: item.stale,
  }])), [items]);
  const runsById = useMemo(() => Object.fromEntries((allSeries || []).map((run) => [String(run.id), run])), [allSeries]);
  const arcByline = useMemo(() => {
    const publisher = data?.items ? arcPublisher(data, new Map(Object.entries(runsById))) : list?.publisher || "";
    return [publisher, arcYears(data || list)].filter(Boolean).join(" • ");
  }, [data, list, runsById]);
  // Each item as a catalog issue: the run's own record when the issue is in
  // the library (so its title, date and rating are the real ones), else a
  // stand-in that says what is missing. Across several series the tile's
  // small line names the series, since "#16" alone would not.
  const issues = useMemo(() => items.map((item) => {
    const run = item.runId ? runsById[String(item.runId)] : null;
    const known = run?.issues?.find((issue) => String(issue.id) === String(item.issueId)) || null;
    const base = known || {
      id: item.issueId || `arc-item-${item.id}`, number: item.number, title: null, cover: item.cover, fileCover: item.fileCover,
      fileId: item.fileId, ownership: item.owned ? "direct" : "unowned", publicationDate: item.coverDate,
      releaseState: item.coverDate ? "released" : "unknown", acquisitionState: item.queued ? "queued" : undefined,
    };
    const ownTitle = known?.title && identityKey(known.title) !== identityKey(`Issue ${known.number}`) ? known.title : null;
    return {
      ...base, fileCover: base.fileCover || item.fileCover, cover: base.cover || item.cover,
      title: multiSeries ? [item.seriesTitle, ownTitle].filter(Boolean).join(" · ") : ownTitle,
      arcItemId: item.id, seriesTitle: item.seriesTitle, runId: item.runId, queued: item.queued,
      contextRunId: `arc-${list.id}`, contextLabel: name, contextGroupName: name, contextType: "arc",
    };
  }), [items, runsById, multiSeries, list.id, name]);
  const runsInArc = useMemo(() => [...new Set(items.map((item) => item.runId).filter(Boolean))].map((id) => runsById[String(id)]).filter(Boolean), [items, runsById]);
  const files = useMemo(() => {
    const wanted = new Set(items.map((item) => item.fileId).filter(Boolean).map(String));
    return runsInArc.flatMap((run) => (run.fileDetails || []).filter((file) => wanted.has(String(file.id))));
  }, [items, runsInArc]);
  const backdrop = useGroupBackdrop(`/api/v1/reading-lists/${list.id}`, list.id);
  const readableFiles = items.filter((item) => item.fileId && item.readable);
  const allRead = readableFiles.length > 0 && readableFiles.every((item) => item.finishedAt && !item.stale);
  const readCount = readableFiles.filter((item) => item.finishedAt && !item.stale).length;
  const started = readableFiles.some((item) => item.pageCount && !item.stale);
  const readingBadgeText = allRead ? { tone: "green", text: "Read" } : started ? { tone: "violet", text: `In progress · ${readCount} of ${readableFiles.length} read` } : null;
  const restartFile = readableFiles[0] || null;
  const coverArt = data?.cover || list.cover || null;
  const heroArt = backdrop.shown ? backdrop.page || coverArt : null;
  const toned = toneProps(useArtTone(coverArt));
  // A collection's tabs, with an arc's Issues for its Runs, and Files for the
  // admin; Arrange is in Advanced, as a collection's is.
  const tabs = [
    ["overview", "Overview"],
    ["issues", `Issues (${counts.issues})`],
    ...(admin ? [["files", `Files (${data ? files.length : counts.owned})`]] : []),
    ["advanced", "Advanced"],
  ];
  const [tabsRef, tabGlass] = useGlassIndicator("button.active", [tab, list.id, edit]);
  const shownTab = useRef(tab);
  const tabMove = useRef("");
  if (shownTab.current !== tab) {
    const ids = tabs.map(([id]) => id);
    tabMove.current = ids.indexOf(tab) > ids.indexOf(shownTab.current) ? "next" : "back";
    shownTab.current = tab;
  }
  async function run(kind, action) {
    if (busy) return;
    setBusy(kind);
    try { await action(); await load(); onChanged?.(); } finally { setBusy(""); }
  }
  async function patch(body) {
    await apiRequest(`/api/v1/reading-lists/${list.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }
  // A picture of your own: made a JPEG of bounded size on the server, as a
  // run's uploaded cover is, and kept among the choices after.
  const [uploadError, setUploadError] = useState("");
  async function uploadCover(file) {
    setUploadError("");
    try {
      await apiRequest(`/api/v1/reading-lists/${list.id}/cover/upload`, { method: "POST", headers: { "Content-Type": file.type }, body: file });
    } catch (failure) { setUploadError(failure.message); throw failure; }
  }
  const itemsById = useMemo(() => Object.fromEntries(items.map((item) => [String(item.id), item])), [items]);
  const itemName = (item) => `${item.seriesTitle} #${item.number}`;
  const coverChoices = [
    ...(data?.uploadedCover ? [{ id: data.uploadedCover, url: data.uploadedCover, label: "Your picture" }] : []),
    ...(data?.cover && data.cover.startsWith("http") ? [{ id: data.cover, url: data.cover, label: data.provider === "metron" ? "Metron's cover" : "The list's cover" }] : []),
    ...items.filter((item) => item.fileCover).map((item) => ({ id: item.fileCover, url: item.fileCover, label: `${item.seriesTitle} #${item.number}` })),
  ];
  const missingCount = counts.missing;
  // A reader asks for an arc by its Metron id; one made by hand asks issue
  // by issue, from each missing issue's run.
  // Only where the host takes requests: Flipparr View passes no handler.
  const canAskMissing = Boolean(admin ? onPullMissing : onRequestMissing) && (admin || Boolean(data?.providerArcId));
  const pullLabel = admin ? `Pull ${missingCount} missing` : `Request ${missingCount} missing`;
  const pullMissing = () => run("pull", () => (admin ? onPullMissing : onRequestMissing)(data));
  const EDIT_ROWS = [
    { id: "cover", label: "Cover", detail: "A picture you upload, the one it came with, or one of its comics' covers.", onOpen: () => setEdit("cover") },
    { id: "backdrop", label: "Header background", detail: "A page from one of its comics, behind the header.", disabled: !files.length, onOpen: () => setEdit("backdrop") },
  ];
  const arrangeView = data ? <GroupArrange noun="issue" groupNoun="arc" ids={items.map((item) => String(item.id))} sortMode={data.sortMode || "custom"} sortOptions={ARC_SORTS} busy={busy}
            onSortMode={(next) => run("sort", () => patch({ sortMode: next }))}
            onReorder={(order) => run("order", () => patch({ order }))}
            onRemove={(id) => run("remove", () => patch({ remove: [id] }))}
            nameOf={(id) => (itemsById[id] ? itemName(itemsById[id]) : "Issue")}
            rowClassName={(id) => (itemsById[id]?.owned ? "" : "arc-order-row--missing")}
            renderRow={(id, index) => {
              const issue = itemsById[id];
              if (!issue) return null;
              return <>
                <b className="source-order-position" aria-hidden="true">{index + 1}</b>
                <span className="rating-review-cover"><DiscoverCover src={issue.fileCover || issue.cover} alt="" glyph={16} /></span>
                <span><strong>{itemName(issue)}</strong>
                  <small>{[issue.seriesYear ? `${issue.seriesTitle} (${issue.seriesYear})` : null, formatLongDate(issue.coverDate), issue.owned ? "In library" : issue.queued ? "On the way" : "Missing"].filter(Boolean).join(" \u00b7 ")}</small></span>
              </>;
            }}
            onAdd={onAddIssues ? () => onAddIssues(data) : null} addLabel="Add issues"
            empty={<div className="drawer-empty"><ListNumbers size={26} weight="duotone" /><strong>Nothing to arrange yet</strong><span>Add issues from any run, owned or missing.</span>
              {onAddIssues ? <button type="button" className="ghost-button" onClick={() => onAddIssues(data)}><Plus size={17} /> Add issues</button> : null}</div>} /> : null;
  const advanced = <div className="advanced-tools">
    <div className="drawer-facts"><span><strong>{items.length}</strong>Issues</span><span><strong>{data?.owned ?? 0}</strong>In library</span><span><strong>{missingCount}</strong>Missing</span><span><strong>{seriesNames.length}</strong>Series</span></div>
    {canEdit && items.length ? <GroupArrangeCard noun="issue" count={items.length} sortMode={data?.sortMode || "custom"} sortOptions={ARC_SORTS} onOpen={() => setEdit("arrange")} /> : null}
    {origin ? <p className="settings-card-note drawer-provenance">{origin}</p> : null}
    {groupTools ? <GroupShareCard group={data} noun="story arc" busy={busy}
      onShare={(on) => run("share", () => patch({ shared: on }))}
      onStopSharing={async () => {
        // Unshared, it is no longer the admin's to see: the drawer closes
        // rather than reloading into "not in the library".
        setBusy("share");
        try { await patch({ shared: false }); onGone?.(`${name} is no longer shared with the household`); } catch (failure) { setBusy(""); window.alert(failure.message); }
      }} /> : null}
    <GroupReadingCards noun="story arc" firstLabel="the arc’s first comic"
      onRestart={restartFile && onRead ? () => onRead({ id: restartFile.fileId, fromStart: true, ...context }) : null}
      allRead={allRead} anyRead={started} busy={busy}
      onMark={readableFiles.length && onMark ? (read) => run("mark", () => onMark(list, read)) : null} />
    {missingCount && canAskMissing ? <section className="advanced-card">
      <div><strong>{pullLabel}</strong><p>{admin ? "Asks for every issue of the arc that is not here and not on its way, one pull per series." : "Asks the admin for the issues of this arc that are not here yet."}</p></div>
      <button type="button" onClick={pullMissing} disabled={busy === "pull"}><CloudArrowDown size={16} /> {admin ? "Pull" : "Request"}</button>
    </section> : null}
    {admin && onRefresh && (data?.providerArcId || data?.sourceUrl) ? <section className="advanced-card">
      <div><strong>{data.providerArcId ? "Refresh from Metron" : "Refresh from its link"}</strong><p>Takes new details and any new issues, slotted in after their neighbours. Your order is kept, and nothing you took out comes back.</p></div>
      <button type="button" onClick={() => run("refresh", () => onRefresh(data))} disabled={busy === "refresh"}>{busy === "refresh" ? <LoadingSpinner size={16} /> : <ArrowsClockwise size={16} />} Refresh</button>
    </section> : null}
    {items.length && groupTools ? <section className="advanced-card">
      <div><strong>Export as a reading list</strong><p>A CBL file in this order, with each issue&rsquo;s catalog ids, that Kavita, Komga and Mylar import. JSON is the Comic Reading List standard.</p></div>
      <div className="advanced-card-actions">
        <button type="button" onClick={() => downloadFrom(`/api/v1/reading-lists/${list.id}/export?format=cbl`)}><DownloadSimple size={16} /> CBL</button>
        <button type="button" onClick={() => downloadFrom(`/api/v1/reading-lists/${list.id}/export?format=json`)}><DownloadSimple size={16} /> JSON</button>
      </div>
    </section> : null}
    {canManage ? <GroupDeleteCard group={data} noun="story arc" name={name} onDelete={() => onDelete(data || list)} /> : null}
  </div>;
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer comic-drawer${toned.className}${edit !== null ? " comic-drawer--editing" : ""} ${closing ? "closing" : ""}`} style={toned.style} ref={dialogRef}
      role="dialog" aria-modal="true" aria-labelledby="reading-list-title" onMouseDown={(event) => event.stopPropagation()}>
      <DrawerTopBar
        title={edit === "arrange" ? "Arrange" : edit ? (EDIT_ROWS.find((row) => row.id === edit)?.label || name) : edit === "" ? "Edit story arc" : name}
        onClose={requestClose}
        onBack={edit !== null ? () => setEdit(edit && edit !== "arrange" ? "" : null) : undefined}
        stacked={edit !== null}
        closeLabel="Close story arc">
        {edit === null && onToggleReadingList ? <ReadingListToggle on={isOnReadingList(readingList, "arc", list.id)} title={name}
          onChange={(on) => onToggleReadingList("arc", list.id, on)} /> : null}
        {edit === null && data && missingCount && canAskMissing ? <button type="button" className="glass-button glass-button--icon comic-drawer-follow-button"
          onClick={pullMissing} disabled={busy === "pull"} aria-label={pullLabel} title={pullLabel}>
          {busy === "pull" ? <LoadingSpinner size={18} /> : <CloudArrowDown size={20} />}
          <b>{admin ? "Pull" : "Request"}</b>
        </button> : null}
        {edit === null && canEdit ? <button type="button" className="glass-button glass-button--icon comic-drawer-edit-button" onClick={() => setEdit("")} aria-label={`Edit ${name}`} title="Edit"><PencilSimple size={20} /></button> : null}
      </DrawerTopBar>
      <header className="comic-drawer-hero">
        {heroArt ? <><img className="comic-drawer-backdrop" src={imageSrc(heroArt)} alt="" aria-hidden="true" key={heroArt} onError={backdrop.page ? backdrop.onFail : undefined} /><img className="comic-drawer-backdrop blurred" src={imageSrc(heroArt)} alt="" aria-hidden="true" key={`${heroArt}-blurred`} /></> : null}
        <span className="comic-drawer-scrim" aria-hidden="true" />
        <div className="comic-drawer-identity">
          <div className="comic-drawer-cover">{canEdit ? <button type="button" className="drawer-cover-button" onClick={() => setEdit("cover")} aria-label={`Change the cover for ${name}`}><DiscoverCover src={coverArt} alt={`${name} cover`} glyph={30} /><span className="drawer-cover-hint"><ImageSquare size={15} /> Change cover</span></button> : <DiscoverCover src={coverArt} alt={`${name} cover`} glyph={30} />}</div>
          <div className="comic-drawer-copy">
            <div className="comic-drawer-titles">
              <h2 id="reading-list-title">{name}</h2>
              {/* As its card and a run's "Publisher • year": the one publisher,
                  if it is one, then the years; what it is, a badge. The counts
                  are the ownership line's, the series its Runs shelf's. */}
              {arcByline ? <p>{arcByline}</p> : null}
            </div>
            <div className="comic-drawer-statuses">
              <StatusBadge tone="violet">Story arc</StatusBadge>
              {ownerLine ? <StatusBadge tone="muted">{ownerLine}</StatusBadge> : null}
              {data?.queued ? <StatusBadge tone="violet">{data.queued} on the way</StatusBadge> : null}
              {readingBadgeText ? <StatusBadge tone={readingBadgeText.tone}>{readingBadgeText.text}</StatusBadge> : null}
            </div>
            <Ownership series={arcOwnership({ owned: counts.owned, issueCount: counts.issues, missing: counts.missing })} compact />
          </div>
        </div>
      </header>
      {detail.state === "error" ? <div className="comic-drawer-body"><div className="shelf-message">
        <WarningCircle size={18} />
        <span><strong>This arc could not be opened</strong><small>{detail.error}</small></span>
        <button type="button" onClick={load}>Try again</button>
      </div></div> : null}
      {edit !== null && data ? <div className="comic-drawer-body comic-drawer-edit">
        {/* As a collection's: the name and description on the screen, the
            pickers a step in; the order is the Arrange tab's (2026-10-02). */}
        {edit === "" ? <GroupEditScreen name={name} summary={data.description} summaryLabel="Description" busy={busy} rows={EDIT_ROWS}
          onSave={({ name: nextName, summary: description }) => run("save", () => patch({ name: nextName, description })).then(() => setEdit(null), () => {})} /> : null}
        {edit === "cover" ? <GroupCoverPicker choices={coverChoices} current={coverArt} busy={busy} uploadError={uploadError}
          onUpload={(file) => run("upload", () => uploadCover(file)).then(() => setEdit(""), () => {})}
          onChoose={(choice) => run("cover", () => patch({ cover: choice.url })).then(() => setEdit(""), () => {})}
          empty={<div className="drawer-empty"><ImageSquare size={26} weight="duotone" /><strong>No covers to choose from yet</strong><span>The arc's covers come from the comics of it that are here.</span></div>} /> : null}
        {edit === "arrange" ? arrangeView : null}
        {edit === "backdrop" ? <>
          <p className="backdrop-workbench-intro">Choose a page from one of {name}&rsquo;s comics to show behind the drawer&rsquo;s header.</p>
          <BackdropPicker series={{ id: `arc-${list.id}`, title: name, fileDetails: files }} readable={readingFiles} current={backdrop.shown}
            busy={backdrop.busy} error={backdrop.error}
            onChoose={(fileId, page) => backdrop.save({ fileId, page }).then((done) => { if (done) setEdit(""); })}
            onAutomatic={() => backdrop.save({ source: "auto" }).then((done) => { if (done) setEdit(""); })} />
        </> : null}
      </div> : detail.state !== "error" ? <>
        <div className="comic-drawer-actions">
          <ReadWithDownload group={{ kind: "arc", id: String(list.id), title: name }}>
            {data ? <ReadRunButton reading={data} title={name} medium="comic" onRead={(target) => onRead({ ...target, ...context })} /> : null}
          </ReadWithDownload>
        </div>
        {/* The labels come from the card and do not change when the detail
            lands, so nothing settles under the glass pill once it is placed. */}
        <DrawerTabs tabs={tabs} tab={tab} onTab={setTab} label="Story arc details" tabsRef={tabsRef} glass={tabGlass} disabled={(id) => !data && id !== "overview"} />
        <div className="comic-drawer-body" key={tab} data-tab-move={tabMove.current || undefined}>
          {!data ? <div role="status" aria-busy="true" aria-label={`Opening ${name}`}>
            <RunSynopsis loading heading="About" />
            <ComicDrawerRow title="Reading order" count={counts.issues}>{Array.from({ length: Math.min(6, Math.max(1, counts.issues)) }, (_, index) => <PullCardSkeleton key={index} />)}</ComicDrawerRow>
          </div> : null}
          {data && tab === "overview" ? <>
            {data.description ? <RunSynopsis text={data.description} heading="About" source={data.source === "manual" ? null : data.source === "cbl" ? (data.sourceName || "the reading list") : "Metron"} /> : null}
            {!items.length ? <div className="drawer-empty"><ListNumbers size={26} weight="duotone" /><strong>Nothing in this arc yet</strong><span>{canEdit ? "Add issues from any run, owned or missing. Each goes on the end; arrange them in Advanced." : "Its issues show up here once they are added."}</span>
              {canEdit && onAddIssues ? <button type="button" className="ghost-button" onClick={() => onAddIssues(data)}><Plus size={17} /> Add issues</button> : null}</div> : null}
            {/* The band above holds Read alone, as a run's does; adding is
                done where the issues are, under their shelf. */}
            {items.length ? <div className="comic-drawer-shelf-group">
              <ComicDrawerRow title="Reading order" count={issues.length}>{issues.map((issue) => <ComicDrawerIssueCard issue={issue} medium="comic" onOpen={() => setTab("issues")} onRead={onRead ? (target) => onRead({ ...target, ...context }) : undefined} readingFiles={readingFiles} key={issue.id} />)}</ComicDrawerRow>
              {canEdit && onAddIssues ? <button type="button" className="glass-button comic-drawer-add" onClick={() => onAddIssues(data)}><Plus size={18} /> Add issues</button> : null}
            </div> : null}
            {runsInArc.length && onOpenSeries ? <ComicDrawerRow title="Runs in this arc" count={runsInArc.length}>{runsInArc.map((run) => <ComicDrawerRunCard run={run} onOpen={(item) => onOpenSeries(item)} key={run.id} />)}</ComicDrawerRow> : null}
          </> : null}
          {data && tab === "issues" ? <GroupedIssueInventory issues={issues} onEditIssue={admin ? (issue) => (issue.runId ? onEditIssue?.(issue) : undefined) : undefined}
            onRead={onRead ? (target) => onRead({ ...target, ...context }) : undefined} onRate={onRateIssue} onMark={onMarkIssue} onAddToArc={onAddToArc}
            empty={<div className="drawer-empty"><ListNumbers size={26} weight="duotone" /><strong>Nothing in this arc yet</strong><span>{canEdit ? "Add issues from any run to start its reading order." : "Its issues show up here once they are added."}</span></div>}
            onMarkRun={onMark ? (read) => run("mark", () => onMark(list, read)) : undefined} runRead={allRead} runStarted={started} readingFiles={readingFiles} medium="comic" /> : null}
          {data && tab === "files" ? <FileInventory files={files} readingFiles={readingFiles} onRead={onRead} onOpenWorkbench={onOpenWorkbench} onOpenCover={onOpenCover} onOpenContents={onOpenContents} onChangeRun={onChangeRun} onReplace={onReplace} /> : null}
          {data && tab === "advanced" ? advanced : null}
        </div>
      </> : null}
    </aside>
  </div>;
}
