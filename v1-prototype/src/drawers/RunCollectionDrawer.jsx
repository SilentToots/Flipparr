import { useEffect, useMemo, useState } from "react";
import { apiRequest } from "../api.js";
import { DiscoverCover } from "../components/CoverArt.jsx";
import { StatusBadge } from "../components/StatusBadge";
import { useGlassIndicator } from "../components/glass.jsx";
import { CollectionIcon } from "../design-icons.jsx";
import { useDialog, useDrawerExit, useSwipeToDismiss } from "../dialogs.jsx";
import { Ownership, SeriesList, arcOwnership } from "../library/cards.jsx";
import { EMPTY_ENTRIES, isOnReadingList } from "../reading-list-tab.js";
import { groupOwnerLine } from "../reading-list.js";
import { readingVerb } from "../reading-target.js";
import { COLLECTION_SORTS, collectionReadTarget } from "../run-collections.js";
import { ComicDrawerRow, ComicDrawerRunCard, DrawerTabs, DrawerTopBar, ReadRunButton, ReadingListToggle, RunSynopsis, toneProps, useArtTone, useRunReading } from "./frame.jsx";
import { BackdropPicker, GroupArrange, GroupArrangeCard, GroupCoverPicker, GroupDeleteCard, GroupEditScreen, GroupReadingCards, GroupShareCard, RunCollectionRow, useGroupBackdrop } from "./groups.jsx";

import { ImageSquare, PencilSimple, Plus } from "@phosphor-icons/react";
import { useImageSrc } from "../host.js";

/**
 * A collection, in the comic drawers' frame as a story arc is (owner,
 * 2026-10-02): Overview, Runs, Arrange for whoever may change it, Advanced
 * for everyone; Edit is its name and summary, with Cover and Header
 * background a step in. Whose it is decides who changes it, as an arc's.
 */
export function RunCollectionDrawer({ card, readingVersion, readingList = EMPTY_ENTRIES, onToggleReadingList, onClose, onOpenSeries, onRead, onSave, onDelete, onAddRuns, onUploadCover, onMarkRuns }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  useSwipeToDismiss(dialogRef, requestClose);
  const imageSrc = useImageSrc();
  const reading = useRunReading(readingVersion);
  const [tab, setTab] = useState("overview");
  // null: reading; "": Edit; "cover" | "backdrop": a picker, a step in.
  const [edit, setEdit] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  async function act(kind, work) {
    setBusy(kind);
    setError("");
    try { await work(); return true; } catch (failure) { setError(failure.message); return false; } finally { setBusy(""); }
  }
  const canEdit = Boolean(card.editable);
  const runs = card.runs || [];
  const runIds = runs.map((run) => String(run.id));
  const runsById = useMemo(() => Object.fromEntries(runs.map((run) => [String(run.id), run])), [runs]);
  const issueTotals = runs.reduce((sum, run) => ({ owned: sum.owned + (Number(run.owned) || 0), total: sum.total + (Number(run.total) || 0) }), { owned: 0, total: 0 });
  const { run: readRun, started, finished, allRead, anyRead } = collectionReadTarget(card, reading);
  const [readTarget, setReadTarget] = useState(null);
  useEffect(() => {
    if (!readRun?.id) { setReadTarget(null); return undefined; }
    let live = true;
    apiRequest(`/api/v1/series/${readRun.id}/reading`)
      .then((data) => { if (live) setReadTarget({ ...data, runId: String(readRun.id) }); })
      .catch(() => { if (live) setReadTarget(null); });
    return () => { live = false; };
  }, [readRun?.id, readingVersion]);
  const backdrop = useGroupBackdrop(`/api/v1/run-collections/${card.id}`, card.id);
  const coverArt = card.cover || null;
  // A cover that cannot be fetched is no backdrop, rather than a broken image.
  const [artFailed, setArtFailed] = useState(null);
  const heroArt = backdrop.shown ? backdrop.page || (coverArt && artFailed !== coverArt ? coverArt : null) : null;
  const toned = toneProps(useArtTone(coverArt));
  const files = useMemo(() => runs.flatMap((run) => run.fileDetails || []), [runs]);
  const tabs = [["overview", "Overview"], ["runs", `Runs (${card.runCount})`], ["advanced", "Advanced"]];
  const [tabsRef, tabGlass] = useGlassIndicator("button.active", [tab, card.id, edit]);
  // The cover is the uploaded picture, a run's cover chosen for it, or the first run's.
  const chosenCover = card.coverImage ? "upload" : card.coverSeriesId ? String(card.coverSeriesId) : "auto";
  const coverChoices = [
    ...(card.coverImage ? [{ id: "upload", url: card.coverImage, label: "Your picture" }] : []),
    ...(runs.length ? [{ id: "auto", url: runs[0].cover, label: "The first run's" }] : []),
    ...runs.map((run) => ({ id: String(run.id), url: run.cover, label: `${run.title}${/^\d{4}$/.test(String(run.year)) ? ` (${run.year})` : ""}` })),
  ];
  const ownerLine = groupOwnerLine(card);
  const firstRunId = runs[0]?.id ? String(runs[0].id) : null;
  // Starting over, as an arc's Advanced offers: the first run's first
  // readable comic, looked up when Advanced is opened.
  const [firstFile, setFirstFile] = useState(null);
  useEffect(() => {
    if (tab !== "advanced" || !firstRunId) return undefined;
    let live = true;
    apiRequest(`/api/v1/series/${firstRunId}/reading`)
      .then((data) => { if (live) setFirstFile((data.issues || []).find((file) => file.readable) || null); })
      .catch(() => { if (live) setFirstFile(null); });
    return () => { live = false; };
  }, [tab, firstRunId, readingVersion]);
  const readingBadge = allRead ? { tone: "green", text: "Read" } : started || finished ? { tone: "violet", text: `In progress · ${finished} of ${runs.length} read` } : null;
  const EDIT_ROWS = [
    { id: "cover", label: "Cover", detail: "A picture you upload, or one of its runs' covers.", onOpen: () => setEdit("cover") },
    { id: "backdrop", label: "Header background", detail: "A page from one of its comics, behind the header.", disabled: !files.length, onOpen: () => setEdit("backdrop") },
  ];
  const editTitle = edit === "arrange" ? "Arrange" : edit ? EDIT_ROWS.find((row) => row.id === edit)?.label : "Edit collection";
  const arrangeView = <GroupArrange noun="run" groupNoun="collection" ids={runIds} sortMode={card.sortMode || "custom"} sortOptions={COLLECTION_SORTS} busy={busy}
            onSortMode={(next) => act("sort", () => onSave(card.id, { sortMode: next }))}
            onReorder={(order) => act("order", () => onSave(card.id, { order }))}
            onRemove={(id) => act("remove", () => onSave(card.id, { remove: [String(id)] }))}
            nameOf={(id) => runsById[id]?.title || "Run"}
            renderRow={(id) => <RunCollectionRow run={runsById[id]} />}
            onAdd={onAddRuns} addLabel="Add runs" empty={<div className="drawer-empty"><CollectionIcon size={26} /><strong>Nothing to arrange yet</strong><span>Add runs from your library to start it.</span>
              <button type="button" className="ghost-button" onClick={onAddRuns}><Plus size={17} /> Add runs</button></div>} />;
  const advanced = <div className="advanced-tools">
    <div className="drawer-facts"><span><strong>{card.runCount}</strong>Runs</span><span><strong>{issueTotals.owned}</strong>Issues here</span><span><strong>{started}</strong>In progress</span></div>
    {/* Arrange lives in Advanced, for whoever may change it (owner, 2026-10-02). */}
    {/* Arrange is a card here that steps into its own screen, as Edit's
        pickers do, rather than a long list over the rest (2026-10-02). */}
    {canEdit && runs.length ? <GroupArrangeCard noun="run" count={runs.length} sortMode={card.sortMode || "custom"} sortOptions={COLLECTION_SORTS} onOpen={() => setEdit("arrange")} /> : null}
    <GroupShareCard group={card} noun="collection" busy={busy}
      onShare={(on) => act("share", () => onSave(card.id, { shared: on }))}
      onStopSharing={() => act("share", () => onSave(card.id, { shared: false }))} />
    <GroupReadingCards noun="collection" firstLabel="the first run’s first issue"
      onRestart={firstFile && onRead ? () => onRead({ id: firstFile.id, fromStart: true }) : null}
      allRead={allRead} anyRead={anyRead} busy={busy}
      onMark={runs.length && onMarkRuns ? (read) => act("mark", () => onMarkRuns(runs, read)) : null} />
    {card.manageable ? <GroupDeleteCard group={card} noun="collection" name={card.name} onDelete={() => act("delete", () => onDelete(card.id))} /> : null}
  </div>;
  const emptyRuns = <div className="drawer-empty"><CollectionIcon size={26} /><strong>Nothing in it yet</strong><span>{canEdit ? "Add runs from your library to start it." : "Runs added to it show up here."}</span>
    {canEdit ? <button type="button" className="ghost-button" onClick={onAddRuns}><Plus size={17} /> Add runs</button> : null}</div>;
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer comic-drawer${toned.className}${edit !== null ? " comic-drawer--editing" : ""} ${closing ? "closing" : ""}`} style={toned.style} ref={dialogRef}
      role="dialog" aria-modal="true" aria-labelledby="run-collection-title" onMouseDown={(event) => event.stopPropagation()}>
      <DrawerTopBar
        title={edit !== null ? editTitle : card.name}
        onClose={requestClose}
        onBack={edit !== null ? () => setEdit(edit && edit !== "arrange" ? "" : null) : undefined}
        stacked={edit !== null}
        closeLabel="Close collection">
        {edit === null && onToggleReadingList ? <ReadingListToggle on={isOnReadingList(readingList, "collection", card.id)} title={card.name}
          onChange={(on) => onToggleReadingList("collection", card.id, on)} /> : null}
        {edit === null && canEdit ? <button type="button" className="glass-button glass-button--icon comic-drawer-edit-button" onClick={() => { setError(""); setEdit(""); }} aria-label={`Edit ${card.name}`} title="Edit"><PencilSimple size={20} /></button> : null}
      </DrawerTopBar>
      <header className="comic-drawer-hero">
        {heroArt ? <><img className="comic-drawer-backdrop" src={imageSrc(heroArt)} alt="" aria-hidden="true" key={heroArt} onError={backdrop.page ? backdrop.onFail : () => setArtFailed(heroArt)} /><img className="comic-drawer-backdrop blurred" src={imageSrc(heroArt)} alt="" aria-hidden="true" key={`${heroArt}-blurred`} /></> : null}
        <span className="comic-drawer-scrim" aria-hidden="true" />
        <div className="comic-drawer-identity">
          <div className="comic-drawer-cover">{canEdit ? <button type="button" className="drawer-cover-button" onClick={() => setEdit("cover")} aria-label={`Change the cover for ${card.name}`}><DiscoverCover src={coverArt} alt={`${card.name} cover`} glyph={30} /><span className="drawer-cover-hint"><ImageSquare size={15} /> Change cover</span></button> : <DiscoverCover src={coverArt} alt={`${card.name} cover`} glyph={30} />}</div>
          <div className="comic-drawer-copy">
            <div className="comic-drawer-titles">
              <h2 id="run-collection-title">{card.name}</h2>
              {/* As its card and a run's: the one publisher, if it is one, then
                  the years; what it is, a badge, as on the card. */}
              {card.publisher || card.years ? <p>{[card.publisher, card.years].filter(Boolean).join(" • ")}</p> : null}
            </div>
            <div className="comic-drawer-statuses">
              <StatusBadge tone="muted">Collection</StatusBadge>
              {ownerLine ? <StatusBadge tone="muted">{ownerLine}</StatusBadge> : null}
              {readingBadge ? <StatusBadge tone={readingBadge.tone}>{readingBadge.text}</StatusBadge> : null}
            </div>
            {issueTotals.total ? <Ownership series={arcOwnership({ owned: issueTotals.owned, issueCount: issueTotals.total, missing: issueTotals.total - issueTotals.owned })} compact /> : null}
          </div>
        </div>
      </header>
      {error ? <p className="workbench-error comic-drawer-error" role="alert">{error}</p> : null}
      {edit !== null ? <div className="comic-drawer-body comic-drawer-edit">
        {edit === "" ? <GroupEditScreen name={card.name} summary={card.summary} busy={busy} rows={EDIT_ROWS}
          onSave={(changes) => act("save", () => onSave(card.id, changes)).then((done) => { if (done) setEdit(null); })} /> : null}
        {edit === "cover" ? <GroupCoverPicker choices={coverChoices} current={chosenCover} busy={busy}
          onUpload={onUploadCover ? (file) => act("upload", () => onUploadCover(card.id, file)).then((done) => { if (done) setEdit(""); }) : null}
          onChoose={(choice) => act("cover", () => onSave(card.id, choice.id === "upload" ? {}
            : { coverSeriesId: choice.id === "auto" ? null : choice.id, ...(card.coverImage ? { coverImage: null } : {}) })).then((done) => { if (done) setEdit(""); })} /> : null}
        {edit === "arrange" ? arrangeView : null}
        {edit === "backdrop" ? <>
          <p className="backdrop-workbench-intro">Choose a page from one of {card.name}&rsquo;s comics to show behind the drawer&rsquo;s header.</p>
          <BackdropPicker series={{ id: `collection-${card.id}`, title: card.name, fileDetails: files }} current={backdrop.shown}
            busy={backdrop.busy} error={backdrop.error}
            onChoose={(fileId, page) => backdrop.save({ fileId, page }).then((done) => { if (done) setEdit(""); })}
            onAutomatic={() => backdrop.save({ source: "auto" }).then((done) => { if (done) setEdit(""); })} />
        </> : null}
      </div> : <>
        {readRun && readTarget?.runId === String(readRun.id) && readingVerb(readTarget.resume) && onRead ? <div className="comic-drawer-actions">
          <ReadRunButton reading={readTarget} title={readRun.title} medium={readRun.medium} onRead={onRead} />
        </div> : null}
        <DrawerTabs tabs={tabs} tab={tab} onTab={setTab} label="Collection details" tabsRef={tabsRef} glass={tabGlass} />
        <div className="comic-drawer-body" key={tab}>
          {tab === "overview" ? <>
            {card.summary ? <RunSynopsis text={card.summary} heading="About" /> : null}
            {runs.length ? <div className="comic-drawer-shelf-group">
              <ComicDrawerRow title="Runs" count={runs.length}>{runs.map((run) => <ComicDrawerRunCard run={run} onOpen={(item) => onOpenSeries(item)} key={run.id} />)}</ComicDrawerRow>
              {canEdit ? <button type="button" className="glass-button comic-drawer-add" onClick={onAddRuns}><Plus size={18} /> Add runs</button> : null}
            </div> : emptyRuns}
          </> : null}
          {tab === "runs" ? (runs.length ? <SeriesList series={runs} view="grid" reading={reading || {}} onOpen={onOpenSeries} onRead={onRead} /> : emptyRuns) : null}
          {tab === "advanced" ? advanced : null}
        </div>
      </>}
    </aside>
  </div>;
}
