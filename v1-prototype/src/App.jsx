import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FlipparrMark } from "./brand.jsx";
import {
  Users,
  UserCircle,
  DeviceMobile,
  Hourglass,
  ArrowsClockwise,
  DownloadSimple,
  ListNumbers,
  ListPlus,
  CaretRight,
  Heartbeat,
  LockSimple,
  Sparkle,
  ArrowUpRight,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Books,
  CaretDown,
  Trash,
  Check,
  CheckCircle,
  Copy,
  ClockCounterClockwise,
  CloudArrowDown,
  Database,
  Eye,
  FolderOpen,
  Gear,
  Info,
  Pulse,
  HardDrive,
  ListBullets,
  ListChecks,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  ShieldCheck,
  SignOut,
  UploadSimple,
  WarningCircle,
  X } from "@phosphor-icons/react";
import { LoadingIndicator } from "./components/LoadingIndicator";
import { Button } from "./components/Button";
import { StatusBadge } from "./components/StatusBadge";
import { SettingsCard, Toggle } from "./components/SettingsControls.jsx";
import { PinPad, ProfileAvatar, ProfileTiles } from "./components/ProfileTiles.jsx";
import {
  DialogCloseButton, SheetGrabber, closeTopDialog, dialogLeftByBack, dialogMarks, isPhoneWidth, openDialogs,
  slideSheetAway, useDialog, useDrawerExit, usePhoneWidth, useSwipeToDismiss } from "./dialogs.jsx";
import { ReaderView } from "./reader/ReaderView.jsx";
import { historyStep } from "./history-step.js";
import { Host } from "./host.js";
import { apiRequest, forgetAnswers, lastAnswer, rememberAnswer } from "./api.js";
import { listCard, communityListLine, arcMatches, arcYears, groupOwnerLine, groupsToAddTo, issuesInRange } from "./reading-list.js";
import { jobHasFailed } from "./nav-counts.js";
import { agoPhrase, coolingProviders, formatBytes, problemTitle, scanBadge, waitingWork, workerAdvice, workerBadge } from "./system-status.js";

import { arrivalAt, canDeleteJob, canDeletePull, classifyRequest, groupPullList, isWorking, jobsForTab, releaseSearchSummary, tabCount, waitingIssues, RECENT_ARRIVAL_DAYS, takeAnywayCopy, releaseSendLabel, releaseTransport, downloadStateLabel, sourceOrder, sourceRows, groupServices } from "./pull-list.js";
import {
  needsAttention, staleDismissals, readLegacyDismissed, forgetLegacyState, timeAgo } from "./notifications.js";

import { isEdgeTouch, isStolenBack, PULL_REFRESH_PX } from "./reader.js";
import { readRecent, recentEntry, rememberRecent, writeRecent } from "./recent-searches.js";
import {
  FollowingIcon,
  ActiveRunIcon, FollowedIcon, PullIcon, ShelfBackIcon, ShelfNextIcon } from "./design-icons.jsx";

import { setStorageProfile, storageProfile, profileStorage, migrateLegacyKeys, can, isAdmin, nextProfileColour, VIEWER_CACHE_KEY, RATINGS, RATING_LABELS, limitLabel, isLocked, lockChoices, lockPatch, LOCK_LABELS } from "./profiles.js";
import { ratingRows, limitedProfiles, filterRatingRows, publisherChoices, ratingNote, ratingOutcome } from "./rating-review.js";

import { collectionCards, collectionMatches } from "./run-collections.js";
import { EMPTY_ENTRIES, withEntry } from "./reading-list-tab.js";

import {
  pullState, issueKey, PULL_STATES, PULL_LABELS, READER_PULL_LABELS, shelfState, splitSearchResults,
  providerProgress, libraryMatchState,
  selectableIssue, releasedToPull, runPullSummary, runPreviewIds, weeklyPicks,
  runModes, completeRunToPull, issueLabel, libraryRunMatches } from "./discover.js";
import { requestKey, waitingKeys, runRequested, requestScope, adminQueue, isFollow, swipeDecision, ratingTone, UNDO_MS, REQUEST_STATE_LABELS, ADMIN_STATE_LABELS } from "./member-requests.js";
import { ViewerContext, useViewer } from "./viewer.js";
import { CoverArt, DiscoverCover, SeriesCover } from "./components/CoverArt.jsx";
import { MonitoringStatus, Ownership, PublicationStatus, arcOwnership } from "./library/cards.jsx";

import { useShelfPaging } from "./library/shelves.jsx";
import { GlassSegmented, GlassSelect, LoadingSpinner, useGlassIndicator } from "./components/glass.jsx";
import { FinishDrawer } from "./drawers/FinishDrawer.jsx";
import { ReadingListDrawer } from "./drawers/ReadingListDrawer.jsx";
import { RunCollectionDrawer } from "./drawers/RunCollectionDrawer.jsx";
import { SeriesDrawer } from "./drawers/SeriesDrawer.jsx";
import { ComicDrawerCreators, ComicDrawerRow, ComicDrawerRunCard, DiscoverDrawerHero, DrawerPrimary, DrawerTabs, ProviderCredit, PullCardSkeleton, RunSynopsis, formatLongDate, toneProps, useArtTone } from "./drawers/frame.jsx";
import { BackdropPicker, CoverPicker, DragOrderList } from "./drawers/groups.jsx";
import { ActionMenu, GroupedIssueInventory, downloadFrom } from "./drawers/inventory.jsx";
import { CollectedEditionsContext, EDITION_KIND_LABELS, editionKindLabel, identityKey, volumeTerminology } from "./editions.js";
import { Nav } from "./frame/Nav.jsx";
import { HeaderContext, PageHeader, SegmentedTabs } from "./frame/header.jsx";
import { DEMO_SERIES, LibraryView, catalogPending, logicalCatalogSeries, searchQueryParts, useReadingLists } from "./library/LibraryView.jsx";

const DEMO_META_ITEMS = [
  { file: "ironwood_vol1.cbz", issue: "Empty archive", detail: "The archive is only 22 bytes and contains no comic pages.", severity: "error", category: "file", code: "empty_archive" },
  { file: "nightshift_vol1.cbz", issue: "No image pages", detail: "Metadata exists, but the archive has no readable cover or pages.", severity: "error", category: "file", code: "no_image_pages" },
  { file: "nightshift_vol2.cbz", issue: "Conflicting volume metadata", detail: "The filename and external catalog disagree with ComicInfo.xml.", severity: "warning", category: "metadata", code: "metadata_conflict" },
];

const ACQUISITION_LABELS = {
  volumes: "Volumes preferred",
  issues: "Single issues preferred",
  either: "Issues or volumes",
};

const JOB_STATUS_LABELS = {
  queued: "Queued",
  waiting: "Waiting",
  searching: "Searching",
  grabbed: "Download sent",
  failed: "Search failed",
  fulfilled: "Covered",
  cancelled: "Cancelled",
};

const DOWNLOAD_STATUS_LABELS = {
  queued: "Waiting for SABnzbd",
  downloading: "Downloading",
  completed: "Download complete",
  importing: "Adding to library",
  waiting_for_files: "Waiting for completed file",
  imported: "Added to library",
  failed: "Import needs attention",
};

// Browser storage is the profile's own (profiles.js). Which profile was last
// reading on this device is known before anything renders, so the first
// paint already reads that profile's sort, view and settings.
function cachedViewerId() {
  try { return Number(window.localStorage.getItem(VIEWER_CACHE_KEY)) || null; } catch { return null; }
}
setStorageProfile(typeof window === "undefined" ? null : cachedViewerId());

function storable() {
  try {
    window.localStorage.setItem("flipparr.probe", "1");
    window.localStorage.removeItem("flipparr.probe");
    return true;
  } catch {
    return false;
  }
}

// A profile's reader settings live on the server, so they follow the person to
// any device; the browser keeps a copy for the first paint. The server's word
// wins for what it has.
async function syncReaderPrefs() {
  try {
    const me = await apiRequest("/api/v1/me");
    const prefs = me?.prefs || {};
    if (!Object.keys(prefs).length) return;
    const storage = profileStorage();
    let local = {};
    try { local = JSON.parse(storage.getItem("flipparr.reader") || "{}") || {}; } catch { local = {}; }
    storage.setItem("flipparr.reader", JSON.stringify({ ...local, ...prefs }));
  } catch {
    // Offline or refused: the browser's copy stands.
  }
}

// Enter a profile: remember it on this device, hand the browser's settings
// from before profiles to the admin, and start the page afresh. A reload is
// the one sure way that nothing -- a cached answer, an open drawer, a
// component's state -- carries one profile's library into another's.
function enterProfile(viewerId) {
  forgetAnswers();
  try {
    if (viewerId) {
      window.localStorage.setItem(VIEWER_CACHE_KEY, String(viewerId));
      migrateLegacyKeys(window.localStorage, viewerId);
    } else {
      window.localStorage.removeItem(VIEWER_CACHE_KEY);
    }
  } catch {
    // A browser that keeps nothing starts every profile fresh.
  }
  window.location.reload();
}

function parseIssueInput(value) {
  const issues = [];
  for (const token of value.split(",").map((item) => item.trim()).filter(Boolean)) {
    const range = token.match(/^(\d+)\s*[-–]\s*(\d+)$/);
    if (!range) { issues.push(token); continue; }
    const start = Number(range[1]);
    const end = Number(range[2]);
    if (end < start || end - start > 499) throw new Error(`Invalid issue range: ${token}`);
    for (let issue = start; issue <= end; issue += 1) issues.push(String(issue));
  }
  return [...new Set(issues)];
}

// ---- Read groups ------------------------------------------------------------
// A collection is runs read in an order; a story arc is issues read in an
// order. One pattern for both (owner, 2026-10-02): the same drawer frame,
// Edit (name and summary on the screen, cover and header background a step
// in), Arrange tab, Advanced cards, Add sheet and New dialog, differing only
// where their content does.

/** A new collection or story arc: its name and, if you like, what it is. */
function NewGroupModal({ kind, onClose, onCreate }) {
  const dialogRef = useDialog(onClose);
  const arc = kind === "arc";
  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const usable = name.trim().length >= 2;
  async function submit(event) {
    event.preventDefault();
    if (busy || !usable) return;
    setBusy(true);
    setError("");
    try { await onCreate({ name: name.trim(), summary: summary.trim() }); } catch (failure) { setError(failure.message); setBusy(false); }
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}>
    <section className="modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="new-group-title" onMouseDown={(event) => event.stopPropagation()}>
      <DialogCloseButton onClose={onClose} label="Close" />
      <span className="eyebrow">{arc ? "Story arcs" : "Collections"}</span>
      <h2 id="new-group-title">{arc ? "New story arc" : "New collection"}</h2>
      <p className="settings-card-note">{arc
        ? "Your own reading order of issues, across runs. Only you see it until you share it."
        : "Runs that belong together, read in the order you give them. Only you see it until you share it."}</p>
      <form className="profile-form" onSubmit={submit}>
        <label className="form-field"><span>Name</span>
          <input value={name} maxLength={80} autoFocus onChange={(event) => setName(event.target.value)} placeholder={arc ? "Blackest Night, my order" : "Batman essentials"} />
        </label>
        <label className="form-field"><span>{arc ? "Description" : "Summary"}</span>
          <textarea value={summary} maxLength={2000} rows={3} onChange={(event) => setSummary(event.target.value)} placeholder="Optional" />
        </label>
        {error ? <p className="workbench-error" role="alert">{error}</p> : null}
        <div className="metadata-edit-actions">
          <button type="button" className="ghost-button" onClick={onClose}>Cancel</button>
          <button type="submit" className="primary-button" disabled={busy || !usable} aria-busy={busy || undefined}>
            {busy ? <LoadingSpinner size={18} /> : <Plus size={18} />} Create
          </button>
        </div>
      </form>
    </section>
  </div>;
}

/**
 * "Add to …" for a read group: the ones this profile may change, latest
 * first, a tick on those that hold it already, and "Or start a new one"
 * (Komga's and Spotify's add-to-list). What is added goes on the end.
 */
function AddToGroupSheet({ kind, groups, ids, itemName, onClose, onAdd, onCreate }) {
  const dialogRef = useDialog(onClose);
  const arc = kind === "arc";
  const noun = arc ? "story arc" : "collection";
  const [name, setName] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const choices = groups === null ? null : groupsToAddTo(groups, ids, (group) => (arc ? group.issueIds : group.runIds));
  async function act(key, work) {
    if (busy) return;
    setBusy(key);
    setError("");
    try { await work(); } catch (failure) { setError(failure.message); setBusy(""); }
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}>
    <section className="modal arc-picker" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="group-picker-title" onMouseDown={(event) => event.stopPropagation()}>
      <DialogCloseButton onClose={onClose} label="Close" />
      <span className="eyebrow">{arc ? "Story arcs" : "Collections"}</span>
      <h2 id="group-picker-title">Add to {noun}</h2>
      <p className="settings-card-note">{ids.length > 1
        ? `${itemName} go on the end of the ${noun} you choose; arrange them there.`
        : `${itemName} goes on the end of the ${noun} you choose; arrange it there.`}</p>
      {choices === null ? <div className="arc-picker-loading" role="status"><LoadingSpinner size={18} /> Finding your {noun}s</div>
        : choices.length ? <div className="arc-picker-list">{choices.map((group) => <button type="button" className="arc-picker-row" key={group.id}
          disabled={group.has || Boolean(busy)} aria-busy={busy === group.id || undefined} onClick={() => act(group.id, () => onAdd(group))}>
          <span className="rating-review-cover"><DiscoverCover src={group.cover} alt="" glyph={16} /></span>
          <span><strong>{group.name}</strong><small>{[arc ? `${group.issueCount} issue${group.issueCount === 1 ? "" : "s"}` : `${group.runCount ?? group.runIds?.length ?? 0} run${(group.runCount ?? group.runIds?.length) === 1 ? "" : "s"}`,
            groupOwnerLine(group), group.hasSome ? "some already in it" : null].filter(Boolean).join(" · ")}</small></span>
          {group.has ? <span className="arc-picker-state"><Check size={16} weight="bold" /> Added</span> : busy === group.id ? <LoadingSpinner size={16} /> : <Plus size={18} />}
        </button>)}</div>
        : <p className="arc-picker-empty">No {noun}s of your own yet. Name one below to start it with this.</p>}
      <form className="profile-form arc-picker-new" onSubmit={(event) => { event.preventDefault(); if (name.trim().length >= 2) act("new", () => onCreate(name.trim())); }}>
        <label className="form-field"><span>Or start a new one</span>
          <input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} placeholder={arc ? "Blackest Night, my order" : "Batman essentials"} /></label>
        {error ? <p className="workbench-error" role="alert">{error}</p> : null}
        <div className="metadata-edit-actions">
          <button type="button" className="ghost-button" onClick={onClose}>Cancel</button>
          <button type="submit" className="primary-button" disabled={Boolean(busy) || name.trim().length < 2} aria-busy={busy === "new" || undefined}>
            {busy === "new" ? <LoadingSpinner size={18} /> : <Plus size={18} />} Create
          </button>
        </div>
      </form>
    </section>
  </div>;
}

/**
 * "Add to story arc…" from an issue's menu or a run's selection: the shared
 * Add sheet, with the arcs this profile may change. A new arc made here
 * starts with what was chosen.
 */
function ArcPickerModal({ issues, issueName = "", onClose, onAdded, onCreated }) {
  const issueIds = (issues || []).map((item) => String(item.id));
  const [lists, setLists] = useState(() => lastAnswer("/api/v1/reading-lists")?.lists ?? null);
  useEffect(() => {
    let live = true;
    apiRequest("/api/v1/reading-lists").then((answer) => { if (live) setLists(answer.lists || []); })
      .catch(() => { if (live) setLists((current) => current || []); });
    return () => { live = false; };
  }, []);
  const json = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return <AddToGroupSheet kind="arc" groups={lists} ids={issueIds} itemName={issueName} onClose={onClose}
    onAdd={async (list) => onAdded(await apiRequest(`/api/v1/reading-lists/${list.id}/items`, json({ issueIds })))}
    onCreate={async (name) => onCreated(await apiRequest("/api/v1/reading-lists/manual", json({ name, issueIds })))} />;
}

/**
 * "Add issues" from inside an arc -- Spotify's empty playlist that asks what
 * to put in it: find a run, tick its issues (or a range of numbers), add
 * them, and go on to another run without leaving. Owned and missing issues
 * alike; those already in the arc are ticked and fixed. They go on the end
 * in the run's order, to drag into place after.
 */
function ArcIssuePickerModal({ list, series, onClose, onAdded }) {
  const dialogRef = useDialog(onClose);
  const runs = useMemo(() => ratingRows((series || []).filter((run) => (run.issues || []).length)), [series]);
  const runsById = useMemo(() => new Map((series || []).map((run) => [String(run.id), run])), [series]);
  const [query, setQuery] = useState("");
  const [runId, setRunId] = useState(null);
  const [inArc, setInArc] = useState(() => new Set());
  const [selected, setSelected] = useState(() => new Set());
  const [range, setRange] = useState({ from: "", to: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [arcName, setArcName] = useState(list.name);
  useEffect(() => {
    let live = true;
    apiRequest(`/api/v1/reading-lists/${list.id}`).then((detail) => {
      if (!live) return;
      setInArc(new Set(detail.items.map((item) => String(item.issueId)).filter((id) => id !== "null")));
      setArcName(detail.name);
    }).catch(() => {});
    return () => { live = false; };
  }, [list.id]);
  const run = runId ? runsById.get(runId) : null;
  const issues = run?.issues || [];
  const choosable = issues.filter((issue) => !inArc.has(String(issue.id)));
  const allChosen = choosable.length > 0 && choosable.every((issue) => selected.has(String(issue.id)));
  const shownRuns = filterRatingRows(runs, { show: "all", query });
  function toggle(id) {
    setSelected((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  }
  function chooseRun(id) { setRunId(id); setSelected(new Set()); setRange({ from: "", to: "" }); setStatus(""); setError(""); }
  function selectRange(event) {
    event.preventDefault();
    const ids = issuesInRange(choosable, range.from, range.to);
    setSelected((current) => new Set([...current, ...ids]));
    setStatus(ids.length ? "" : `No issues numbered ${range.from} to ${range.to} to add`);
  }
  async function add() {
    if (!selected.size || busy) return;
    setBusy(true); setError(""); setStatus("");
    // In the run's order, whatever order they were ticked in.
    const ids = issues.map((issue) => String(issue.id)).filter((id) => selected.has(id));
    try {
      const result = await apiRequest(`/api/v1/reading-lists/${list.id}/items`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ issueIds: ids }) });
      setInArc(new Set(result.items.map((item) => String(item.issueId))));
      setSelected(new Set());
      const count = result.added?.length || 0;
      setStatus(`${count} issue${count === 1 ? "" : "s"} added to ${result.name}`);
      onAdded?.(result);
    } catch (failure) { setError(failure.message); }
    setBusy(false);
  }
  const runLabel = run ? `${run.title}${/^\d{4}$/.test(String(run.year)) ? ` (${run.year})` : ""}` : "";
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}>
    <section className="modal rating-review arc-issue-picker" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="arc-issue-picker-title" onMouseDown={(event) => event.stopPropagation()}>
      <DialogCloseButton onClose={onClose} label="Close" />
      <span className="eyebrow">Add issues</span>
      <h2 id="arc-issue-picker-title">{arcName}</h2>
      {!run ? <>
        <label className="form-field rating-review-search"><span className="sr-only">Find a run</span>
          <input type="search" value={query} autoFocus onChange={(event) => setQuery(event.target.value)} placeholder="Find a run" /></label>
        {shownRuns.length ? <div className="arc-picker-list">{shownRuns.map((row) => {
          const item = runsById.get(row.id);
          const count = item?.issues?.length || 0;
          const here = (item?.issues || []).filter((issue) => inArc.has(String(issue.id))).length;
          return <button type="button" className="arc-picker-row" key={row.id} onClick={() => chooseRun(row.id)}>
            <span className="rating-review-cover"><CoverArt id={row.id} title={row.title} cover={row.cover} coverCandidates={row.coverCandidates} decorative placeholderSize={16} /></span>
            <span><strong>{row.title}{row.year ? ` (${row.year})` : ""}</strong>
              <small>{[`${count} issue${count === 1 ? "" : "s"}`, here ? `${here} in this arc` : null, row.publisher].filter(Boolean).join(" \u00b7 ")}</small></span>
            <CaretRight size={16} />
          </button>;
        })}</div> : <p className="rating-review-empty">No runs match.</p>}
      </> : <>
        <button type="button" className="ghost-button arc-issue-picker-back" onClick={() => chooseRun(null)}><ArrowLeft size={16} /> All runs</button>
        <h3 className="arc-issue-picker-run">{runLabel}</h3>
        <form className="arc-issue-range" onSubmit={selectRange}>
          <label className="form-field"><span>From #</span><input inputMode="decimal" value={range.from} onChange={(event) => setRange({ ...range, from: event.target.value })} /></label>
          <label className="form-field"><span>To #</span><input inputMode="decimal" value={range.to} onChange={(event) => setRange({ ...range, to: event.target.value })} /></label>
          <button type="submit" className="secondary-button" disabled={!range.from.trim() || !range.to.trim()}>Select</button>
        </form>
        <label className="check-row rating-review-all">
          <input type="checkbox" checked={allChosen} disabled={!choosable.length}
            onChange={() => setSelected(allChosen ? new Set() : new Set(choosable.map((issue) => String(issue.id))))} />
          <span><strong>{allChosen ? "Clear all" : "Select all"}</strong><small>{issues.length} issue{issues.length === 1 ? "" : "s"}{issues.length - choosable.length ? ` \u00b7 ${issues.length - choosable.length} in this arc` : ""}</small></span>
        </label>
        <ul className="rating-review-list">{issues.map((issue) => {
          const id = String(issue.id);
          const member = inArc.has(id);
          const owned = issue.ownership && issue.ownership !== "unowned";
          return <li key={id}><label className="rating-review-row">
            <input type="checkbox" checked={member || selected.has(id)} disabled={member} onChange={() => toggle(id)} />
            <span className="rating-review-cover"><DiscoverCover src={issue.fileCover || issue.cover} alt="" glyph={16} /></span>
            <span><strong>{issueLabel(issue.number, run.medium)}{issue.title && identityKey(issue.title) !== identityKey(`Issue ${issue.number}`) ? ` \u00b7 ${issue.title}` : ""}</strong>
              <small>{[formatLongDate(issue.publicationDate), member ? "In this arc" : owned ? "In library" : "Missing"].filter(Boolean).join(" \u00b7 ")}</small></span>
          </label></li>;
        })}</ul>
      </>}
      {error ? <p className="workbench-error" role="alert">{error}</p> : null}
      <p className="arc-issue-picker-status" role="status">{status}</p>
      <div className="rating-review-actions">
        <span>{run ? `${selected.size} selected` : "Choose a run"}</span>
        {run ? <button type="button" className="primary-button" onClick={add} disabled={busy || !selected.size} aria-busy={busy || undefined}>
          {busy ? <LoadingSpinner size={18} /> : <Plus size={18} />} Add {selected.size || ""} issue{selected.size === 1 ? "" : "s"}
        </button> : <button type="button" className="secondary-button" onClick={onClose}>Done</button>}
      </div>
    </section>
  </div>;
}

/** Runs to add to a collection: the Set ratings list, choosing runs instead. */
function RunPickerModal({ title, series, already = [], onClose, onAdd }) {
  const dialogRef = useDialog(onClose);
  const rows = useMemo(() => ratingRows(series), [series]);
  const inAlready = useMemo(() => new Set(already.map(String)), [already]);
  const [publisher, setPublisher] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const shown = filterRatingRows(rows, { show: "all", publisher, query });
  const choosable = shown.filter((row) => !inAlready.has(row.id));
  const allShown = choosable.length > 0 && choosable.every((row) => selected.has(row.id));
  function toggle(id) {
    setSelected((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  }
  function toggleShown() {
    setSelected((current) => { const next = new Set(current); for (const row of choosable) { if (allShown) next.delete(row.id); else next.add(row.id); } return next; });
  }
  async function add() {
    if (!selected.size || busy) return;
    setBusy(true);
    setError("");
    try { await onAdd([...selected]); } catch (failure) { setError(failure.message); setBusy(false); }
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}>
    <section className="modal rating-review" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="run-picker-title" onMouseDown={(event) => event.stopPropagation()}>
      <DialogCloseButton onClose={onClose} label="Close" />
      <span className="eyebrow">Add runs</span>
      <h2 id="run-picker-title">{title}</h2>
      <div className="rating-review-filters rating-review-filters--two">
        <GlassSelect label="Publisher" value={publisher || "any"} onChange={(next) => setPublisher(next === "any" ? "" : next)}
          options={[{ value: "any", label: "All publishers" }, ...publisherChoices(rows).map((house) => ({ value: house.key, label: `${house.label} (${house.count})` }))]} className="glass-select--fill" />
        <label className="form-field rating-review-search"><span className="sr-only">Find a run</span>
          <input type="search" value={query} autoFocus onChange={(event) => setQuery(event.target.value)} placeholder="Title or year" /></label>
      </div>
      <label className="check-row rating-review-all">
        <input type="checkbox" checked={allShown} onChange={toggleShown} disabled={!choosable.length} />
        <span><strong>{allShown ? "Clear these" : "Select these"}</strong><small>{shown.length} run{shown.length === 1 ? "" : "s"} shown</small></span>
      </label>
      {shown.length ? <ul className="rating-review-list">{shown.map((row) => {
        const member = inAlready.has(row.id);
        return <li key={row.id}><label className="rating-review-row">
          <input type="checkbox" checked={member || selected.has(row.id)} disabled={member} onChange={() => toggle(row.id)} />
          <span className="rating-review-cover"><CoverArt id={row.id} title={row.title} cover={row.cover} coverCandidates={row.coverCandidates} decorative placeholderSize={16} /></span>
          <span><strong>{row.title}{row.year ? ` (${row.year})` : ""}</strong><small>{[row.publisher, member ? "In this collection" : ""].filter(Boolean).join(" · ")}</small></span>
        </label></li>;
      })}</ul> : <p className="rating-review-empty">No runs here.</p>}
      {error ? <p className="workbench-error" role="alert">{error}</p> : null}
      <div className="rating-review-actions">
        <span>{selected.size} selected</span>
        <button type="button" className="primary-button" onClick={add} disabled={busy || !selected.size} aria-busy={busy || undefined}>
          {busy ? <LoadingSpinner size={18} /> : <Plus size={18} />} Add {selected.size || ""}
        </button>
      </div>
    </section>
  </div>;
}

// A token rather than prose: RunStatusChip renders the label, and a function
// that returned "Completed run" while the chip tested for "completed" simply
// drew nothing at all.
function discoveryRunStatus(item) {
  const status = String(item?.status || "").toLowerCase();
  if (item?.yearEnded || ["completed", "complete", "cancelled"].includes(status)) return "completed";
  if (["ongoing", "continuing"].includes(status)) return "ongoing";
  return "unknown";
}

// The design's own control, not one of the app's button variants: a filled
// purple pill with a light border and an inner highlight.
function PullButton({ state, idleLabel, readerLabel = "Request", requestedLabel, onClick, size = "sm" }) {
  // A reader asks rather than pulls: "Request", then "Requested" until the
  // admin decides.
  const reader = !isAdmin(useViewer());
  const labels = reader ? READER_PULL_LABELS : PULL_LABELS;
  const settled = state === PULL_STATES.queued || state === PULL_STATES.owned;
  // Requested reads as settled, like On Pull List: the fill that says "done".
  const look = state === PULL_STATES.requested ? PULL_STATES.queued : state;
  return <button type="button" className={`pull-button pull-button-${size} pull-button-${look}`}
    onClick={onClick} disabled={state !== PULL_STATES.idle}
    aria-busy={state === PULL_STATES.pending || undefined}>
    <span>{state === PULL_STATES.idle ? (reader ? readerLabel : idleLabel)
      : state === PULL_STATES.requested && requestedLabel ? requestedLabel : labels[state]}</span>
    {state === PULL_STATES.pending ? <LoadingIndicator size={16} />
      : state === PULL_STATES.requested ? <Hourglass size={16} />
      : settled ? <FollowingIcon size={16} /> : <PullIcon />}
  </button>;
}

function RunStatusChip({ status }) {
  if (status === "completed") return <span className="run-status complete">Run Complete</span>;
  if (status === "ongoing") return <span className="run-status active"><ActiveRunIcon /> Active Run</span>;
  return null;
}

function PullCard({ issue, state, onPull, onOpen }) {
  return <article className="pull-card">
    <button type="button" className="discover-open" onClick={() => onOpen(issue)}
      aria-label={`Details for ${issue.title}`}>
      <DiscoverCover src={issue.cover} alt="" glyph={30} />
    </button>
    <div className="pull-card-body">
      <h3 title={issue.title}>{issue.title}</h3>
      <div className="pull-card-action">
        <PullButton state={state} idleLabel="Pull Issue" onClick={() => onPull(issue)} />
      </div>
    </div>
  </article>;
}

const SHELF_COPY = {
  empty: { title: "Nothing listed for this week", detail: "Metron has no shipping dates for these days yet." },
  unavailable: { title: "Release dates need Metron", detail: "Connect Metron in Settings. It is the only source with comic shop shipping dates." },
  // A reader cannot connect anything: it is the admin's to set up.
  readerUnavailable: { title: "Release dates aren't set up", detail: "The admin can connect Metron, the source of comic shop shipping dates." },
};

/**
 * One week of comics, scrolled sideways.
 *
 * The chevrons page by the visible width rather than by a card, because a row
 * that moves 120px on a click reads as a twitch rather than as navigation.
 */
function ReleaseShelf({ title, date, subtitle, state, issues = [], error, pulled, waiting, onPull, onOpen, onRetry }) {
  const copyKey = state === "unavailable" && !isAdmin(useViewer()) ? "readerUnavailable" : state;
  const { scroller, atStart, atEnd, measure, page } = useShelfPaging([issues.length, state]);
  const heading = date ? `${title} - ${date}` : title;
  return <section className="release-shelf" aria-label={heading}>
    <header>
      <div className="release-shelf-heading"><h2>{heading}</h2>{subtitle ? <p>{subtitle}</p> : null}</div>
      {state === "ready" ? <div className="shelf-scroll">
        <button type="button" onClick={() => page(-1)} disabled={atStart} aria-label={`Scroll ${title} back`}><ShelfBackIcon /></button>
        <button type="button" onClick={() => page(1)} disabled={atEnd} aria-label={`Scroll ${title} forward`}><ShelfNextIcon /></button>
      </div> : null}
    </header>
    {state === "loading" ? <div className="shelf-row" role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">Getting this week&rsquo;s releases from Metron</span>
      {[0, 1, 2, 3, 4, 5, 6, 7].map((item) => <PullCardSkeleton key={item} />)}
    </div> : null}
    {state === "ready" ? <div className="shelf-row" ref={scroller} onScroll={measure}>
      {issues.map((issue) => <PullCard issue={issue} state={pullState(issue, pulled, waiting)}
        onPull={onPull} onOpen={onOpen} key={issueKey(issue)} />)}
    </div> : null}
    {state === "error" ? <div className="shelf-message" role="status">
      <WarningCircle size={18} />
      <span><strong>This week could not be fetched</strong><small>{error}</small></span>
      <button type="button" onClick={onRetry}>Try again</button>
    </div> : null}
    {state === "empty" || state === "unavailable" ? <div className="shelf-message">
      <MagnifyingGlass size={18} />
      <span><strong>{SHELF_COPY[copyKey].title}</strong><small>{SHELF_COPY[copyKey].detail}</small></span>
    </div> : null}
  </section>;
}

function LibraryMatchCard({ series, onOpen }) {
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
function RecentSearches({ entries, allSeries, onClear, onSearch, onOpenSeries, onOpenRun }) {
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

function LibraryMatchSkeleton() {
  return <div className="library-match-skeleton" aria-hidden="true">
    <span className="discover-cover" />
    <h3><i /></h3>
  </div>;
}

function NewRunCard({ item, state, onPull, onOpen }) {
  // How long the run is, beside when it ran: "13 issues" is the difference
  // between a one-shot and a decade, and deciding to pull turns on it.
  const issues = Number(item.issueCount) || 0;
  const byline = [
    item.publisher || "Publisher unknown",
    item.yearLabel || item.yearBegan,
    issues ? `${issues} issue${issues === 1 ? "" : "s"}` : null,
  ].filter(Boolean).join(" • ");
  return <article className="new-run-card">
    <button type="button" className="discover-open" onClick={() => onOpen(item)}
      aria-label={`Issues in ${item.title}`}>
      <DiscoverCover src={item.cover} alt="" className="new-run-art" glyph={44} />
    </button>
    <div className="new-run-copy">
      <h3 title={item.title}>{item.title}</h3>
      <p title={byline}>{byline}</p>
    </div>
    <div className="new-run-status"><RunStatusChip status={discoveryRunStatus(item)} />{item.medium === "manga" ? <span className="discover-chip">Manga</span> : null}
      {/* Why a run whose title looks nothing like the search is here. */}
      {item.matchedBy ? <span className="discover-chip matched-by" title={item.matchedBy}>{item.matchedBy}</span> : null}</div>
    <hr />
    <div className="new-run-action">
      <PullButton state={state} idleLabel="Pull Run" size="md" onClick={() => onPull(item)} />
    </div>
  </article>;
}

function NewRunCardSkeleton() {
  return <div className="new-run-card new-run-skeleton" aria-hidden="true">
    <span className="discover-cover new-run-art" />
    <div className="new-run-copy"><h3><i /></h3><p><i /></p></div>
    <div className="new-run-status"><i /></div>
    <hr />
    <div className="new-run-action"><i /></div>
  </div>;
}

// The catalog arrives in one response, so until it does every list read from
// it is empty for the wrong reason. Screens other than the library show this
// instead of their empty state, which would otherwise flash "nothing here".
function CatalogLoading({ title, detail }) {
  return <div className="library-loading" role="status" aria-live="polite" aria-busy="true">
    <section className="library-loading-message">
      <span className="library-loading-icon"><LoadingSpinner size={21} /></span>
      <span><strong>{title}</strong><small>{detail}</small></span>
    </section>
  </div>;
}

// The four states a comic can be in on its way to the library, in the order
// it moves through them. Following is not among them: monitoring is a property
// of the run, shown on its card, not a place a run sits.
const PULL_LIST_TABS = [
  { id: "wanted", label: "Wanted" },
  { id: "downloading", label: "Downloading" },
  { id: "acquired", label: "Acquired" },
  { id: "failed", label: "Failed" },
];

const PULL_LIST_COPY = {
  wanted: {
    emptyTitle: "Nothing on your wanted list",
    emptyDetail: "Follow a run from Discover or a series, or request a replacement from Library health.",
  },
  downloading: {
    emptyTitle: "Nothing is downloading",
    emptyDetail: "Issues appear here while a download client is fetching them and while Flipparr is importing them.",
  },
  acquired: {
    emptyTitle: "Nothing has arrived recently",
    emptyDetail: `Issues appear here for ${RECENT_ARRIVAL_DAYS} days once they are downloaded, validated and added to your library.`,
  },
  failed: {
    emptyTitle: "Nothing has failed",
    emptyDetail: "A download that cannot finish on its own waits here for you to retry it or pick another release.",
  },
};

// Everything the phone's toolbar used to hold, in one sheet from the bottom of
// the screen: grid or list, the sort, the Following filter, and Runs or
// Collections when collected editions are on. Changes apply as they are made;
// Done, the backdrop and Escape all close it.
// A reading list from elsewhere -- a .cbl file, or a link to one -- kept as
// a story arc. The community's lists are other people's work, and the
// sentence at the foot says so; nothing is fetched that the admin did not
// name. A file that matches an arc already here asks before doubling it.
/**
 * Story arcs to suggest as someone types: Metron's arcs and the community's
 * reading lists, from the names the server keeps (no remote catalog is asked
 * per keystroke), a moment after typing stops.
 */
function useArcSuggestions(query, enabled = true) {
  const [found, setFound] = useState({ query: "", arcs: [], lists: [] });
  const cleaned = String(query || "").trim();
  useEffect(() => {
    if (!enabled || cleaned.length < 2) { setFound({ query: "", arcs: [], lists: [] }); return undefined; }
    let live = true;
    const timer = window.setTimeout(() => {
      apiRequest(`/api/v1/discover/arc-suggestions?query=${encodeURIComponent(cleaned)}`)
        .then((answer) => { if (live) setFound({ query: cleaned, arcs: answer.arcs || [], lists: answer.lists || [] }); })
        .catch(() => { if (live) setFound({ query: cleaned, arcs: [], lists: [] }); });
    }, 250);
    return () => { live = false; window.clearTimeout(timer); };
  }, [cleaned, enabled]);
  return found.query === cleaned ? found : { query: cleaned, arcs: [], lists: [] };
}

/** The community's reading lists as rows: a tap imports one as a story arc. */
function CommunityListRows({ lists, busyId, onImport }) {
  return <div className="arc-results">
    {lists.map((entry) => <button type="button" className="arc-row" key={entry.id} disabled={Boolean(busyId)} aria-busy={busyId === entry.id || undefined}
      onClick={() => onImport(entry)}>
      <span className="arc-row-icon" aria-hidden="true">{busyId === entry.id ? <LoadingSpinner size={18} /> : <ListNumbers size={20} weight="duotone" />}</span>
      <span><strong>{entry.name}</strong><small>{communityListLine(entry)}</small></span>
      <DownloadSimple size={16} aria-hidden="true" />
    </button>)}
  </div>;
}

function ImportReadingListModal({ onClose, onImported }) {
  const dialogRef = useDialog(onClose);
  const [file, setFile] = useState(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [duplicate, setDuplicate] = useState(null);
  // The community's lists, found by name: the way to a list without hunting
  // for its file or link (2026-10-03).
  const [communityQuery, setCommunityQuery] = useState("");
  const [picked, setPicked] = useState(null);
  const community = useArcSuggestions(communityQuery);
  async function send(force = false, entry = null) {
    const link = entry ? entry.url : url.trim();
    if (busy || (!file && !link)) return;
    setBusy(true);
    setPicked(entry ? entry.id : null);
    setError("");
    setDuplicate(null);
    try {
      const result = file && !entry
        ? await apiRequest("/api/v1/reading-lists/import", {
          method: "POST", body: file,
          headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name), ...(force ? { "X-Import-Anyway": "1" } : {}) },
        })
        : await apiRequest("/api/v1/reading-lists/import", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: link, force }),
        });
      onImported(result);
    } catch (problem) {
      if (problem?.status === 409 && problem?.payload?.readingListId) setDuplicate({ message: problem.message, id: problem.payload.readingListId, entry });
      else setError(problem.message);
    } finally {
      setBusy(false);
      setPicked(null);
    }
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}>
    <section className="modal import-list-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="import-list-title" onMouseDown={(event) => event.stopPropagation()}>
      <DialogCloseButton onClose={onClose} label="Close import" />
      <span className="eyebrow">Story arcs</span>
      <h2 id="import-list-title">Import a story arc</h2>
      <p className="workbench-intro">A CBL file, or a link to one, becomes a story arc in your comics: its issues in the list's order, matched to what you have.</p>
      <div className="import-community">
        <label className="form-field"><span>Find a community list</span>
          <input type="search" value={communityQuery} placeholder="War of the Realms" onChange={(event) => setCommunityQuery(event.target.value)} />
        </label>
        {community.query.length >= 2 ? (community.lists.length
          ? <CommunityListRows lists={community.lists} busyId={picked} onImport={(entry) => send(false, entry)} />
          : <p className="discover-note">No community list matches &ldquo;{community.query}&rdquo; yet.</p>) : null}
      </div>
      <form onSubmit={(event) => { event.preventDefault(); send(false); }}>
        <label className="form-field"><span>Or a reading list file</span>
          <input type="file" accept=".cbl,.json,.xml,application/json,text/xml" onChange={(event) => { setFile(event.target.files?.[0] || null); setUrl(""); }} />
          <small>.cbl or .json, up to 2 MB.</small>
        </label>
        <label className="form-field"><span>Or a link to one</span>
          <input type="url" inputMode="url" value={url} placeholder="https://…/reading-list.cbl" onChange={(event) => { setUrl(event.target.value); if (event.target.value) setFile(null); }} disabled={Boolean(file)} />
          <small>A link is remembered, so the arc can be refreshed from it later.</small>
        </label>
        {duplicate ? <div className="replacement-summary import-list-duplicate">
          <WarningCircle size={19} weight="fill" />
          <span><strong>{duplicate.message}</strong><small>Open it, or import this one as well.</small></span>
          <span className="reading-list-edit-actions">
            <button type="button" className="ghost-button" onClick={() => onImported({ id: duplicate.id, existed: true })}>Open</button>
            <button type="button" className="ghost-button" onClick={() => send(true, duplicate.entry)} disabled={busy}>Import anyway</button>
          </span>
        </div> : null}
        {error ? <p className="workbench-error" role="alert">{error}</p> : null}
        <p className="import-list-credit">Community reading lists, such as DieselTech's CBL-ReadingLists, are their authors' work. Import what you have the right to use.</p>
        <div className="metadata-edit-actions">
          <button type="button" className="ghost-button" onClick={onClose}>Cancel</button>
          <button type="submit" className="primary-button" aria-busy={busy || undefined} disabled={busy || (!file && !url.trim())}>
            {busy ? <LoadingSpinner size={18} /> : <UploadSimple size={18} />} {busy ? "Importing…" : "Import"}
          </button>
        </div>
      </form>
    </section>
  </div>;
}

/**
 * Discover: browse this week, or search every catalog.
 *
 * One screen with two states rather than two routes. The old split put the
 * search box on Discover and its results on a route of their own, so the
 * screen a reader landed on could only be used by people who already knew what
 * they wanted.
 */
// Two pages in one. mode "discover" is the week's releases, with a phone's
// search over them; mode "search" is the Search page above 640px -- the same
// results, over your library and the catalogs, and nothing else.
function DiscoverView({
  mode = "discover", query, draft = "", onDraft, onLeaveSearch, runCollections = [], onOpenRunCollection, onOpenList,
  catalog, backendStatus, onSearch, onClearSearch,
  onOpenSeries, onOpenCollection, onDiscoverRequest, onUnfollowRun, onPullIssue, onPullIssues, onPullArc, onSaveArc, onReadArc,
  onImportCommunity,
}) {
  const viewer = useViewer();
  const phone = usePhoneWidth();
  const narrow = useNarrowWidth();
  const catalogsAllowed = can(viewer, "discover.search");
  // Search's library half follows the field as you type; the catalogs are
  // asked only for what was submitted (`query`).
  const liveQuery = mode === "search" ? String(draft ?? "").trim() : "";
  const submittedMatches = Boolean(query) && String(query).trim() === liveQuery;
  const [releases, setReleases] = useState(() => {
    const last = lastAnswer("/api/v1/discover/releases");
    return last ? { state: "done", data: last } : { state: "loading", data: null };
  });
  const [discovery, setDiscovery] = useState(
    { state: "idle", results: [], error: "", providersChecked: [], providersAnswered: [], fallbacks: [] });
  const [pulled, setPulled] = useState({});
  const [drawer, setDrawer] = useState(null);
  // Story arcs matching the search, asked beside it so runs are not held up.
  const [arcs, setArcs] = useState([]);
  const latestQuery = useRef(query);
  latestQuery.current = query;
  const searching = Boolean(query);
  const [recent, setRecent] = useState(() => readRecent(profileStorage()));
  function remember(kind, item) {
    setRecent((current) => {
      const next = rememberRecent(current, recentEntry(kind, item));
      writeRecent(profileStorage(), next);
      return next;
    });
  }
  function clearRecent() {
    setRecent([]);
    writeRecent(profileStorage(), []);
  }
  const openLibraryRun = (item) => item.isCollectionSeries ? onOpenCollection(item.collection) : onOpenSeries(item);
  const openCatalogRun = (run) => setDrawer({ kind: "run", item: run });

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

  async function loadReleases() {
    // The skeleton only when there is nothing to show yet; a refresh of
    // shelves already up lands behind them.
    setReleases((current) => (current.data ? current : { state: "loading", data: null }));
    try {
      setReleases({ state: "done", data: await apiRequest("/api/v1/discover/releases") });
    } catch (error) {
      setReleases({ state: "done", data: { available: true, error: error.message } });
    }
  }
  useEffect(() => {
    if (searching || mode === "search" || backendStatus === "offline") return;
    loadReleases();
  }, [searching, mode, backendStatus]);

  async function searchProviders(value = query) {
    const cleaned = String(value || "").trim();
    if (cleaned.length < 2 || backendStatus === "offline") {
      setDiscovery({ state: "done", results: [], providersChecked: [], providersAnswered: [], fallbacks: [],
        error: backendStatus === "offline" ? "Library service is unavailable" : "" });
      return;
    }
    setDiscovery({ state: "loading", results: [], error: "", providersChecked: [], providersAnswered: [], fallbacks: [] });
    setArcs([]);
    apiRequest(`/api/v1/discover/arcs?query=${encodeURIComponent(cleaned)}`)
      .then((result) => { if (cleaned === String(latestQuery.current || "").trim()) setArcs(result.arcs || []); })
      .catch(() => setArcs([]));
    try {
      // One answer: titles, a creator's runs and a publisher's, ranked together.
      const result = await apiRequest(`/api/v1/discover?query=${encodeURIComponent(cleaned)}`);
      setDiscovery({ state: "done", results: result.results || [], error: "",
        providersChecked: result.providersChecked || [], providersAnswered: result.providersAnswered || [],
        fallbacks: result.fallbacks || [], didYouMean: result.didYouMean || [],
        stillLooking: Boolean(result.stillLooking) });
    } catch (error) {
      setDiscovery({ state: "done", results: [], error: error.message,
        providersChecked: [], providersAnswered: [], fallbacks: [] });
    }
  }
  useEffect(() => {
    if (searching && catalogsAllowed) searchProviders(query);
  }, [query, backendStatus]);
  // Every search run is remembered, however it was started: the field, a
  // "Did you mean", or Comics sending a search on.
  useEffect(() => { if (query) remember("query", query); }, [query]);

  function mark(key, state) { setPulled((current) => ({ ...current, [key]: state })); }
  // A reader's pull is a request: its card says so until the admin decides.
  const settledBy = (result) => (result?.requested ? PULL_STATES.requested : PULL_STATES.queued);
  // What this profile has asked for and is still waiting on, from any device.
  const waiting = useMemo(() => waitingKeys(catalog?.memberRequests), [catalog]);
  async function pullIssue(issue) {
    const key = issueKey(issue);
    mark(key, PULL_STATES.pending);
    const result = await onPullIssue(issue);
    mark(key, result?.ok ? settledBy(result) : PULL_STATES.idle);
  }
  async function pullRun(item) {
    const key = `run:${item.provider}-${item.providerSeriesId}`;
    mark(key, PULL_STATES.pending);
    // A run that has ended is pulled once, whole; following it would watch
    // for issues that will never come.
    const result = discoveryRunStatus(item) === "completed"
      ? await onPullIssues({ provider: item.provider, providerSeriesId: item.providerSeriesId,
          released: true, title: item.title, query })
      : await onDiscoverRequest({ ...item, query });
    mark(key, result?.ok ? settledBy(result) : PULL_STATES.idle);
  }
  // From the run drawer. Following, or taking every released issue or the
  // whole of an ended run, settles the card behind it; a handful of chosen
  // issues does not, because the card's own button is still on offer.
  const runKey = (item) => `run:${item?.provider}-${item?.providerSeriesId}`;
  async function followFromDrawer(target) {
    const result = await onDiscoverRequest(target);
    if (result?.ok && drawer?.item) mark(runKey(drawer.item), settledBy(result));
    return result;
  }
  // Unfollowing frees the card to offer the run again.
  async function unfollowFromDrawer(target) {
    const result = await onUnfollowRun(target);
    if (result?.ok && drawer?.item) mark(runKey(drawer.item), PULL_STATES.idle);
    return result;
  }
  async function pullFromDrawer(target) {
    const result = await onPullIssues(target);
    if (result?.ok && (target.released || target.complete) && drawer?.item) mark(runKey(drawer.item), settledBy(result));
    return result;
  }

  const libraryState = libraryMatchState(catalog, backendStatus, libraryMatches);
  const data = releases.data || {};
  const picks = useMemo(() => weeklyPicks(data), [data]);
  const { fresh, ownedCount } = splitSearchResults(discovery.results);
  const progress = providerProgress(discovery);
  const outstanding = progress.filter((item) => item.status === "searching");
  // Search's field: the sidebar's above 900px; below, the page's own, in the
  // header, ready to type into.
  const searchField = {
    value: draft,
    onChange: (value) => onDraft?.(value),
    onSubmit: () => onSearch(draft),
    onClear: () => { onDraft?.(""); if (searching) onClearSearch(); },
    onEscape: true, autoFocus: true,
    label: catalogsAllowed ? "Search your library and the comic catalogs" : "Search your library",
    placeholder: "Title, creator or publisher",
  };
  const libraryCount = libraryMatches.length + collectionMatchCards.length + arcMatchCards.length;
  // Story arcs as you type, from the names the server keeps: Metron's arcs,
  // and for the admin the community's reading lists. Once the catalogs are
  // searched, Metron's own (forgiving) answer joins them.
  const hints = useArcSuggestions(liveQuery, catalogsAllowed && mode === "search");
  const arcRows = useMemo(() => {
    const seen = new Set();
    return [...(submittedMatches ? arcs : []), ...hints.arcs].filter((arc) => !seen.has(arc.providerArcId) && seen.add(arc.providerArcId));
  }, [submittedMatches, arcs, hints.arcs]);
  const [importing, setImporting] = useState(null);
  async function importCommunity(entry) {
    setImporting(entry.id);
    try { await onImportCommunity?.(entry); } finally { setImporting(null); }
  }

  return <>
    <PageHeader
      title={mode === "search" ? (liveQuery ? `Results for “${liveQuery}”` : "Search") : "Discover"}
      search={mode === "search" && narrow ? "page" : "none"}
      field={searchField}
      searchButton={mode !== "search"}
      leading={mode === "search" && phone ? <button type="button" className="glass-button glass-button--icon" onClick={onLeaveSearch} aria-label="Close search"><ArrowLeft size={20} /></button> : null}
    />

    {mode === "search" && liveQuery ? <>
      <section className="discover-results" aria-label="In your library">
        {/* No count until there is one: "0" before the library has arrived is
            a claim, not a placeholder. */}
        <header><h2>{libraryState === "loading" ? null : <>{libraryCount} </>}<span>In Your Library</span></h2></header>
        {libraryState === "loading" ? <div className="library-match-row" role="status" aria-busy="true">
          <span className="sr-only">Reading your library</span>
          {[0, 1, 2].map((item) => <LibraryMatchSkeleton key={item} />)}
        </div> : libraryCount ? <div className="library-match-row">
          {collectionMatchCards.map((card) => <LibraryMatchCard series={{ id: card.key, title: card.name, cover: card.cover, year: `${card.runCount} run${card.runCount === 1 ? "" : "s"}` }}
            onOpen={() => onOpenRunCollection?.(card)} key={card.key} />)}
          {libraryMatches.map((series) => <LibraryMatchCard series={series}
            onOpen={(item) => { remember("series", item); openLibraryRun(item); }}
            key={series.id} />)}
          {arcMatchCards.map((list) => <LibraryMatchCard series={{ id: `arc-${list.id}`, title: list.name, cover: list.cover, year: arcYears(list) || "Story arc" }}
            onOpen={() => onOpenList?.(list)} key={`arc-${list.id}`} />)}
        </div> : <p className="discover-note">Nothing in your library matches “{liveQuery}”.</p>}
      </section>

      {catalogsAllowed && arcRows.length ? <section className="discover-results" aria-label="Story arcs">
        <header><h2>{arcRows.length} <span>{arcRows.length === 1 ? "Story Arc" : "Story Arcs"}</span></h2></header>
        <div className="arc-results">
          {arcRows.map((arc) => <button type="button" className="arc-row" onClick={() => setDrawer({ kind: "arc", arc })} key={arc.providerArcId}>
            <span className="arc-row-icon" aria-hidden="true"><Books size={20} /></span>
            <span><strong>{arc.name}</strong><small>Story arc &middot; Metron</small></span>
            <CaretRight size={16} aria-hidden="true" />
          </button>)}
        </div>
      </section> : null}
      {catalogsAllowed && hints.lists.length && onImportCommunity ? <section className="discover-results" aria-label="Community reading lists">
        <header><h2>{hints.lists.length} <span>{hints.lists.length === 1 ? "Community Reading List" : "Community Reading Lists"}</span></h2></header>
        <CommunityListRows lists={hints.lists} busyId={importing} onImport={importCommunity} />
      </section> : null}

      {catalogsAllowed && !submittedMatches && liveQuery.length >= 2 ? <button type="button" className="arc-row search-catalogs-row" onClick={() => onSearch(liveQuery)}>
        <span className="arc-row-icon" aria-hidden="true"><MagnifyingGlass size={20} /></span>
        <span><strong>Search the catalogs for “{liveQuery}”</strong><small>New runs and story arcs from Metron, Comic Vine and the GCD. Or press Enter.</small></span>
        <CaretRight size={16} aria-hidden="true" />
      </button> : null}

      {catalogsAllowed && submittedMatches ? <>

      <section className="discover-results" aria-label="New matches">
        <header>
          <h2>{discovery.state === "loading" ? null : <>{fresh.length} </>}<span>{fresh.length === 1 ? "New Match" : "New Matches"}</span></h2>
          {discovery.state === "loading" || discovery.fallbacks?.length ? <p className="provider-progress" role="status" aria-live="polite">
            {/* The providers asked are only known once the answer arrives. */}
            {discovery.state === "loading" && !progress.length ? <span>Searching titles, creators and publishers…</span> : null}
            {progress.map((item) => <span className={`provider-${item.status}`} key={item.name}
              title={item.error || undefined}>
              {item.name}{item.status === "searching" ? " searching…" : item.status === "failed" ? " unavailable" : ""}
            </span>)}
          </p> : null}
        </header>
        {discovery.state === "loading" ? <div className="new-run-grid" role="status" aria-busy="true">
          <span className="sr-only">Searching comic catalogs</span>
          {[0, 1, 2, 3, 4].map((item) => <NewRunCardSkeleton key={item} />)}
        </div> : null}
        {discovery.state === "done" && fresh.length ? <div className="new-run-grid">
          {fresh.map((item) => <NewRunCard item={item}
            state={pulled[`run:${item.provider}-${item.providerSeriesId}`]
              || (runRequested(waiting, item.provider, item.providerSeriesId) ? PULL_STATES.requested : PULL_STATES.idle)}
            onPull={pullRun} onOpen={(run) => { remember("run", run); openCatalogRun(run); }}
            key={`${item.provider}-${item.providerSeriesId}`} />)}
        </div> : null}
        {discovery.state === "done" && !fresh.length ? <div className="shelf-message">
          <MagnifyingGlass size={18} />
          <span>
            <strong>{discovery.error ? "Online search needs another try"
              : ownedCount ? `All ${ownedCount} matching run${ownedCount === 1 ? "" : "s"} are already in your library`
              : `No new runs match “${query}”`}</strong>
            <small>{discovery.error || "Try the exact title, a creator's full name or a publisher, and add a four-digit year to narrow it."}</small>
          </span>
          {discovery.error ? <button type="button" onClick={() => searchProviders(query)}>Try again</button> : null}
        </div> : null}
        {outstanding.length && fresh.length ? <p className="discover-note">
          Still hearing from {outstanding.map((item) => item.name).join(" and ")}.
        </p> : null}
        {discovery.state === "done" && discovery.stillLooking ? <p className="discover-people-note">
          Still looking up creators and publishers.{" "}
          <button type="button" className="discover-text-button" onClick={() => searchProviders(query)}>Look again</button>
        </p> : null}
        {discovery.state === "done" && discovery.didYouMean?.length ? <p className="discover-people-note did-you-mean">
          <span>Did you mean</span>
          {discovery.didYouMean.map((name) => <button type="button" key={name} onClick={() => onSearch(name)}>{name}</button>)}
        </p> : null}
      </section>
      </> : null}
    </> : mode === "search" ? <>
      <p className="search-hint">{catalogsAllowed
        ? "Type a title, a creator's full name or a publisher. Your library answers as you type; press Enter to search the comic catalogs too."
        : "Type a title, a creator's full name or a publisher to find it in your library."}</p>
      <RecentSearches entries={recent} allSeries={allSeries} onClear={clearRecent} onSearch={onSearch}
        onOpenSeries={(item) => { remember("series", item); openLibraryRun(item); }}
        onOpenRun={(run) => { remember("run", run); openCatalogRun(run); }} />
    </> : <>
      {/* What this week is worth looking at, ranked against the library. It is
          only drawn when there is something to say: when Metron is not
          connected, or a week failed, the weeks below already say so, and
          saying it twice on one screen is noise. */}
      {picks.issues.length ? <ReleaseShelf title="Worth a look this week"
        subtitle={picks.state === "partial"
          ? `Runs you follow, then new #1s — from ${picks.weeksUsed} of ${picks.weeksTotal} weeks, one could not be fetched.`
          : "New issues of runs you follow, then this week's #1s."}
        state="ready" issues={picks.issues}
        pulled={pulled} waiting={waiting} onPull={pullIssue} onOpen={(issue) => setDrawer({ kind: "issue", issue })} /> : null}
      <ReleaseShelf title="Latest Releases" date={formatShelfDate(data.latest?.date)}
        state={shelfState(data.latest, data.available, releases.state === "loading")}
        issues={data.latest?.issues} error={data.latest?.error || data.error}
        pulled={pulled} waiting={waiting} onPull={pullIssue} onOpen={(issue) => setDrawer({ kind: "issue", issue })}
        onRetry={loadReleases} />
      <ReleaseShelf title="Upcoming Releases" date={formatShelfDate(data.upcoming?.date)}
        state={shelfState(data.upcoming, data.available, releases.state === "loading")}
        issues={data.upcoming?.issues} error={data.upcoming?.error || data.error}
        pulled={pulled} waiting={waiting} onPull={pullIssue} onOpen={(issue) => setDrawer({ kind: "issue", issue })}
        onRetry={loadReleases} />
      {/* The week before last: a comic is easy to miss by a few days, and by
          the time you look the shelf it was on has moved up. */}
      <ReleaseShelf title="Previous Releases" date={formatShelfDate(data.previous?.date)}
        state={shelfState(data.previous, data.available, releases.state === "loading")}
        issues={data.previous?.issues} error={data.previous?.error || data.error}
        pulled={pulled} waiting={waiting} onPull={pullIssue} onOpen={(issue) => setDrawer({ kind: "issue", issue })}
        onRetry={loadReleases} />
    </>}
    {drawer?.kind === "issue" ? <DiscoverIssueDrawer issue={drawer.issue}
      state={pullState(drawer.issue, pulled, waiting)} onPull={pullIssue}
      onOpenRun={(item) => setDrawer({ kind: "run", item })} onClose={() => setDrawer(null)} /> : null}
    {drawer?.kind === "arc" ? <StoryArcDrawer arc={drawer.arc} waiting={waiting} onPull={onPullArc} onSave={onSaveArc} onRead={onReadArc}
      onClose={() => setDrawer(null)} /> : null}
    {drawer?.kind === "run" ? <DiscoverRunDrawer item={drawer.item} query={query}
      settled={pulled[runKey(drawer.item)]
        || (runRequested(waiting, drawer.item.provider, drawer.item.providerSeriesId) ? PULL_STATES.requested : undefined)}
      followRequested={waiting.has(requestKey("discover_run", drawer.item))}
      onFollow={followFromDrawer} onUnfollow={unfollowFromDrawer} onPullIssues={pullFromDrawer}
      onClose={() => setDrawer(null)} /> : null}
  </>;
}

const PREVIEW_PROVIDER_NAMES = { metron: "Metron", comic_vine: "Comic Vine", gcd: "Grand Comics Database" };

// A drawer's top bar, after Plex's detail page. It stays pinned: at the top
// it is clear, with the close control as a glass circle over the art; once
// the title has scrolled under it, it frosts and takes the title, and its
// controls lose their circles (no glass on glass). While the drawer moves,
// it also tells the header how far it has gone -- the art darkens and drifts
// at a slower pace than the page, and stretches when pulled down at the top.

// A label such as "Pull 12 released issues" as the band's verb and its object.
function splitAction(text) {
  const [verb, ...rest] = String(text || "").split(" ");
  return { verb, noun: rest.join(" ") };
}

/**
 * One comic from a release shelf.
 *
 * What the shelf already knows -- cover, title, dates -- is drawn at once;
 * the description and credits come from Metron after the drawer opens, behind
 * placeholder lines rather than a spinner in an empty panel.
 */
function DiscoverIssueDrawer({ issue, state, onPull, onOpenRun, onClose }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  const reader = !isAdmin(useViewer());
  const [detail, setDetail] = useState({ state: "loading", data: null });
  useEffect(() => {
    let live = true;
    if (!issue?.providerIssueId) { setDetail({ state: "done", data: null }); return undefined; }
    setDetail({ state: "loading", data: null });
    apiRequest(`/api/v1/discover/issue?id=${encodeURIComponent(issue.providerIssueId)}`)
      .then((data) => { if (live) setDetail({ state: "done", data }); })
      .catch(() => { if (live) setDetail({ state: "error", data: null }); });
    return () => { live = false; };
  }, [issue?.providerIssueId]);
  const data = detail.data || {};
  const toned = toneProps(useArtTone(issue.cover));
  const facts = [
    ["Ships", formatLongDate(data.storeDate || issue.storeDate)],
    ["Cover date", formatLongDate(data.coverDate || issue.coverDate)],
    ["Pages", data.pageCount],
    ["Price", data.price ? `$${data.price}` : null],
  ].filter(([, value]) => value);
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer comic-drawer discover-drawer${toned.className} ${closing ? "closing" : ""}`} style={toned.style} ref={dialogRef}
      role="dialog" aria-modal="true" aria-labelledby="discover-issue-title"
      onMouseDown={(event) => event.stopPropagation()}>
      <DiscoverDrawerHero art={issue.cover} titleId="discover-issue-title" title={issue.title}
        cover={<DiscoverCover src={issue.cover} alt={`${issue.title} cover`} glyph={30} />}
        byline={[issue.publisher, issue.seriesTitle].filter(Boolean).join(" • ")}
        onClose={requestClose} closeLabel="Close issue details">
        {issue.storeDate || data.storeDate ? <StatusBadge tone="muted">Ships {formatLongDate(data.storeDate || issue.storeDate)}</StatusBadge> : null}
      </DiscoverDrawerHero>
      {/* Pull is the band's, as Read is a library drawer's (2026-10-01). */}
      <DrawerPrimary icon={state === PULL_STATES.requested ? <Hourglass size={20} /> : state === PULL_STATES.idle ? <PullIcon /> : <FollowingIcon size={20} />}
        verb={state === PULL_STATES.idle ? (reader ? "Request" : "Pull") : (reader ? READER_PULL_LABELS : PULL_LABELS)[state]}
        noun={state === PULL_STATES.idle ? "this issue" : ""}
        busy={state === PULL_STATES.pending} settled={state !== PULL_STATES.idle && state !== PULL_STATES.pending}
        disabled={state !== PULL_STATES.idle} onClick={() => onPull(issue)} />
      <div className="comic-drawer-body">
        {detail.state === "loading" ? <RunSynopsis loading heading="Story" /> : null}
        {detail.state === "error" ? <p className="discover-note">Metron&rsquo;s details for this issue are unavailable right now.</p> : null}
        {detail.state === "done" ? <>
          {data.storyTitles?.length ? <p className="discover-story-titles">{data.storyTitles.join(" · ")}</p> : null}
          {data.description ? <RunSynopsis text={data.description} source="Metron" /> : <p className="discover-note">Metron has no description for this issue.</p>}
          {data.creators?.length ? <ComicDrawerCreators creators={data.creators} /> : null}
        </> : null}
        {issue.providerSeriesId && onOpenRun ? <ComicDrawerRow title="The run" count={1}>
          <ComicDrawerRunCard run={{ id: `metron-${issue.providerSeriesId}`, title: issue.seriesTitle, year: issue.seriesYear, cover: issue.cover }}
            onOpen={() => onOpenRun({
              provider: "metron", providerSeriesId: issue.providerSeriesId,
              providerIds: { metron: issue.providerSeriesId },
              title: issue.seriesTitle, yearBegan: issue.seriesYear,
              publisher: issue.publisher, cover: issue.cover,
            })} />
        </ComicDrawerRow> : null}
        {facts.length ? <div className="drawer-facts">
          {facts.map(([label, value]) => <span key={label}><strong>{value}</strong>{label}</span>)}
        </div> : null}
      </div>
    </aside>
  </div>;
}

// A catalog run, as the comics drawer shows a library one: the art, then --
// on one page, no tabs -- how to take it, stacked. Follow is the comics
// drawer's band (a run that has ended has nothing to follow, so it has none);
// under it one card pulls every released issue (or the whole of an ended
// run), and one opens into the issue list to pick from. The story follows.
// A story arc: its issues in reading order across every series it runs
// through, what the library holds of each, and one button for the rest --
// Pull arc, or for a reader, Request arc.
function StoryArcDrawer({ arc, waiting, onPull, onSave, onRead, onClose }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  const reader = !isAdmin(useViewer());
  const [detail, setDetail] = useState({ state: "loading", data: null, error: "" });
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(waiting?.has(requestKey("discover_arc", { arcId: arc.providerArcId })) ? "Requested. Waiting for approval." : "");
  async function load() {
    setDetail({ state: "loading", data: null, error: "" });
    try {
      setDetail({ state: "done", data: await apiRequest(`/api/v1/discover/arc?id=${encodeURIComponent(arc.providerArcId)}`), error: "" });
    } catch (error) {
      setDetail({ state: "error", data: null, error: error.message });
    }
  }
  useEffect(() => { load(); }, [arc.providerArcId]);
  const data = detail.data;
  const issues = data?.issues || [];
  const seriesNames = [...new Set((data?.series || []).map((series) => `${series.title}${series.year ? ` (${series.year})` : ""}`))];
  async function pull() {
    if (!data || busy) return;
    setBusy(true);
    const result = await onPull({ arcId: arc.providerArcId, name: data.name || arc.name, cover: data.cover, issueCount: issues.length });
    setBusy(false);
    if (result?.ok) {
      setDone(result.requested ? "Requested. Waiting for approval." : reader ? "On the way." : "On your Pull List.");
      load();
    }
  }
  const nothingLeft = Boolean(data) && !data.missing;
  const saved = Boolean(data?.readingListId);
  // One action. Pulling keeps the arc as well -- the issues without their
  // order would be the gap a saved arc exists to close -- so a second "save
  // only" card read as the same thing minus something (owner, 2026-09-28).
  // With nothing left to pull, the one thing to do is keep it; pruning
  // tie-ins happens in the arc's own drawer afterwards.
  const saveOnly = nothingLeft && !reader;
  const verb = saveOnly ? "Save" : reader ? "Request" : "Pull";
  const label = !data ? `${verb} arc` : saveOnly ? "Save arc" : data.missing === issues.length ? `${verb} all ${issues.length} issues`
    : data.missing ? `${verb} ${data.missing} missing issue${data.missing === 1 ? "" : "s"}` : `${verb} arc`;
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState("overview");
  const [tabsRef, tabGlass] = useGlassIndicator("button.active", [tab, detail.state]);
  async function save() {
    if (!data || saving) return;
    setSaving(true);
    const result = await onSave?.({ arcId: arc.providerArcId, name: data.name || arc.name });
    setSaving(false);
    if (result?.ok) load();
  }
  const toned = toneProps(useArtTone(data?.cover));
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer comic-drawer discover-drawer${toned.className} ${closing ? "closing" : ""}`} style={toned.style} ref={dialogRef}
      role="dialog" aria-modal="true" aria-labelledby="story-arc-title" onMouseDown={(event) => event.stopPropagation()}>
      <DiscoverDrawerHero art={data?.cover} titleId="story-arc-title" title={data?.name || arc.name}
        cover={<DiscoverCover src={data?.cover} alt={`${arc.name} cover`} glyph={30} />}
        byline={arcYears(data || {}) || null}
        onClose={requestClose} closeLabel="Close story arc"
        ownership={data && issues.length ? <Ownership series={arcOwnership({ owned: data.owned, issueCount: issues.length, missing: data.missing })} compact /> : null}>
        <StatusBadge tone="violet">Story arc</StatusBadge>
        {saved ? <StatusBadge tone="muted">In your comics</StatusBadge> : null}
      </DiscoverDrawerHero>
      {/* One action, the band's, as a library drawer's Read is: Read once the
          arc is kept, else Pull (Request, or Save when nothing is missing). */}
      {saved ? (onRead ? <DrawerPrimary icon={<BookOpen size={20} weight="fill" />} verb="Read" noun="in order"
        onClick={() => onRead({ listId: data.readingListId, listName: data.name || arc.name })}
        note="Kept in your comics, under Story arcs." /> : null)
        : <DrawerPrimary icon={saveOnly ? <ListPlus size={20} /> : <PullIcon />} {...splitAction(busy ? `${reader ? "Requesting" : "Pulling"}…` : saving ? "Saving…" : label)}
          busy={busy || saving} disabled={!data || Boolean(done)} settled={Boolean(done)} onClick={saveOnly ? save : pull}
          note={done || (detail.state === "loading" ? "Getting the arc’s issues…"
            : reader ? "Saved to your comics once approved, in reading order."
            : saveOnly ? "Every issue is here or on the way. Keep the reading order in your comics."
            : `${seriesNames.length > 1 ? `Across ${seriesNames.length} series, in reading order.` : "In reading order."} The arc is kept in your comics.`)} />}
      <DrawerTabs tabs={[["overview", "Overview"], ["issues", data ? `Issues (${issues.length})` : "Issues"]]} tab={tab} onTab={setTab}
        label="Story arc details" tabsRef={tabsRef} glass={tabGlass} />
      <div className="comic-drawer-body" key={tab}>
        {detail.state === "error" ? <div className="shelf-message">
          <WarningCircle size={18} />
          <span><strong>This arc&rsquo;s issues could not be listed</strong><small>{detail.error}</small></span>
          <button type="button" onClick={load}>Try again</button>
        </div> : null}
        {tab === "overview" ? <>
          {detail.state === "loading" ? <RunSynopsis loading /> : null}
          {data?.description ? <RunSynopsis text={data.description} source="Metron" /> : null}
          {data && !data.description ? <p className="discover-note">Metron has no description for this arc. Its issues, in reading order, are under Issues.</p> : null}
          {data && seriesNames.length ? <section className="discover-drawer-section"><h3>Series in it</h3><p>{seriesNames.join(" · ")}</p></section> : null}
        </> : null}
        {tab === "issues" ? (detail.state === "loading" ? <div role="status" aria-busy="true">
          {[0, 1, 2, 3].map((row) => <div className="discover-issue-row discover-issue-skeleton" aria-hidden="true" key={row}><i /><span className="discover-cover" /><span><i /><i /></span></div>)}
        </div> : <div className="discover-issue-list arc-issue-list">
          {issues.map((issue) => <div className="discover-issue-row" key={issue.providerIssueId}>
            <DiscoverCover src={issue.cover} alt="" glyph={16} />
            <span>
              <strong>{issue.seriesTitle} #{issue.number}</strong>
              <small>{[seriesNames.length > 1 && issue.seriesYear ? `${issue.seriesTitle} (${issue.seriesYear})` : null, formatLongDate(issue.coverDate),
                issue.owned ? "In library" : issue.queued ? (reader ? "On the way" : "On Pull List") : null].filter(Boolean).join(" · ") || " "}</small>
            </span>
          </div>)}
        </div>) : null}
      </div>
    </aside>
  </div>;
}

/**
 * A run's issues, and how much of the run to take.
 *
 * Three ends, said before the button is pressed: following keeps watching for
 * new issues; the other two take what is chosen, once. Ticking an issue is
 * choosing, so it switches the mode itself rather than asking first.
 */
function DiscoverRunDrawer({ item, query, settled, followRequested = false, onFollow, onUnfollow, onPullIssues, onClose }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  // A reader asks: Follow becomes "Request follow", Pull becomes "Request".
  const reader = !isAdmin(useViewer());
  const verb = reader ? "Request" : "Pull";
  const [preview, setPreview] = useState({ state: "loading", data: null, error: "" });
  const [selected, setSelected] = useState(() => new Set());
  // Which action is running, and what the last one said, by action.
  const [busy, setBusy] = useState("");
  const [done, setDone] = useState(settled === PULL_STATES.queued ? { whole: reader ? "Already on the way." : "Already on your Pull List." }
    : followRequested ? { follow: "Follow requested. Waiting for approval." } : {});
  // A follow asked for, as opposed to some of the run's issues.
  const [asked, setAsked] = useState(followRequested);
  const ids = runPreviewIds(item);
  const idsKey = JSON.stringify(ids);
  const source = ["metron", "comic_vine", "gcd"].find((provider) => ids[provider]);
  async function load() {
    setPreview({ state: "loading", data: null, error: "" });
    const params = new URLSearchParams({ ...ids, query: query || item.title || "" });
    try {
      setPreview({ state: "done", data: await apiRequest(`/api/v1/discover/run?${params}`), error: "" });
    } catch (error) {
      setPreview({ state: "error", data: null, error: error.message });
    }
  }
  useEffect(() => { load(); }, [idsKey]);
  const run = preview.data;
  const issues = run?.issues || [];
  // The preview's own reading outranks the search row's, which is often
  // silent about whether a run has ended.
  const status = run?.publicationStatus || discoveryRunStatus(item);
  const modes = runModes(status).map(([id]) => id);
  const canFollow = modes.includes("follow");
  const completed = modes.includes("complete");
  // What the switch last did outranks the preview until the preview catches up.
  const [followed, setFollowed] = useState(null);
  const following = followed ?? Boolean(run?.following);
  const wholeMode = completed ? "complete" : "released";
  const wholeSummary = runPullSummary(wholeMode, issues);
  const chooseSummary = runPullSummary("choose", issues, [...selected]);
  function toggle(number) {
    setDone((current) => ({ ...current, choose: "" }));
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(number)) next.delete(number); else next.add(number);
      return next;
    });
  }
  async function commit(action) {
    // Only a run the library holds can be unfollowed, and it is known by its id.
    if (!run || busy || (action === "unfollow" && !run.runId)) return;
    setBusy(action);
    const target = {
      provider: run.provider, providerSeriesId: run.providerSeriesId,
      title: run.title, query: query || run.title,
    };
    const result = action === "follow" ? await onFollow(target)
      : action === "unfollow" ? await onUnfollow({ runId: run.runId, title: run.title })
      : await onPullIssues(action === "choose" ? { ...target, numbers: [...selected] }
        : completed
          ? { ...target, complete: true, numbers: completeRunToPull(issues).map((issue) => issue.number) }
          : { ...target, released: true });
    setBusy("");
    if (result?.ok) {
      if (result.requested) {
        // Asked, not done: nothing is followed or pulled until the admin says so.
        if (action === "follow") setAsked(true);
        setDone((current) => ({ ...current, [action === "follow" ? "follow" : action]:
          action === "follow" ? "Follow requested. Waiting for approval." : "Requested. Waiting for approval." }));
      } else if (action === "follow" || action === "unfollow") {
        setFollowed(action === "follow");
        setDone((current) => ({ ...current, follow: action === "follow" ? "Following this run." : "No longer following this run." }));
      } else {
        setDone((current) => ({ ...current, [action]: reader ? "On the way." : "On your Pull List." }));
      }
      if (action === "choose") setSelected(new Set());
      load();
    }
  }
  const yearLabel = item.yearLabel || run?.year || item.yearBegan;
  const art = run?.cover || item.cover;
  const toned = toneProps(useArtTone(art));
  const waiting = preview.state === "loading" ? "Getting the issue list…" : "";
  const title = run?.title || item.title;
  // The library run drawer's frame (2026-10-01): Follow in the top bar as the
  // eye, the whole pull as the band's pill, then Overview and Issues.
  const [tab, setTab] = useState("overview");
  const [tabsRef, tabGlass] = useGlassIndicator("button.active", [tab, preview.state]);
  const ownedCount = issues.filter((issue) => issue.owned).length;
  const whole = splitAction((run ? wholeSummary.label : completed ? "Pull complete run" : "Pull all released").replace(/^Pull/, verb));
  const followButton = !canFollow ? null : reader
    ? <button type="button" className={`glass-button glass-button--icon comic-drawer-follow-button${following || asked ? " active" : ""}`}
      onClick={() => commit("follow")} disabled={!run || busy === "follow" || following || asked}
      aria-label={following ? `Following ${title}` : asked ? `Following ${title} is requested` : `Request a follow of ${title}`}
      title={following ? "Following" : asked ? "Waiting for approval" : "Ask for this run to be followed: new issues as they come out"}>
      {busy === "follow" ? <LoadingSpinner size={18} /> : asked && !following ? <Hourglass size={20} /> : <FollowedIcon size={20} />}
      <b>{following ? "Following" : asked ? "Follow requested" : "Request follow"}</b>
    </button>
    : <button type="button" className={`glass-button glass-button--icon comic-drawer-follow-button${following ? " active" : ""}`}
      onClick={() => commit(following ? "unfollow" : "follow")} disabled={!run || busy === "follow" || busy === "unfollow"} aria-pressed={following}
      aria-label={following ? `Stop following ${title}` : `Follow ${title}`} title={following ? "Following" : "Follow run"}>
      {busy === "follow" || busy === "unfollow" ? <LoadingSpinner size={18} /> : <FollowedIcon size={20} />}
      <b>{following ? "Unfollow" : "Follow"}</b>
    </button>;
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer comic-drawer discover-drawer${toned.className} ${closing ? "closing" : ""}`} style={toned.style} ref={dialogRef}
      role="dialog" aria-modal="true" aria-labelledby="discover-run-title"
      onMouseDown={(event) => event.stopPropagation()}>
      <DiscoverDrawerHero art={art} titleId="discover-run-title" title={title}
        cover={<DiscoverCover src={art} alt={`${item.title} cover`} glyph={30} />}
        byline={[run?.publisher || item.publisher, yearLabel].filter(Boolean).join(" • ")}
        onClose={requestClose} closeLabel="Close run details" actions={followButton}
        ownership={run && issues.length ? <Ownership series={arcOwnership({ owned: ownedCount, issueCount: issues.length, missing: issues.length - ownedCount })} compact /> : null}>
        <RunStatusChip status={status} />
        <MonitoringStatus series={{ monitoringStatus: following ? "monitored" : "" }} />
        {(run?.medium || item.medium) === "manga" ? <StatusBadge tone="muted">Manga</StatusBadge> : null}
      </DiscoverDrawerHero>
      <DrawerPrimary icon={<PullIcon />} verb={whole.verb} noun={whole.noun}
        busy={busy === "whole"} disabled={!run || wholeSummary.disabled || Boolean(busy)} settled={Boolean(run) && wholeSummary.disabled}
        note={done.follow || done.whole || waiting || wholeSummary.detail} onClick={() => commit("whole")} />
      <DrawerTabs tabs={[["overview", "Overview"], ["issues", run ? `Issues (${issues.length})` : "Issues"]]} tab={tab} onTab={setTab}
        label="Run details" tabsRef={tabsRef} glass={tabGlass} />
      <div className="comic-drawer-body" key={tab}>
        {preview.state === "error" ? <div className="shelf-message">
          <WarningCircle size={18} />
          <span><strong>This run&rsquo;s issues could not be listed</strong><small>{preview.error}</small></span>
          <button type="button" onClick={load}>Try again</button>
        </div> : null}
        {tab === "overview" ? <>
          {preview.state === "loading" ? <RunSynopsis loading /> : <RunSynopsis text={run?.synopsis} source={run?.providerName} key={idsKey} />}
          {run?.detailsLimited ? <p className="discover-note">The Grand Comics Database lists this run&rsquo;s issue numbers without titles, dates or covers.</p> : null}
          {preview.state === "done" && !run?.synopsis && !run?.detailsLimited ? <p className="discover-note">No description for this run. Its issues are under Issues.</p> : null}
        </> : null}
        {tab === "issues" && preview.state !== "error" ? <div className="discover-issue-list" id="run-issue-picker">
          <header>
            <h3>Choose issues</h3>
            {run ? <span>
              <button type="button" onClick={() => { setSelected(new Set(releasedToPull(issues).map((issue) => issue.number))); setDone((current) => ({ ...current, choose: "" })); }}>Select all released</button>
              <button type="button" onClick={() => setSelected(new Set())} disabled={!selected.size}>Clear</button>
            </span> : null}
          </header>
          {preview.state === "loading" ? <div role="status" aria-busy="true">
            <p className="discover-note">Getting the issue list from {PREVIEW_PROVIDER_NAMES[source] || "the catalogs"}&hellip; a long run takes a little while the first time.</p>
            {[0, 1, 2, 3].map((row) => <div className="discover-issue-row discover-issue-skeleton" aria-hidden="true" key={row}>
              <i /><span className="discover-cover" /><span><i /><i /></span>
            </div>)}
          </div> : issues.map((issue, index) => {
            const available = selectableIssue(issue);
            const note = issue.owned ? "In library" : issue.queued ? (reader ? "On the way" : "On Pull List")
              : issue.releaseState === "upcoming" ? "Not out yet"
              : issue.releaseState === "unknown" ? "Release date unknown" : null;
            const date = formatLongDate(issue.publicationDate) || issue.publicationYear;
            return <label className={`discover-issue-row${available ? "" : " unavailable"}`} key={`${issue.number}-${index}`}>
              <input type="checkbox" checked={selected.has(issue.number)} disabled={!available || Boolean(busy)}
                onChange={() => toggle(issue.number)} aria-label={`Issue ${issue.number}`} />
              <DiscoverCover src={issue.cover} alt="" glyph={16} />
              <span>
                <strong>{issueLabel(issue.number, run?.medium || item.medium)}{issue.title ? ` · ${issue.title}` : ""}</strong>
                <small>{[date, note].filter(Boolean).join(" · ") || " "}</small>
              </span>
            </label>;
          })}
          <footer className="run-pull-choose-actions">
            <small role="status" aria-live="polite">{done.choose || chooseSummary.detail}</small>
            <button type="button" className="pull-button pull-button-md pull-button-idle"
              disabled={!run || chooseSummary.disabled || Boolean(busy)} onClick={() => commit("choose")} aria-busy={busy === "choose" || undefined}>
              <span>{busy === "choose" ? `${verb === "Pull" ? "Pulling" : "Requesting"}…` : (chooseSummary.disabled ? "Pull issues" : chooseSummary.label).replace(/^Pull/, verb)}</span>
              {busy === "choose" ? <LoadingIndicator size={16} /> : <PullIcon />}
            </button>
          </footer>
        </div> : null}
      </div>
    </aside>
  </div>;
}

// The design labels a shelf by its shipping Wednesday, in the reader's own
// notation rather than the ISO date the endpoint speaks.
function formatShelfDate(iso) {
  if (!iso) return "";
  const [year, month, day] = String(iso).split("-");
  return year && month && day ? `${month}/${day}/${year}` : "";
}

// A library scan started from the app, in progress or just finished: on the
// folders page and in Settings. The rail's button shows it as a turning icon.
function ScanProgress({ scanState, scanProgress }) {
  const scanTotal = Number(scanProgress?.total_files || 0);
  const scanProcessed = Number(scanProgress?.processed_files || 0);
  const scanPercent = scanTotal ? Math.min(100, Math.round((scanProcessed / scanTotal) * 100)) : 0;
  if (scanState === "done") return <div className="success-banner"><CheckCircle size={19} weight="fill" /> Scan complete. Your library is up to date.</div>;
  if (scanState !== "scanning") return null;
  return <div className={`scan-progress ${scanTotal ? "determinate" : ""}`} aria-live="polite"><span><strong>{scanTotal ? `Scanning comic ${Math.min(scanProcessed + 1, scanTotal)} of ${scanTotal}` : "Finding comic files…"}</strong><small>{scanProgress?.rootTotal > 1 ? `Folder ${scanProgress.rootIndex} of ${scanProgress.rootTotal} · ${scanProgress.rootPath}` : scanTotal ? `${scanProcessed} complete · reading embedded details, cover art, and file health` : "Counting files before the library scan begins"}</small></span><b>{scanTotal ? `${scanPercent}%` : "Starting"}</b><i style={scanTotal ? { width: `${scanPercent}%` } : undefined} /></div>;
}

function ImportLibraryView({ onNavigate, onStartInventory, onScanLibrary, onUpdateRoot, onRemoveRoot, catalog, backendStatus, scanState, scanProgress }) {
  const [path, setPath] = useState("");
  const [recursive, setRecursive] = useState(true);
  const [pickerState, setPickerState] = useState("");
  const [editingRoot, setEditingRoot] = useState(null);
  const [rootRecursive, setRootRecursive] = useState(true);
  const busy = scanState === "scanning";
  const roots = catalog?.roots || [];
  const lastScan = catalog?.lastScan?.iso ? `${catalog.lastScan.date} ${catalog.lastScan.time}` : "Not scanned yet";
  async function chooseFolder() {
    setPickerState("Opening Finder…");
    try {
      const result = await apiRequest("/api/pick-folder");
      if (result.folder) setPath(result.folder);
      setPickerState(result.folder ? "Folder selected" : "Selection canceled");
    } catch (error) {
      setPickerState(error.message);
    }
  }
  async function removeRoot(root) {
    const confirmed = window.confirm(`Remove ${root.path} from Flipparr?\n\nYour comic files will stay exactly where they are, but this folder's comics will no longer appear in your library.`);
    if (!confirmed) return;
    await onRemoveRoot(root);
    if (editingRoot === root.id) setEditingRoot(null);
  }
  function rootScanLabel(value) {
    if (!value) return "Not scanned yet";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }
  return <><PageHeader title="Import library" narrow leading={<button type="button" className="glass-button glass-button--icon" onClick={() => onNavigate("settings", "library")} aria-label="Back to Library folders"><ArrowLeft size={20} /></button>} />
    <ScanProgress scanState={scanState} scanProgress={scanProgress} />
    {catalogPending(catalog, backendStatus) ? <CatalogLoading title="Loading your library folders…" detail="Checking which folders Flipparr already scans." /> : null}
    {roots.length ? <section className="library-sources-panel"><header><div><span className="eyebrow">Library folders</span><h2>Your comic sources</h2><p>Each folder is scanned independently. Removing one from Flipparr never deletes or moves its files.</p></div><div className="library-scan-action"><span>Last library scan</span><strong>{lastScan}</strong><button className={`primary-button ${busy ? "loading" : ""}`} onClick={onScanLibrary} disabled={busy}>{busy ? <LoadingSpinner size={19} /> : <ArrowsClockwise size={19} />}{busy ? "Scanning…" : "Scan all folders"}</button></div></header><div className="library-source-list">{roots.map((root) => <article className="library-source" key={root.id}><span className="library-source-icon"><FolderOpen size={22} weight="duotone" /></span><div className="library-source-copy"><strong>{root.path}</strong><span>{root.recursive ? "Includes subfolders" : "Top-level comics only"} · {rootScanLabel(root.last_scan_at)}</span></div><div className="library-source-actions"><button className="ghost-button" disabled={busy} onClick={() => onStartInventory(root.path, Boolean(root.recursive))}><ArrowsClockwise size={16} /> Scan</button><button className="ghost-button" disabled={busy} onClick={() => { setEditingRoot(editingRoot === root.id ? null : root.id); setRootRecursive(Boolean(root.recursive)); }}><PencilSimple size={16} /> Manage</button></div>{editingRoot === root.id ? <div className="library-source-editor"><label className="check-row"><input type="checkbox" checked={rootRecursive} onChange={(event) => setRootRecursive(event.target.checked)} /><span><strong>Include subfolders</strong><small>Apply this setting on future scans</small></span></label><div><button className="secondary-button" onClick={async () => { await onUpdateRoot(root, rootRecursive); setEditingRoot(null); }}>Save setting</button><button className="danger-button" onClick={() => removeRoot(root)}>Remove from Flipparr</button></div><small>To change the folder path, add the new folder below, then remove this source.</small></div> : null}</article>)}</div></section> : null}
    <section className="focused-panel add-panel"><div className="panel-icon"><FolderOpen size={30} weight="duotone" /></div><h2>Add another library folder</h2><p>We’ll inventory the issues and volumes already in this folder, use covers and metadata from the files, and check for damaged archives. Online details can be refreshed after the library is visible.</p><label className="form-field"><span>Library folder</span><div className="path-input"><input value={path} placeholder="/comics-archive" onChange={(event) => setPath(event.target.value)} /><button type="button" onClick={chooseFolder}>Choose folder</button></div>{pickerState ? <small>{pickerState}</small> : null}</label><label className="check-row"><input type="checkbox" checked={recursive} onChange={(event) => setRecursive(event.target.checked)} /><span><strong>Include subfolders</strong><small>Useful when each series has its own folder</small></span></label><div className="safety-note"><ShieldCheck size={22} weight="fill" /><span><strong>Your files stay untouched</strong><small>No files will be renamed, moved, or modified during this scan.</small></span></div><div className="docker-path-note"><HardDrive size={20} /><span><strong>Using Docker?</strong><small>Mount each NAS share into the Flipparr container first, then enter its container path here. Avoid adding a folder inside an existing source.</small></span></div><div className="panel-actions"><button className={`primary-button ${busy ? "loading" : ""}`} disabled={busy || !path.trim()} onClick={() => onStartInventory(path, recursive)}>{busy ? <LoadingSpinner size={19} /> : <UploadSimple size={19} />} {busy ? "Scanning…" : "Import and scan folder"}</button><button className="ghost-button" onClick={() => onNavigate("settings", "library")}>Cancel</button></div></section></>;
}

// ---- Readers' requests ----------------------------------------------------------

const REQUEST_TONES = { pending: "amber", failed: "red", approved: "violet", available: "green", declined: "muted", cancelled: "muted" };

function requestedOn(iso) {
  const date = iso ? new Date(iso) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
}

// What a request is, from Metron: its age rating, the run's genres and what
// it is about. "Not rated" once looked up and there is none -- for a parent,
// that is worth saying too.
function RequestFacts({ detail, story = false }) {
  const [open, setOpen] = useState(false);
  if (!detail || !("genres" in detail)) return null;
  const genres = detail.genres || [];
  return <>
    <span className="tag-line request-facts">
      <StatusBadge tone={ratingTone(detail.rating)}>{detail.rating ? `Rated ${detail.rating}` : "Not rated"}</StatusBadge>
      {genres.map((genre) => <StatusBadge tone="muted" key={genre}>{genre}</StatusBadge>)}
    </span>
    {detail.rating && detail.ratingFrom ? <small className="request-facts-note">Rating from {detail.ratingFrom.toLowerCase()}</small> : null}
    {story && detail.synopsis ? <p className={`request-story${open ? " open" : ""}`}>{detail.synopsis}</p> : null}
    {story && detail.synopsis?.length > 180 ? <button type="button" className="request-story-more" onClick={() => setOpen((value) => !value)}>{open ? "Less" : "More"}</button> : null}
  </>;
}

// One request: what was asked for, by whom, where it stands, and -- for the
// admin while it waits, or its reader while it is pending -- what to do.
function MemberRequestCard({ request, admin, onDecide, aimed = false }) {
  const [busy, setBusy] = useState("");
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const waiting = request.status === "pending" || request.status === "failed";
  const labels = admin ? ADMIN_STATE_LABELS : REQUEST_STATE_LABELS;
  async function decide(action, body) {
    setBusy(action);
    setError("");
    const failure = await onDecide(request, action, body);
    if (failure) setError(failure);
    setBusy("");
    if (!failure) setDeclining(false);
  }
  const byline = [request.detail?.publisher, request.detail?.year].filter(Boolean).join(" · ");
  return <article className={`request-card member-request-card${aimed ? " request-aimed" : ""}`} data-member-request={request.id}>
    <div className="member-request">
      <span className="request-cover">{request.detail?.cover
        ? <img src={request.detail.cover} alt="" loading="lazy" />
        : <span className="cover-placeholder" aria-hidden="true"><BookOpen size={24} weight="duotone" /></span>}</span>
      <span className="request-identity">
        <strong>{request.title}</strong>
        <small>{[requestScope(request), byline].filter(Boolean).join(" · ")}</small>
        {admin ? <span className="member-request-who"><ProfileAvatar profile={request.requestedBy} size="sm" />
          <small>{request.requestedBy?.name} · {requestedOn(request.createdAt)}</small></span>
          : <small>Asked {requestedOn(request.createdAt)}</small>}
        <span className="tag-line"><StatusBadge tone={REQUEST_TONES[request.state] || "muted"}>{labels[request.state] || request.state}</StatusBadge></span>
        {admin ? <RequestFacts detail={request.detail} /> : null}
        {request.state === "declined" && request.declineReason ? <small className="member-request-reason">&ldquo;{request.declineReason}&rdquo;</small> : null}
        {admin && request.status === "failed" && request.failure ? <small className="member-request-reason">{request.failure}</small> : null}
      </span>
    </div>
    {admin && waiting ? <div className="member-request-actions">
      {declining ? <form className="member-request-decline" onSubmit={(event) => { event.preventDefault(); decide("decline", { reason }); }}>
        <label className="form-field"><span>Reason</span>
          <input value={reason} maxLength={300} autoFocus onChange={(event) => setReason(event.target.value)} placeholder="Optional, shown to them" />
        </label>
        <div className="settings-card-actions">
          <button type="button" className="secondary-button" onClick={() => setDeclining(false)} disabled={Boolean(busy)}>Back</button>
          <button className="danger-button" disabled={Boolean(busy)} aria-busy={busy === "decline"}>{busy === "decline" ? <LoadingSpinner size={16} /> : null} Decline</button>
        </div>
      </form> : <div className="settings-card-actions">
        <button type="button" className="secondary-button" onClick={() => setDeclining(true)} disabled={Boolean(busy)}>Decline</button>
        <button type="button" className="primary-button" onClick={() => decide("approve")} disabled={Boolean(busy)} aria-busy={busy === "approve"}>
          {busy === "approve" ? <LoadingSpinner size={16} /> : <Check size={16} weight="bold" />} {request.status === "failed" ? "Try again" : "Approve"}
        </button>
      </div>}
    </div> : null}
    {!admin && request.status === "pending" ? <div className="member-request-actions"><div className="settings-card-actions">
      <button type="button" className="secondary-button" onClick={() => decide("cancel")} disabled={Boolean(busy)} aria-busy={busy === "cancel"}>
        {busy === "cancel" ? <LoadingSpinner size={16} /> : null} Cancel request
      </button>
    </div></div> : null}
    {error ? <p className="workbench-error member-request-error" role="alert">{error}</p> : null}
  </article>;
}

// One waiting request as a card to swipe: right approves, left declines, the
// buttons under it do the same. The card follows the finger; let go short of
// a decision and it springs back. A vertical drag is left to the page's scroll.
function SwipeCard({ request, onSwipe, peek = false }) {
  const cardRef = useRef(null);
  const drag = useRef(null);
  const [dx, setDx] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [leaving, setLeaving] = useState("");
  const still = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  function fly(action) {
    if (leaving) return;
    if (still) { onSwipe(request, action); return; }
    setLeaving(action);
    window.setTimeout(() => onSwipe(request, action), 220);
  }
  function down(event) {
    if (leaving || (event.pointerType === "mouse" && event.button !== 0) || event.target.closest("button, a, input")) return;
    drag.current = { x: event.clientX, y: event.clientY, id: event.pointerId, horizontal: null, at: performance.now() };
  }
  function move(event) {
    const current = drag.current;
    if (!current || current.id !== event.pointerId) return;
    const mx = event.clientX - current.x;
    const my = event.clientY - current.y;
    if (current.horizontal === null) {
      if (Math.abs(mx) < 8 && Math.abs(my) < 8) return;
      current.horizontal = Math.abs(mx) > Math.abs(my);
      if (!current.horizontal) { drag.current = null; return; }
      cardRef.current?.setPointerCapture?.(event.pointerId);
      setDragging(true);
    }
    setDx(mx);
  }
  function up(event) {
    const current = drag.current;
    drag.current = null;
    setDragging(false);
    if (!current?.horizontal) return;
    const moved = event.clientX - current.x;
    const action = swipeDecision(moved, cardRef.current?.offsetWidth || 320, performance.now() - current.at);
    if (action) fly(action); else setDx(0);
  }
  const offset = leaving ? (leaving === "approve" ? "130%" : "-130%") : `${dx}px`;
  const tilt = leaving ? (leaving === "approve" ? 12 : -12) : dx / 24;
  const lean = Math.max(-1, Math.min(1, dx / 120));
  const byline = [request.detail?.publisher, request.detail?.year].filter(Boolean).join(" · ");
  return <div className="request-swipe">
    <div className="request-swipe-pile">
    {peek ? <div className="request-card request-swipe-peek" aria-hidden="true" /> : null}
    <article ref={cardRef} className={`request-card request-swipe-card${dragging ? " dragging" : ""}${leaving ? " leaving" : ""}`}
      style={{ transform: `translateX(${offset}) rotate(${tilt}deg)` }} tabIndex={0}
      aria-label={`${request.title}, asked for by ${request.requestedBy?.name}. Swipe right to approve, left to decline.`}
      onKeyDown={(event) => { if (event.key === "ArrowRight") fly("approve"); if (event.key === "ArrowLeft") fly("decline"); }}
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
      <span className="request-swipe-stamp approve" style={{ opacity: Math.max(0, lean) }} aria-hidden="true">Approve</span>
      <span className="request-swipe-stamp decline" style={{ opacity: Math.max(0, -lean) }} aria-hidden="true">Decline</span>
      <div className="request-swipe-head">
        <span className="request-cover request-swipe-cover">{request.detail?.cover
          ? <img src={request.detail.cover} alt="" draggable="false" />
          : <span className="cover-placeholder" aria-hidden="true"><BookOpen size={28} weight="duotone" /></span>}</span>
        <span className="request-identity">
          <strong>{request.title}</strong>
          <small>{[requestScope(request), byline].filter(Boolean).join(" · ")}</small>
          <span className="member-request-who"><ProfileAvatar profile={request.requestedBy} size="sm" />
            <small>{request.requestedBy?.name} · {requestedOn(request.createdAt)}</small></span>
          {request.status === "failed" ? <small className="member-request-reason">Couldn&rsquo;t be done: {request.failure}</small> : null}
        </span>
      </div>
      <RequestFacts detail={request.detail} story />
    </article>
    </div>
    <div className="request-swipe-actions">
      <button type="button" className="secondary-button" onClick={() => fly("decline")} disabled={Boolean(leaving)}><X size={16} weight="bold" /> Decline</button>
      <button type="button" className="primary-button" onClick={() => fly("approve")} disabled={Boolean(leaving)}>
        <Check size={16} weight="bold" /> {request.status === "failed" ? "Try again" : "Approve"}</button>
    </div>
  </div>;
}

// Whether the device points precisely and hovers -- a mouse or trackpad. It
// decides the request queue's shape by how it is used, not by how wide the
// screen is: an iPad without a trackpad still swipes.
const FINE_POINTER = "(hover: hover) and (pointer: fine)";

function useFinePointer() {
  const [fine, setFine] = useState(() => typeof window !== "undefined" && Boolean(window.matchMedia?.(FINE_POINTER).matches));
  useEffect(() => {
    const query = window.matchMedia?.(FINE_POINTER);
    if (!query) return undefined;
    const change = () => setFine(query.matches);
    query.addEventListener?.("change", change);
    return () => query.removeEventListener?.("change", change);
  }, []);
  return fine;
}

// One waiting request as a row, for a mouse and keyboard: everything about it
// on show, Decline and Approve at its end -- Seerr's requests list.
function QueueRow({ request, onDecide }) {
  const byline = [request.detail?.publisher, request.detail?.year].filter(Boolean).join(" · ");
  return <article className="request-card request-queue-row" tabIndex={0} data-queue-request={request.id}
    aria-label={`${request.title}, asked for by ${request.requestedBy?.name}`}>
    <span className="request-cover request-swipe-cover">{request.detail?.cover
      ? <img src={request.detail.cover} alt="" />
      : <span className="cover-placeholder" aria-hidden="true"><BookOpen size={28} weight="duotone" /></span>}</span>
    <span className="request-identity">
      <strong>{request.title}</strong>
      <small>{[requestScope(request), byline].filter(Boolean).join(" · ")}</small>
      <span className="member-request-who"><ProfileAvatar profile={request.requestedBy} size="sm" />
        <small>{request.requestedBy?.name} · {requestedOn(request.createdAt)}</small></span>
      {request.status === "failed" ? <small className="member-request-reason">Couldn&rsquo;t be done: {request.failure}</small> : null}
      <RequestFacts detail={request.detail} story />
    </span>
    <span className="request-queue-actions">
      <button type="button" className="secondary-button" onClick={() => onDecide(request, "decline")} title="Decline (D)"><X size={16} weight="bold" /> Decline</button>
      <button type="button" className="primary-button" onClick={() => onDecide(request, "approve")} title="Approve (A)">
        <Check size={16} weight="bold" /> {request.status === "failed" ? "Try again" : "Approve"}</button>
    </span>
  </article>;
}

// The admin's waiting requests as a deck: one card at a time, the next behind
// it. A decision is held for a few seconds with Undo before it is sent --
// approving starts downloads, and a swipe is easy to make by accident. Leaving
// the page sends it; a request never sent simply stays waiting.
function RequestDeck({ requests, onDecide }) {
  const fine = useFinePointer();
  const listRef = useRef(null);
  const focusNext = useRef(null);
  const [held, setHeld] = useState(null);
  const [gone, setGone] = useState(() => new Set());
  const [error, setError] = useState("");
  const heldRef = useRef(null);
  const timer = useRef(null);
  const hide = (id, hidden) => setGone((current) => {
    const next = new Set(current);
    if (hidden) next.add(id); else next.delete(id);
    return next;
  });
  async function send(entry) {
    window.clearTimeout(timer.current);
    if (heldRef.current === entry) { heldRef.current = null; setHeld(null); }
    const failure = await onDecide(entry.request, entry.action, entry.action === "decline" ? { reason: entry.reason || "" } : undefined);
    if (failure) setError(failure);
    // Sent, and the queue reloaded: a decided request has left it; one whose
    // approval failed is back, offering Try again.
    hide(entry.request.id, false);
  }
  function decide(request, action) {
    // Focus moves on to the next request once the list has re-rendered
    // without this one, so a keyboard can keep going.
    const rows = [...(listRef.current?.querySelectorAll("[data-queue-request]") || [])];
    const at = rows.findIndex((row) => row.dataset.queueRequest === String(request.id));
    focusNext.current = (rows[at + 1] || rows[at - 1])?.dataset.queueRequest || null;
    if (heldRef.current) send(heldRef.current);
    const entry = { request, action, reason: "" };
    heldRef.current = entry;
    setHeld(entry);
    setError("");
    hide(request.id, true);
    timer.current = window.setTimeout(() => send(entry), UNDO_MS);
  }
  function undo() {
    const entry = heldRef.current;
    if (!entry) return;
    window.clearTimeout(timer.current);
    heldRef.current = null;
    setHeld(null);
    hide(entry.request.id, false);
  }
  function askReason() {
    window.clearTimeout(timer.current);
    setHeld({ ...heldRef.current, reasoning: true });
  }
  useEffect(() => {
    if (!focusNext.current) return;
    listRef.current?.querySelector(`[data-queue-request="${focusNext.current}"]`)?.focus();
    focusNext.current = null;
  }, [gone]);
  // While a decision waits, Z takes it back from anywhere on the page -- the
  // row that had focus is gone.
  useEffect(() => {
    if (!held || held.reasoning) return undefined;
    function onKey(event) {
      if (event.key.toLowerCase() !== "z" || event.metaKey || event.ctrlKey || event.altKey || event.target.closest?.("input, textarea")) return;
      event.preventDefault();
      undo();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [held]);
  // Leaving the page sends what was decided rather than dropping it.
  useEffect(() => () => {
    window.clearTimeout(timer.current);
    if (heldRef.current) onDecide(heldRef.current.request, heldRef.current.action,
      heldRef.current.action === "decline" ? { reason: heldRef.current.reason || "" } : undefined);
  }, []);
  const deck = requests.filter((request) => !gone.has(request.id));
  const [top, next] = deck;
  // A mouse and keyboard: move with the arrows or J/K, decide with A or D,
  // take it back with Z -- a moderation queue's keys, Gmail's Undo.
  function onKeys(event) {
    if (event.metaKey || event.ctrlKey || event.altKey || event.target.closest("input, textarea")) return;
    const key = event.key.toLowerCase();
    if (key === "z") return;
    const row = event.target.closest?.("[data-queue-request]");
    const rows = [...(listRef.current?.querySelectorAll("[data-queue-request]") || [])];
    if (["arrowdown", "j", "arrowup", "k"].includes(key)) {
      event.preventDefault();
      const at = rows.indexOf(row);
      const step = key === "arrowdown" || key === "j" ? 1 : -1;
      rows[Math.max(0, Math.min(rows.length - 1, at + step))]?.focus();
      return;
    }
    const request = row && deck.find((item) => String(item.id) === row.dataset.queueRequest);
    if (!request) return;
    if (key === "a") { event.preventDefault(); decide(request, "approve"); }
    if (key === "d") { event.preventDefault(); decide(request, "decline"); }
  }
  return <section className="request-deck" aria-label="Requests waiting">
    {held ? <div className="request-undo" role="status">
      {held.reasoning ? <form className="request-undo-reason" onSubmit={(event) => {
        event.preventDefault();
        const reason = new FormData(event.currentTarget).get("reason");
        send({ ...heldRef.current, reason: String(reason || "") });
      }}>
        <input name="reason" autoFocus maxLength={300} placeholder="Why not? Shown to them" aria-label="Reason" />
        <button className="primary-button">Decline</button>
      </form> : <>
        <span>{held.action === "approve" ? "Approved" : "Declined"} <strong>{held.request.title}</strong></span>
        {held.action === "decline" ? <button type="button" onClick={askReason}>Add a reason</button> : null}
        <button type="button" onClick={undo}>Undo</button>
      </>}
    </div> : null}
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
    {top && fine ? <>
      <p className="request-deck-count">{deck.length === 1 ? "1 request waiting" : `${deck.length} requests waiting`}
        <span className="request-keys" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd> move · <kbd>A</kbd> approve · <kbd>D</kbd> decline · <kbd>Z</kbd> undo</span></p>
      <div className="request-queue" ref={listRef} onKeyDown={onKeys}>
        {deck.map((request) => <QueueRow request={request} onDecide={decide} key={request.id} />)}
      </div>
    </> : top ? <>
      <p className="request-deck-count">{deck.length === 1 ? "1 request waiting" : `${deck.length} requests waiting`}</p>
      <SwipeCard request={top} onSwipe={decide} peek={Boolean(next)} key={top.id} />
    </> : held ? null : <div className="empty-state request-empty"><CheckCircle size={34} weight="duotone" /><strong>All caught up</strong><span>Nothing is waiting for you.</span></div>}
  </section>;
}

// The admin's queue, as the Pull List's first tab: what waits, then what was decided.
function MemberRequestList({ requests, admin, onDecide, focus }) {
  // The admin answers the oldest first; a reader looks for what they just asked.
  const queue = adminQueue(requests);
  const waiting = admin ? queue.waiting : [...queue.waiting].reverse();
  // A request its reader took back is theirs; the admin's history leaves it out.
  const decided = admin ? queue.decided.filter((request) => request.status !== "cancelled") : queue.decided;
  const aimed = focus?.requestId;
  if (!requests.length) {
    return <div className="empty-state request-empty"><CheckCircle size={34} weight="duotone" />
      <strong>{admin ? "No requests" : "Nothing requested yet"}</strong>
      <span>{admin ? "When a reader asks for a run or an issue, it waits here for you."
        : "Find a run or an issue in Discover and tap Request. The admin says yes or no."}</span></div>;
  }
  return <>
    {admin ? <RequestDeck requests={waiting} onDecide={onDecide} />
      : waiting.length ? <section className="request-list">{waiting.map((request) => <MemberRequestCard request={request} admin={admin}
      onDecide={onDecide} aimed={String(aimed) === String(request.id)} key={request.id} />)}</section> : null}
    {decided.length ? <section className="request-list member-request-history" aria-label="Decided">
      {admin || waiting.length ? <h2 className="member-request-heading">Earlier</h2> : null}
      {decided.map((request) => <MemberRequestCard request={request} admin={admin} onDecide={onDecide}
        aimed={String(aimed) === String(request.id)} key={request.id} />)}
    </section> : null}
  </>;
}

// A reader's page: what they asked for and what became of it.
function MyRequestsView({ catalog, backendStatus, focus, onDecide }) {
  const loading = catalogPending(catalog, backendStatus);
  return <><PageHeader title="Requests" />
    {loading ? <CatalogLoading title="Loading your requests…" detail="" />
      : <MemberRequestList requests={catalog?.memberRequests || []} admin={false} onDecide={onDecide} focus={focus} />}
  </>;
}

function RequestsView({ catalog, backendStatus, focus, onCancelReplacement, onDeletePull, onRefresh, onDecide }) {
  const [releaseJob, setReleaseJob] = useState(null);
  // Readers' requests come first while any wait: they are the admin's to answer.
  const memberRequests = catalog?.memberRequests || [];
  const pendingAsks = memberRequests.filter((request) => request.status === "pending" || request.status === "failed").length;
  const [tab, setTabState] = useState(() => (pendingAsks ? "asks" : "wanted"));
  // The catalog often arrives after the page: open on Requests then, unless
  // a tab has already been chosen.
  const tabChosen = useRef(false);
  const setTab = (next) => { tabChosen.current = true; setTabState(next); };
  useEffect(() => {
    if (pendingAsks && !tabChosen.current) setTabState("asks");
  }, [pendingAsks]);
  const [searchingMissing, setSearchingMissing] = useState(false);
  const [searchMissingMessage, setSearchMissingMessage] = useState("");
  // The Wanted list's own action, the way Radarr and Sonarr put one there:
  // everything still missing, searched on demand. It covers issues nothing
  // good enough was found for last time, and requests made before Flipparr
  // searched on its own.
  //
  // It runs on the tap, without a confirmation (2026-09-19, at the user's
  // request): the line it sits on says how many issues it is for, downloading
  // them is what a wanted list is for, and nothing here is destructive -- a
  // download that was not wanted is removed from this same page.
  async function searchMissing() {
    setSearchingMissing(true);
    setSearchMissingMessage("");
    try {
      const result = await apiRequest("/api/v1/requests/search-missing", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmed: true }),
      });
      setSearchMissingMessage(result.detail || "");
      await onRefresh?.();
    } catch (error) {
      setSearchMissingMessage(error.message || "That search could not be started");
    } finally {
      setSearchingMissing(false);
    }
  }
  const requests = catalog?.requests || [];
  const replacements = catalog?.replacementRequests || [];
  const [progress, setProgress] = useState({});
  // Declared after the lists it reads: `requests` and `replacements` are const,
  // so reaching them from above is a ReferenceError that blanks the page.
  const anyDownloading = [...requests, ...replacements]
    .flatMap((request) => request.jobs || [])
    .some((job) => ["queued", "downloading"].includes(job.downloadStatus));
  useEffect(() => {
    if (!anyDownloading) { setProgress({}); return undefined; }
    let cancelled = false;
    async function poll() {
      try {
        const result = await apiRequest("/api/v1/acquisition/progress");
        if (!cancelled) setProgress(result?.downloads || {});
      } catch { /* a download client that cannot answer shows no bar */ }
    }
    poll();
    const timer = window.setInterval(poll, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [anyDownloading]);
  // Four tabs for the four states, and every request in exactly one of them.
  // The classifier lives in src/pull-list.js because this screen cannot be
  // exercised on a library with no requests, which is how two bugs reached
  // production; there it is testable without a browser.
  const buckets = useMemo(() => groupPullList(catalog), [catalog]);
  // A notification about a failed download lands here, so the screen has to
  // put the reader in front of that request rather than on whichever tab they
  // last used. A failure is always on a live request, which is Wanted.
  const aimedAt = useRef(null);
  useEffect(() => {
    if (!focus || aimedAt.current === focus) return;
    aimedAt.current = focus;
    if (focus.tab) { setTab(focus.tab); return undefined; }
    // Ask which tab actually holds the row rather than assuming: a failure or
    // an arrival is exactly the case that is not on Wanted.
    const all = [...(catalog?.requests || []), ...(catalog?.replacementRequests || [])];
    const target = all.find((request) => String(request.id) === String(focus.requestId));
    setTab(classifyRequest(target) || "wanted");
    // After the tab renders, not before: the row does not exist until then.
    const key = `${focus.kind === "replacement" ? "replacement" : "series"}-${focus.requestId}`;
    const timer = setTimeout(() => {
      const row = document.querySelector(`[data-request="${key}"]`);
      if (!row) return;
      row.scrollIntoView({ block: "center", behavior: "smooth" });
      const toggle = row.querySelector(".request-row");
      if (toggle?.getAttribute("aria-expanded") === "false") toggle.click();
      row.classList.add("request-aimed");
      setTimeout(() => row.classList.remove("request-aimed"), 2200);
    }, 60);
    return () => clearTimeout(timer);
  }, [focus]);
  const entries = tab === "asks" ? [] : buckets[tab] ?? [];
  const tabCopy = PULL_LIST_COPY[tab] ?? PULL_LIST_COPY.wanted;
  const loading = catalogPending(catalog, backendStatus);
  const tabs = [
    ...(memberRequests.length ? [{ id: "asks", label: "Requests", count: loading ? null : <b>{pendingAsks}</b> }] : []),
    ...PULL_LIST_TABS.filter(({ id }) => id !== "failed" || tabCount(buckets.failed)).map(({ id, label }) => ({ id, label, className: id === "failed" ? "request-tab-failed" : "", count: loading ? null : <b>{tabCount(buckets[id])}</b> })),
  ];
  // Searching for everything missing is Wanted's own action, so it sits at
  // the head of that list, beside how much there is to find, rather than in
  // the page header.
  const missingIssues = buckets.wanted.reduce((sum, { kind, request }) => sum + (kind === "replacement"
    ? (request.jobs || []).filter((job) => !["fulfilled", "cancelled"].includes(job.status)).length
    : request.wantedIssueCount || 0), 0);
  // The count and what to do about it, as one line: "3 missing issues | Find".
  const wantedBar = tab === "wanted" && !loading && entries.length ? <div className="request-list-bar">
    <span><strong>{missingIssues}</strong> missing issue{missingIssues === 1 ? "" : "s"}</span>
    <i className="request-list-bar-rule" aria-hidden="true" />
    <button type="button" className="search-missing-button" onClick={() => searchMissing()} disabled={searchingMissing} aria-busy={searchingMissing}
      aria-label={`Find the ${missingIssues} missing issue${missingIssues === 1 ? "" : "s"}`}>
      {/* No magnifier: that icon means typing a search, and this is not one. */}
      {searchingMissing ? <><LoadingSpinner size={14} /> Finding…</> : "Find"}
    </button>
  </div> : null;
  return <><PageHeader title="Pull List" tools={<SegmentedTabs label="Pull List" value={tab} onChange={setTab} items={tabs} />} />{wantedBar}{tab === "wanted" && searchMissingMessage ? <p className="request-search-result" role="status">{searchMissingMessage}</p> : null}{tab === "asks" && !loading ? <MemberRequestList requests={memberRequests} admin onDecide={onDecide} focus={focus} /> : <section className="request-list">{loading ? <CatalogLoading title="Loading your pull list…" detail="Bringing in followed runs, wanted issues, and downloads." /> : entries.length ? entries.map(({ kind, request }) => kind === "replacement"
    ? <ReplacementRequestRow request={request} progress={progress} openByDefault={tab === "failed" || tab === "downloading"} onCancel={onCancelReplacement} onFindRelease={setReleaseJob} onRefresh={onRefresh} key={`replacement-${request.id}`} />
    : <RequestRow request={request} tab={tab} progress={progress} openByDefault={tab === "failed" || tab === "downloading"} onFindRelease={setReleaseJob} onRefresh={onRefresh} onDelete={onDeletePull} key={`series-${request.id}`} />) : <div className="empty-state request-empty"><CheckCircle size={34} weight="duotone" /><strong>{tabCopy.emptyTitle}</strong><span>{tabCopy.emptyDetail}</span></div>}</section>}{releaseJob ? <ReleaseSearchModal job={releaseJob} onClose={() => setReleaseJob(null)} onGrabbed={async () => { await onRefresh?.(); setReleaseJob(null); }} /> : null}</>;
}

function acquisitionFailureDetails(job) {
  const technical = String(job?.downloadError || "").trim();
  const normalized = technical.toLowerCase();
  if (job?.downloadFailureStage === "import") return {
    label: "Import failed",
    message: "The download finished, but Flipparr could not validate or add the comic to your library. The original file is still active.",
    technical,
  };
  // The file said it was something else, so the release is not used again.
  if (job?.downloadFailureStage === "content") return {
    label: "Wrong comic",
    message: "The release held a different comic than this issue, so it will not be used again. Its files are kept for a week; the technical details say what was found.",
    technical,
  };
  // Parts of the posted release are missing, so downloading it again gives
  // the same holes; it is not grabbed again.
  if (job?.downloadFailureStage === "damaged") return {
    label: "Incomplete release",
    message: job?.downloadSource === "qbittorrent"
      ? "This torrent's comic arrived with parts missing, so it cannot be used and will not be grabbed again. Another release is needed; the technical details say how much was missing."
      : "Parts of this release are missing from the Usenet server, so it cannot be used and will not be grabbed again. Another release, or another indexer, is needed; the technical details say how much was missing.",
    technical,
  };
  // Nothing proved it wrong -- it could not be read or identified -- so the
  // release is only set aside for a day.
  if (job?.downloadFailureStage === "unidentified") return {
    label: "Couldn't confirm the issue",
    message: "Flipparr could not confirm this download is the issue, so it was set aside rather than filed. Its files are kept for a week and the release is tried again tomorrow; the technical details say what was read.",
    technical,
  };
  if (normalized.includes("not-complete") || normalized.includes("aborted, cannot be completed")) return {
    label: "Incomplete release",
    message: "SABnzbd could not retrieve enough Usenet articles to finish this release. Choose another release to continue.",
    technical,
  };
  const client = job?.downloadSource === "qbittorrent" ? "qBittorrent" : "SABnzbd";
  return {
    label: job?.downloadFailureStage === "download" ? "Download failed" : "Request failed",
    message: job?.downloadFailureStage === "download"
      ? `${client} could not complete this release. Choose another release or review the technical details.`
      : "Flipparr could not complete this request. Review the technical details before trying again.",
    technical,
  };
}

function requestFailureStatus(jobs) {
  const failedJobs = jobs.filter(jobHasFailed);
  if (!failedJobs.length) return null;
  if (failedJobs.length === 1) return acquisitionFailureDetails(failedJobs[0]).label;
  const allIncomplete = failedJobs.every((job) => acquisitionFailureDetails(job).label === "Incomplete release");
  return allIncomplete ? `${failedJobs.length} incomplete releases` : `${failedJobs.length} downloads failed`;
}

function JobProgress({ entry }) {
  // Only while SABnzbd is actually holding this one. A row with nothing moving
  // shows nothing rather than an empty bar sitting at zero.
  if (!entry) return null;
  const percent = Math.max(0, Math.min(100, Number(entry.percent) || 0));
  const detail = [
    entry.timeLeft && entry.timeLeft !== "0:00:00" ? `${entry.timeLeft} left` : "",
    entry.sizeLeft ? `${entry.sizeLeft} to go` : "",
  ].filter(Boolean).join(" · ");
  return <div className="job-progress">
    <div className="job-progress-track" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} aria-label="Download progress"><i style={{ width: `${percent}%` }} /></div>
    <small>{percent}%{detail ? ` · ${detail}` : ""}</small>
  </div>;
}

function ReplacementRequestRow({ request, progress = {}, openByDefault = false, onCancel, onFindRelease, onRefresh }) {
  const [expanded, setExpanded] = useState(openByDefault);
  const [retryingJobId, setRetryingJobId] = useState(null);
  const [retryError, setRetryError] = useState(null);
  const jobs = request.jobs || [];
  const failed = jobs.filter(jobHasFailed).length;
  const importing = jobs.filter((job) => ["completed", "importing", "waiting_for_files"].includes(job.downloadStatus)).length;
  const downloading = jobs.filter((job) => ["queued", "downloading"].includes(job.downloadStatus)).length;
  const searching = jobs.filter((job) => job.status === "searching").length;
  const display = { id: `replacement-${request.id}`, title: request.seriesTitle || request.targetTitle, cover: request.cover };
  const statusLabels = request.targetType === "issue" ? { wanted: "Fix issue", searching: "Finding issue", grabbed: "Issue found", failed: "Needs attention", fulfilled: "Issue fixed" } : { wanted: "Fix run", searching: "Finding comics", grabbed: "Run fix found", failed: "Needs attention", fulfilled: "Run fixed" };
  const statusLabel = request.status === "fulfilled" ? statusLabels.fulfilled : failed ? requestFailureStatus(jobs) : importing ? "Adding to library" : downloading ? "Downloading" : searching ? "Searching" : statusLabels[request.status] || request.status;
  const tone = request.status === "fulfilled" ? "green" : request.status === "failed" || failed ? "red" : "violet";
  const preference = ACQUISITION_LABELS[request.acquisitionPreference] || ACQUISITION_LABELS.either;
  const title = request.seriesTitle || request.targetTitle;
  const scope = [preference, request.coverageTarget, request.reason, request.desiredLanguage ? `Wanted language: ${request.desiredLanguage}` : null].filter(Boolean).join(" · ");
  // Stopping is the answer to a download that is not going anywhere: the
  // issue goes back to wanting a release, and another is looked for at once;
  // `pack` stops every issue coming in the same torrent.
  async function stopJob(job, pack = false) {
    setRetryingJobId(job.id); setRetryError(null);
    try {
      await apiRequest(`/api/v1/acquisition-jobs/${job.id}/stop`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pack }) });
      await onRefresh?.();
    } catch (error) {
      setRetryError({ jobId: job.id, message: error.message || "This download could not be stopped" });
    } finally { setRetryingJobId(null); }
  }
  async function retryJob(job) {
    setRetryingJobId(job.id); setRetryError(null);
    try {
      const result = await apiRequest(`/api/v1/acquisition-jobs/${job.id}/retry`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await onRefresh?.();
      if (result.action === "research") onFindRelease(job);
    } catch (error) {
      setRetryError({ jobId: job.id, message: error.message || "This replacement could not be retried" });
    } finally { setRetryingJobId(null); }
  }
  return <article data-request={`replacement-${request.id}`} className={`request-card replacement-request-card ${expanded ? "expanded" : ""}`}><button type="button" className="request-row" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}><span className="request-cover"><SeriesCover series={display} decorative /></span><span className="request-identity"><strong>{title}</strong><small>{scope}</small><small>{request.targetTitle !== title ? `${request.targetTitle} · ` : ""}{request.filename} · Added {request.requestedDate} {request.requestedTime}</small><span className="tag-line"><StatusBadge tone={tone}>{statusLabel}</StatusBadge></span></span><CaretDown className="request-caret" size={20} aria-hidden="true" /></button>{expanded ? <div className="request-job-panel"><header><div><strong>{request.status === "fulfilled" ? "Replacement complete" : "Comics needed for this replacement"}</strong><span>{request.status === "fulfilled" ? "The verified replacement is active and the original is held in recoverable quarantine." : "Flipparr searches and grabs the best match for each issue. The original stays active until every replacement passes validation."}</span></div>{!["fulfilled", "cancelled"].includes(request.status) ? <button className="ghost-button" onClick={() => onCancel(request)}>Cancel request</button> : null}</header>{jobs.length ? <div className="request-jobs">{jobs.map((job) => { const displayStatus = (job.downloadStopped ? null : job.downloadStatus) || job.status; const imported = job.downloadStatus === "imported"; const failedJob = jobHasFailed(job); const canSearch = !job.downloadStatus && !["grabbed", "fulfilled", "cancelled"].includes(job.status); const retryMessage = retryError?.jobId === job.id ? retryError.message : null; const failure = failedJob ? acquisitionFailureDetails(job) : null; const detail = retryMessage || (!failedJob ? job.downloadTitle : null); return <div className="request-job" key={job.id}><b>{issueLabel(job.issueNumber, request.medium)}</b><div><strong>{job.issueTitle || `Issue ${job.issueNumber}`}</strong>{imported ? null : <span>{job.reason}</span>}{failure ? <div className="job-failure-copy"><strong>{failure.label}</strong><small>{failure.message}</small>{failure.technical ? <details><summary>Technical details</summary><code>{failure.technical}</code></details> : null}</div> : detail ? <span className={retryMessage ? "job-error" : ""}>{detail}</span> : null}<JobProgress entry={progress[String(job.id)]} /></div><span className="request-job-actions"><span className={`job-state ${displayStatus}`}>{downloadStateLabel(job, DOWNLOAD_STATUS_LABELS) || JOB_STATUS_LABELS[job.status] || displayStatus}</span>{failedJob ? <><button type="button" disabled={retryingJobId === job.id} onClick={() => retryJob(job)}>{retryingJobId === job.id ? <LoadingSpinner size={14} /> : <ArrowsClockwise size={14} />} {job.downloadFailureStage === "import" ? "Retry import" : "Try next release"}</button><button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button></> : !imported && canSearch ? <button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button> : null}{!imported && !failedJob && job.status === "grabbed" ? <StopDownloadButton job={job} busy={retryingJobId === job.id} onStop={(pack) => stopJob(job, pack)} /> : null}</span></div>; })}</div> : <div className="request-job-empty"><WarningCircle size={20} /><div><strong>No safe issue targets are available</strong><span>Confirm the comic’s issue contents before replacing it.</span></div></div>}</div> : <footer className="replacement-safety-note"><ShieldCheck size={16} weight="fill" /> The current comic stays in your library until all mapped replacements are downloaded and verified.</footer>}</article>;
}

// Stop on an issue that rides in a torrent pack: each of the pack's issues
// has its own row, and stopping one only leaves its file out -- so a slow
// pack of a long run took a Stop per issue (owner, 2026-10-01). It asks
// whether to stop the whole pack or this issue alone; either way what is
// stopped is looked for again at once.
function StopDownloadButton({ job, busy, onStop }) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef(null);
  const close = useCallback(() => setOpen(false), []);
  const count = Number(job.downloadPackCount) || 0;
  const icon = busy ? <LoadingSpinner size={14} /> : <X size={14} />;
  if (count < 2) return <button type="button" disabled={busy} onClick={() => onStop(false)}>{icon} Stop download</button>;
  return <>
    <button type="button" ref={buttonRef} disabled={busy} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{icon} Stop download</button>
    {open ? <ActionMenu anchor={buttonRef} label="Stop download" onClose={close} items={[
      { key: "pack", label: `Stop the whole pack (${count} issues)`, icon: <X size={16} />, onSelect: () => onStop(true) },
      { key: "one", label: "Stop just this issue", icon: <X size={16} />, onSelect: () => onStop(false) },
    ]} /> : null}
  </>;
}

function RequestRow({ request, tab, progress = {}, openByDefault = false, onFindRelease, onRefresh, onDelete }) {
  const [expanded, setExpanded] = useState(openByDefault);
  const [deleting, setDeleting] = useState(null);
  const [retryingJobId, setRetryingJobId] = useState(null);
  const [retryError, setRetryError] = useState(null);
  const [uploadingJobId, setUploadingJobId] = useState(null);
  const [uploadError, setUploadError] = useState(null);
  const jobs = request.jobs || [];
  // A comic found by hand had nowhere to go: it had to be dropped in the
  // library folder and waited for, with nothing tying it to the wanted issue.
  async function uploadForJob(job, file) {
    if (!file) return;
    setUploadingJobId(job.id);
    setUploadError(null);
    try {
      await apiRequest(`/api/v1/acquisition-jobs/${job.id}/import`, {
        method: "POST",
        // A header holds only ASCII; a comic's name often has an en dash or a
        // curly apostrophe, and fetch refuses the header outright otherwise.
        headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) },
        body: file,
      });
      onRefresh?.();
    } catch (error) {
      setUploadError({ jobId: job.id, message: error.message });
    }
    setUploadingJobId(null);
  }
  // A run is on every tab it has issues for. On one, the row speaks for the
  // issues listed there, so its badge does not say Failed on Wanted.
  const tabJobs = jobsForTab(request, tab);
  const counted = ["failed", "downloading", "wanted"].includes(tab) ? tabJobs.jobs : jobs;
  const ready = request.wantedIssueCount || 0;
  const upcoming = request.upcomingIssueCount || 0;
  const unknown = request.unknownReleaseIssueCount || 0;
  const queued = counted === jobs ? request.queuedJobCount || 0
    : counted.filter((job) => job.status === "queued").length;
  const searching = counted.filter((job) => job.status === "searching").length;
  const downloading = counted.filter((job) => ["queued", "downloading"].includes(job.downloadStatus)).length;
  const importing = counted.filter((job) => ["completed", "importing", "waiting_for_files"].includes(job.downloadStatus)).length;
  const failed = counted.filter(jobHasFailed).length;
  // A run reaches Acquired after it was unfollowed, so the row can no longer
  // assume it is being watched. Cancelled is the only status that means that;
  // a fulfilled request is still followed, which is what the Comics grid says.
  // A request for named issues never followed anything -- pulling one comic
  // from Discover is not taking on its run.
  const pulled = request.coverage === "issues";
  const following = !pulled && request.status !== "cancelled";
  const scope = [
    pulled ? `${request.targetIssueCount === 1 ? "One issue" : `${request.targetIssueCount} issues`} pulled from Discover · not following this run` : following ? `Following · ${request.publicationStatus === "ongoing" ? "checks daily for newly listed issues" : request.publicationStatus === "completed" ? "completed run" : "checks daily in case it continues"}` : request.publicationStatus === "completed" ? "Completed run" : "Nothing is being looked for",
    ACQUISITION_LABELS[request.acquisitionPreference] || ACQUISITION_LABELS.either,
    unknown ? `${unknown} release date${unknown === 1 ? "" : "s"} unknown` : null,
  ].filter(Boolean).join(" · ");
  // A comic pulled before it ships has no job yet, so it is listed from the
  // request's own issues -- otherwise there is nothing to see or delete.
  const waiting = waitingIssues(request);
  // With one issue, deleting it and deleting the pull are the same click.
  const perIssueDelete = pulled && request.targetIssueCount > 1;
  const status = pulled ? (request.status === "fulfilled" ? "Arrived" : upcoming && !jobs.length ? "Waiting for release" : "Pulled issue")
    : !following ? "Not following" : request.status === "fulfilled" ? "Up to date" : failed ? requestFailureStatus(jobs) : importing ? `${importing} adding to library` : downloading ? `${downloading} downloading` : searching ? "Searching" : queued ? `${queued} wanted` : upcoming ? "Waiting for release" : "Checking release dates";
  const tone = pulled ? (request.status === "fulfilled" ? "green" : "violet")
    : !following ? "muted" : request.status === "fulfilled" ? "green" : failed ? "red" : queued || searching || downloading || importing ? "violet" : upcoming ? "green" : "muted";
  const display = { id: `request-${request.id}`, title: request.title, cover: request.cover };
  // The bar is the Comics card's: owned out of the issues this pull covers.
  const ownership = {
    owned: request.ownedIssueCount || 0,
    total: Math.max(request.targetIssueCount || 0, 1),
    status: request.ownedIssueCount >= request.targetIssueCount ? "complete" : "partial",
    releaseSummary: { upcoming, releasedMissing: ready },
  };
  const scopeNote = pulled ? "Pulled from Discover" : [ready ? `${ready} missing` : null, upcoming ? `${upcoming} upcoming` : null].filter(Boolean).join(" · ");
  // An issue already in the library is not a missing issue. Listing its job
  // under "Missing issues" put "Added to library" in the middle of a list of
  // things still being looked for.
  const outstandingJobs = tabJobs.jobs;
  // ...but on a row that has nothing outstanding, those same jobs are the only
  // thing there is to show, and on Acquired they are the reason the row is
  // there at all. So the panel falls back to what arrived rather than to an
  // empty state claiming Flipparr is still checking release dates.
  const panelJobs = outstandingJobs.length ? outstandingJobs : jobs.filter(arrivalAt);
  const jobGroups = panelJobs.reduce((groups, job) => {
    const key = job.seriesId || "series";
    if (!groups[key]) groups[key] = { id: key, title: job.seriesTitle || request.title, jobs: [] };
    groups[key].jobs.push(job);
    return groups;
  }, {});
  // Stopping is the answer to a download that is not going anywhere: the
  // issue goes back to wanting a release, and another is looked for at once;
  // `pack` stops every issue coming in the same torrent.
  async function stopJob(job, pack = false) {
    setRetryingJobId(job.id); setRetryError(null);
    try {
      await apiRequest(`/api/v1/acquisition-jobs/${job.id}/stop`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pack }) });
      await onRefresh?.();
    } catch (error) {
      setRetryError({ jobId: job.id, message: error.message || "This download could not be stopped" });
    } finally { setRetryingJobId(null); }
  }
  async function retryJob(job) {
    setRetryingJobId(job.id);
    setRetryError(null);
    try {
      const result = await apiRequest(`/api/v1/acquisition-jobs/${job.id}/retry`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      await onRefresh?.();
      if (result.action === "research") onFindRelease(job);
    } catch (error) {
      setRetryError({ jobId: job.id, message: error.message || "This issue could not be retried" });
    } finally {
      setRetryingJobId(null);
    }
  }
  async function remove(issue) {
    setDeleting(issue ? issue.id : "all");
    try { await onDelete?.(request, issue); } finally { setDeleting(null); }
  }
  const deleteButton = (issue) => <button type="button" className="request-job-delete" disabled={deleting !== null} onClick={() => remove(issue)} aria-label={`Delete #${issue.number} from the Pull List`}>{deleting === issue.id ? <LoadingSpinner size={14} /> : <Trash size={14} />} {deleting === issue.id ? "Deleting…" : "Delete"}</button>;
  return <article data-request={`series-${request.id}`} className={`request-card ${expanded ? "expanded" : ""}`}>
    {/* The Comics list's card: cover, title and year, publisher, the run's
        badges and its ownership bar. What the row is doing and why sits in
        the panel it opens. */}
    <button type="button" className="request-row" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
      <span className="request-cover"><SeriesCover series={display} decorative /></span>
      <span className="request-identity">
        <strong>{request.title}{request.year ? <> <em>({request.year})</em></> : null}</strong>
        <small>{[request.publisher, scopeNote].filter(Boolean).join(" · ")}</small>
        <span className="tag-line"><PublicationStatus series={request} />{following ? <span className="monitoring-status"><FollowedIcon size={null} /> Following</span> : null}<StatusBadge tone={tone}>{status}</StatusBadge></span>
        <span className="request-ownership"><Ownership series={ownership} compact /></span>
      </span>
      <CaretDown className="request-caret" size={20} aria-hidden="true" />
    </button>
    {expanded ? <div className="request-job-panel">
      <header><div><strong>{pulled ? "Pulled from Discover" : !following ? "What arrived" : request.status === "fulfilled" ? "Run is up to date" : "Missing issues"}</strong><span>{pulled ? "You asked for these issues by name. The rest of the run is not being looked for." : !following ? "You stopped following this run. Everything already in your library stays there; nothing new is looked for." : request.status === "fulfilled" ? (request.publicationStatus === "completed" ? "Every issue in this completed run is in your library." : "Flipparr will keep checking this run and add newly released issues to Wanted.") : "Flipparr searches for each missing issue and grabs the best match. Anything it cannot decide waits here for you."}</span><small>{scope}</small></div>{pulled && canDeletePull(request) ? <button type="button" className="ghost-button" disabled={deleting !== null} onClick={() => remove(null)}>{deleting === "all" ? "Deleting…" : "Delete request"}</button> : null}</header>
      {Object.keys(tabJobs.elsewhere).length ? <p className="request-jobs-note">
        Also in this run: {Object.entries(tabJobs.elsewhere).map(([where, count]) => `${count} ${where}`).join(" · ")}.
      </p> : null}
      {panelJobs.length || waiting.length ? <div className="request-jobs">{Object.values(jobGroups).map((group) => <section className="request-job-group" key={group.id}>
        <header><span>Series run</span><strong>{group.title}</strong><b>{group.jobs.length} issue{group.jobs.length === 1 ? "" : "s"}</b></header>
        {group.jobs.map((job) => { const displayStatus = (job.downloadStopped ? null : job.downloadStatus) || job.status; const imported = job.downloadStatus === "imported"; const failedJob = jobHasFailed(job); const relativeDestination = job.downloadDestination?.split("/comics/").pop(); const retryMessage = retryError?.jobId === job.id ? retryError.message : null; const failure = failedJob ? acquisitionFailureDetails(job) : null; const displayDetail = retryMessage || (!failedJob ? (relativeDestination ? `Library: ${relativeDestination}` : job.downloadTitle) : null); const canSearch = !job.downloadStatus && !["grabbed", "fulfilled", "cancelled"].includes(job.status); const retryLabel = job.downloadFailureStage === "import" ? "Retry import" : "Try next release"; return <div className="request-job" key={job.id}>
          <b>{issueLabel(job.issueNumber, request.medium)}</b>
          <div><strong>{job.issueTitle || `Issue ${job.issueNumber}`}</strong>{imported ? null : <span>{job.reason}</span>}{job.deliveredWithCount > 0 ? <span className="job-pack-note">{job.deliveredWithCount} more issue{job.deliveredWithCount === 1 ? "" : "s"} came from this download</span> : null}{uploadError?.jobId === job.id ? <span className="job-error" role="alert">{uploadError.message}</span> : null}{failure ? <div className="job-failure-copy"><strong>{failure.label}</strong><small>{failure.message}</small>{failure.technical ? <details><summary>Technical details</summary><code>{failure.technical}</code></details> : null}</div> : displayDetail ? <span className={retryMessage ? "job-error" : ""}>{displayDetail}</span> : null}<JobProgress entry={progress[String(job.id)]} /></div>
          <span className="request-job-actions"><span className={`job-state ${displayStatus}`}>{downloadStateLabel(job, DOWNLOAD_STATUS_LABELS) || JOB_STATUS_LABELS[job.status] || displayStatus}</span>{failedJob ? <><button type="button" disabled={retryingJobId === job.id} onClick={() => retryJob(job)}>{retryingJobId === job.id ? <LoadingSpinner size={14} /> : <ArrowsClockwise size={14} />} {retryingJobId === job.id ? "Retrying…" : retryLabel}</button><button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button></> : !imported && canSearch ? <button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button> : null}{!imported && !failedJob && job.status === "grabbed" ? <StopDownloadButton job={job} busy={retryingJobId === job.id} onStop={(pack) => stopJob(job, pack)} /> : null}{!imported ? <label className={`job-upload ${uploadingJobId === job.id ? "busy" : ""}`}><input type="file" accept=".cbz,.cbr,.cbt,.cb7,.pdf,.epub" disabled={uploadingJobId === job.id} onChange={(event) => { const [file] = event.target.files || []; event.target.value = ""; uploadForJob(job, file); }} />{uploadingJobId === job.id ? <LoadingSpinner size={14} /> : <UploadSimple size={14} />} {uploadingJobId === job.id ? "Adding…" : "Upload a file"}</label> : null}{perIssueDelete && canDeleteJob(job) ? deleteButton({ id: job.issueId, number: job.issueNumber }) : null}</span>
        </div>; })}
      </section>)}{waiting.length ? <section className="request-job-group">
        <header><span>Not out yet</span><strong>{request.title}</strong><b>{waiting.length} issue{waiting.length === 1 ? "" : "s"}</b></header>
        {waiting.map((issue) => <div className="request-job" key={`waiting-${issue.id}`}>
          <b>{issueLabel(issue.number, request.medium)}</b>
          <div><strong>{issue.title || `Issue ${issue.number}`}</strong><span>{issue.publicationDate ? `Out ${formatLongDate(issue.publicationDate)} · searched for once it ships` : "Release date not known yet · searched for once it ships"}</span></div>
          <span className="request-job-actions"><span className="job-state waiting">Waiting for release</span>{perIssueDelete ? deleteButton(issue) : null}</span>
        </div>)}
      </section> : null}</div> : <div className="request-job-empty"><ArrowsClockwise size={20} /><div><strong>{following ? "Nothing is ready to search yet" : "Nothing is being looked for"}</strong><span>{following ? "No action is required. Flipparr will keep checking release dates automatically." : "Follow the run again from Comics to have Flipparr check it."}</span></div></div>}
      {!pulled && (upcoming || unknown) ? <footer>{upcoming ? `${upcoming} upcoming issue${upcoming === 1 ? " is" : "s are"} being followed` : null}{upcoming && unknown ? " · " : null}{unknown ? `${unknown} issue${unknown === 1 ? " has" : "s have"} an unknown release date` : null}</footer> : null}
    </div> : null}
  </article>;
}

function formatReleaseSize(bytes) {
  const value = Number(bytes || 0);
  if (!value) return "Size unavailable";
  if (value >= 1024 ** 3) return `${(value / (1024 ** 3)).toFixed(2)} GB`;
  return `${Math.max(1, Math.round(value / (1024 ** 2)))} MB`;
}

/**
 * One release the matcher set aside, with the way through for the person who
 * can see it is the right file: "Take anyway" asks once, in the row, saying
 * what taking it means (owner, 2026-09-29). The server refuses the same id
 * without "anyway", so nothing here can grab by accident.
 */
function SetAsideRow({ item, job, admin, busy, onTake }) {
  const [confirming, setConfirming] = useState(false);
  const confirmRef = useRef(null);
  useEffect(() => { if (confirming) confirmRef.current?.focus(); }, [confirming]);
  const takeable = admin && item.id;
  return <li className={takeable ? "release-set-aside-row" : undefined}>
    <b>{item.title}</b>
    <small>{item.reason}{item.copies > 1 ? ` · listed by ${item.copies} indexers` : ""}</small>
    {takeable && confirming ? <div className="release-take-confirm" role="group" aria-label={`Take ${item.title}`} ref={confirmRef} tabIndex={-1}>
      <p aria-live="polite">{takeAnywayCopy(item, job.seriesTitle, job.issueNumber)}</p>
      <span>
        <button type="button" className="ghost-button" disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>
        <button type="button" className={`primary-button ${busy ? "loading" : ""}`} aria-busy={busy} disabled={busy} onClick={() => onTake(item)}>{busy ? <LoadingSpinner size={14} /> : <CloudArrowDown size={14} />} {busy ? "Sending…" : "Take it"}</button>
      </span>
    </div> : takeable ? <span className="release-set-aside-actions">
      <button type="button" className="ghost-button" disabled={!item.grabbable} title={item.grabbable ? undefined : item.source === "torrent" ? "Connect qBittorrent in Settings to download torrents." : "Downloading from this site needs a page fetcher; add one in Settings."} onClick={() => setConfirming(true)}>{item.grabbable ? "Take anyway" : "Not fetchable yet"}</button>
    </span> : null}
  </li>;
}

function SetAsideList({ items, job, admin, grabbingId, onTake }) {
  return <ul className="release-set-aside">{items.map((item) => <SetAsideRow key={item.id || item.title} item={item} job={job} admin={admin} busy={grabbingId === item.id} onTake={onTake} />)}</ul>;
}

function ReleaseSearchModal({ job, onClose, onGrabbed }) {
  const dialogRef = useDialog(onClose);
  const admin = isAdmin(useViewer());
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [grabbingId, setGrabbingId] = useState(null);
  // The query is editable. It looked like a field and was not one, which is
  // exactly when you want it: nothing was found, and the wording is the thing
  // worth changing.
  const [query, setQuery] = useState("");
  const [edited, setEdited] = useState(false);
  async function search(custom) {
    setLoading(true); setError("");
    const asked = typeof custom === "string" ? custom.trim() : "";
    try {
      const found = await apiRequest(`/api/v1/acquisition-jobs/${job.id}/search`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(asked ? { query: asked } : {}),
      });
      setResult(found);
      // Show the query that ran, which is not always the one first tried.
      if (!asked) setQuery(found?.query || "");
    } catch (searchError) {
      setError(searchError.message || "Prowlarr search failed");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { setEdited(false); setQuery(""); search(); }, [job.id]);
  async function grab(candidate, anyway = false) {
    setGrabbingId(candidate.id); setError("");
    try {
      await apiRequest(`/api/v1/acquisition-jobs/${job.id}/grab`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(anyway ? { candidateId: candidate.id, anyway: true } : { candidateId: candidate.id }),
      });
      await onGrabbed?.();
    } catch (grabError) {
      setError(grabError.message || "The download client could not accept this release");
      setGrabbingId(null);
    }
  }
  const candidates = result?.candidates || [];
  // Nothing offered is not the same as nothing found: say which, and why.
  const summary = releaseSearchSummary(result, `#${job.issueNumber}`);
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal release-search-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="release-search-title" onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close release search" /><span className="eyebrow">Find one missing issue</span><h2 id="release-search-title">{job.seriesTitle} #{job.issueNumber}</h2><p className="workbench-intro">Compare the results below. Nothing is downloaded until you choose a release.</p><form className="release-query" onSubmit={(event) => { event.preventDefault(); search(query); }}><MagnifyingGlass size={16} /><input value={query} onChange={(event) => { setQuery(event.target.value); setEdited(true); }} aria-label="Search terms" placeholder="Series and issue to search for…" disabled={loading} /><button type="submit" className="secondary-button" disabled={loading || query.trim().length < 2}>{loading ? <LoadingSpinner size={16} /> : null} Search</button></form>{edited ? null : <p className="release-query-note">Flipparr widens this automatically when a narrower wording finds nothing. Edit it to search for something else.</p>}{loading ? <div className="release-loading"><LoadingSpinner size={24} /><div><strong>Searching your indexers…</strong><span>This can take a few seconds.</span></div></div> : null}{error ? <div className="release-error"><WarningCircle size={19} weight="fill" /><span><strong>Release search needs attention</strong>{error}</span><button type="button" onClick={() => search(query)}>Try again</button></div> : null}{!loading && !error && !candidates.length ? <div className="release-empty"><MagnifyingGlass size={28} /><strong>{summary.headline}</strong><span>{summary.detail}</span>{summary.setAside.length ? <SetAsideList items={summary.setAside} job={job} admin={admin} grabbingId={grabbingId} onTake={(item) => grab(item, true)} /> : null}<button type="button" className="secondary-button" onClick={() => search(query)}>Search again</button></div> : null}{candidates.length ? <div className="release-candidates"><header><div><strong>{candidates.length} candidate{candidates.length === 1 ? "" : "s"}</strong><span>Best matches appear first. Confirm the title, issue, language, and format.</span></div></header>{candidates.map((candidate) => { const isGrabbing = grabbingId === candidate.id; return <article className="release-candidate" key={candidate.id}><div className="release-candidate-main"><StatusBadge tone={candidate.matchScore >= 85 ? "green" : "amber"}>{candidate.matchStrength}</StatusBadge><h3>{candidate.title}</h3><p>{candidate.indexer} · {releaseTransport(candidate)} · {formatReleaseSize(candidate.sizeBytes)}{candidate.publishDate ? ` · ${new Date(candidate.publishDate).toLocaleDateString()}` : ""}</p>{candidate.formatTags?.length ? <div className="release-tags">{candidate.formatTags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}</div><div className="release-match"><strong>{candidate.matchScore}</strong><span>match score</span></div><ul>{candidate.matchReasons.map((reason) => <li key={reason}><CheckCircle size={14} weight="fill" />{reason}</li>)}{candidate.grabbable === false && candidate.grabHint ? <li className="release-candidate-hint"><WarningCircle size={14} />{candidate.grabHint}</li> : null}</ul><button type="button" className={`primary-button ${isGrabbing ? "loading" : ""}`} aria-busy={isGrabbing} disabled={Boolean(grabbingId) || candidate.grabbable === false} title={candidate.grabbable === false ? candidate.grabHint : undefined} onClick={() => grab(candidate)}>{isGrabbing ? <LoadingSpinner size={17} /> : <CloudArrowDown size={17} />}{isGrabbing ? "Sending…" : candidate.grabbable === false ? "Not fetchable yet" : releaseSendLabel(candidate)}</button></article>; })}</div> : null}{candidates.length && summary.setAside.length ? <details className="release-set-aside-more"><summary>{summary.setAside.length} more {summary.setAside.length === 1 ? "was" : "were"} set aside</summary><SetAsideList items={summary.setAside} job={job} admin={admin} grabbingId={grabbingId} onTake={(item) => grab(item, true)} /></details> : null}<footer className="release-modal-footer"><ShieldCheck size={17} weight="fill" /> Prowlarr download links stay on the server and are never exposed in this page.</footer></section></div>;
}

function MetadataView({ items, loading = false, focus, backendStatus, onResolve, onReplace }) {
  const entries = items.length ? items : (backendStatus === "offline" ? DEMO_META_ITEMS : []);
  const [selected, setSelected] = useState(0);
  // Aim once per bell click, not once per catalog poll: the list refreshes
  // every few seconds and would otherwise keep yanking the selection back to
  // the notification the user has already moved on from. Re-runs on `entries`
  // so a focus set before the inbox loaded still lands.
  const aimedAt = useRef(null);
  useEffect(() => {
    if (!focus || aimedAt.current === focus) return;
    const index = entries.findIndex((entry) => entry.id === focus.id);
    if (index < 0) return;
    aimedAt.current = focus;
    setSelected(index);
  }, [focus, entries]);
  const [resolved, setResolved] = useState([]);
  const [mobileBrowseOpen, setMobileBrowseOpen] = useState(false);
  const selectedIndex = Math.min(selected, Math.max(entries.length - 1, 0));
  const item = entries[selectedIndex];
  const isResolved = resolved.includes(selectedIndex);
  if (loading) return <CatalogLoading title="Checking your library health…" detail="Bringing in damaged files and matches waiting for review." />;
  if (!item) return <><div className="empty-state"><CheckCircle size={35} weight="duotone" /><strong>Your library looks healthy</strong><span>Damaged files and uncertain matches will appear here after a scan.</span></div></>;
  async function resolveCurrent() {
    if (item.path && item.code && item.fingerprint) await onResolve(item);
    else setResolved((current) => [...current, selectedIndex]);
  }
  const fileProblem = item.category === "file" || ["empty_archive", "no_image_pages", "corrupt_archive", "file_health"].includes(item.code);
  function selectProblem(index) {
    setSelected(index);
    setMobileBrowseOpen(false);
  }
  return <><div className="metadata-layout"><nav className="mobile-inbox-nav" aria-label="Navigate library problems"><button type="button" disabled={selectedIndex === 0} onClick={() => selectProblem(selectedIndex - 1)} aria-label="Previous problem"><ArrowLeft size={18} /></button><button type="button" className="mobile-inbox-browser" aria-expanded={mobileBrowseOpen} onClick={() => setMobileBrowseOpen((open) => !open)}><ListBullets size={18} /><span><strong>Problem {selectedIndex + 1} of {entries.length}</strong><small>{mobileBrowseOpen ? "Close problem list" : "Browse all problems"}</small></span></button><button type="button" disabled={selectedIndex === entries.length - 1} onClick={() => selectProblem(selectedIndex + 1)} aria-label="Next problem"><ArrowRight size={18} /></button></nav><aside className={`inbox-list ${mobileBrowseOpen ? "mobile-browse-open" : ""}`}><div className="inbox-label">Needs attention <b>{entries.length - resolved.length}</b></div>{entries.map((entry, index) => { const replacementOpen = entry.replacementStatus && !["fulfilled", "cancelled"].includes(entry.replacementStatus); return <button key={entry.id ?? `${entry.file}-${entry.issue}`} className={`${selectedIndex === index ? "active" : ""} ${resolved.includes(index) ? "resolved" : ""} ${replacementOpen ? "replacement-triaged" : ""}`} onClick={() => selectProblem(index)}>{replacementOpen ? <CheckCircle size={19} weight="fill" /> : <WarningCircle size={19} weight={entry.severity === "error" ? "fill" : "regular"} />}<span><strong>{entry.file}</strong><small>{resolved.includes(index) ? "Fixed" : replacementOpen ? "Replacement requested · original retained" : `${entry.category === "file" ? "Comic file" : "Metadata"} · ${entry.issue}`}</small></span></button>; })}</aside><section className="review-panel"><div className="review-heading"><StatusBadge tone={item.severity === "error" ? "red" : "amber"}>{fileProblem ? "Comic file problem" : "Metadata review"}</StatusBadge><h2>{item.file}</h2><p>{item.detail}</p></div>{fileProblem ? <FileProblem item={item} onReplace={onReplace} /> : item.code === "metadata_conflict" && item.comparison ? <MetadataComparison comparison={item.comparison} /> : <MetadataProblem item={item} />}<div className="review-actions"><button className={fileProblem ? "ghost-button" : "primary-button"} disabled={isResolved} onClick={resolveCurrent}><CheckCircle size={19} /> {isResolved ? "Fixed" : "Mark fixed"}</button>{!fileProblem ? <button className="ghost-button"><PencilSimple size={18} /> Edit metadata</button> : null}<button className="ghost-button">Dismiss</button></div></section></div></>;
}

function FileProblem({ item, onReplace }) {
  const [revealState, setRevealState] = useState({ status: "idle", message: "" });
  useEffect(() => setRevealState({ status: "idle", message: "" }), [item.path]);
  async function revealFile() {
    if (!item.path) {
      setRevealState({ status: "error", message: "The file path is unavailable." });
      return;
    }
    setRevealState({ status: "loading", message: "Opening Finder…" });
    try {
      const result = await apiRequest("/api/v1/reveal-file", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: item.path }),
      });
      setRevealState({ status: "success", message: result.message || "Shown in Finder." });
    } catch (error) {
      setRevealState({ status: "error", message: error.message || "Finder could not show this file." });
    }
  }
  const replacementOpen = item.replacementStatus && !["fulfilled", "cancelled"].includes(item.replacementStatus);
  const replacementLabel = item.replacementStatus === "failed" ? "Replacement needs attention" : item.replacementStatus === "grabbed" ? "Replacement in progress" : "Replacement requested";
  return <div className="file-problem"><HardDrive size={35} weight="duotone" /><div><strong>{item.issue}</strong><div className="file-problem-actions">{replacementOpen ? <span className={`replacement-requested-inline ${item.replacementStatus === "failed" ? "failed" : ""}`}><CheckCircle size={18} weight="fill" /> {replacementLabel}</span> : <button className="primary-button" onClick={() => onReplace(item)}><CloudArrowDown size={18} /> Replace comic</button>}<button onClick={revealFile} disabled={revealState.status === "loading"}>{revealState.status === "loading" ? <LoadingSpinner size={18} /> : <FolderOpen size={18} />} {revealState.status === "loading" ? "Opening Finder…" : "Show comic file"}</button></div><small className="replacement-help">{replacementOpen ? "The original remains active until every replacement is downloaded, validated, and added to your library. Progress and failures appear under Pull List." : "Add the mapped issues to your wanted list without deleting this file. It stays in place until every replacement is downloaded and verified."}</small>{revealState.message ? <small className={`file-reveal-status ${revealState.status}`}>{revealState.message}</small> : null}</div></div>;
}

function MetadataProblem({ item }) {
  return <div className="metadata-problem"><Database size={32} weight="duotone" /><div><strong>{item.issue}</strong><small>This changes the library record only; the comic file is not modified.</small></div></div>;
}

function ReplacementModal({ file, busy, error, onClose, onSubmit }) {
  const dialogRef = useDialog(onClose);
  const automaticReason = file.code === "empty_archive" ? "empty" : file.code === "no_image_pages" ? "no_pages" : "corrupt";
  const [reason, setReason] = useState(automaticReason);
  const [language, setLanguage] = useState("English");
  const [acquisitionPreference, setAcquisitionPreference] = useState("issues");
  function submit(event) {
    event.preventDefault();
    onSubmit({ reason, desiredLanguage: reason === "wrong_language" ? language : null, acquisitionPreference });
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal replacement-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="replacement-title" onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close replacement request" /><span className="eyebrow">Fix comic run</span><h2 id="replacement-title">{file.file || file.filename}</h2><p className="workbench-intro">Add this comic’s mapped issues to the wanted list. Flipparr will keep the current file active until every replacement passes validation.</p><form onSubmit={submit}><label className="form-field"><span>Replacement format</span><GlassSelect label="Replacement format" value={acquisitionPreference} onChange={setAcquisitionPreference} className="glass-select--fill" options={[{ value: "issues", label: "Mapped single issues" }]} /><small>Whole-volume acquisition will be added after volume release matching is reliable.</small></label><label className="form-field"><span>Why replace it?</span><GlassSelect label="Why replace it?" value={reason} onChange={setReason} className="glass-select--fill" options={[{ value: "corrupt", label: "Corrupt or unreadable file" }, { value: "no_pages", label: "No readable comic pages" }, { value: "empty", label: "Empty archive" }, { value: "wrong_language", label: "Wrong language" }, { value: "wrong_release", label: "Wrong edition or release" }, { value: "poor_quality", label: "Poor scan or image quality" }]} /></label>{reason === "wrong_language" ? <label className="form-field"><span>Language wanted</span><input value={language} onChange={(event) => setLanguage(event.target.value)} placeholder="English" required /></label> : null}<div className="replacement-summary"><ShieldCheck size={19} weight="fill" /><span><strong>Recoverable replacement</strong><small>The original is moved to hidden quarantine only after all mapped issues pass identity, archive, copy, and hash checks.</small></span></div>{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="metadata-edit-actions"><button type="button" className="ghost-button" onClick={onClose}>Cancel</button><button type="submit" className="primary-button" aria-busy={busy} disabled={busy || (reason === "wrong_language" && !language.trim())}>{busy ? <LoadingSpinner size={18} /> : <CloudArrowDown size={18} />} {busy ? "Adding…" : "Add to wanted"}</button></div></form></section></div>;
}

function MetadataComparison({ comparison }) {
  return <div className="comparison"><div className="comparison-head"><span>Field</span><span>{comparison.catalogLabel}</span><span>{comparison.fileLabel}</span></div>{comparison.rows.map((row) => <div className="comparison-row" key={row.field}><strong>{row.field}</strong><span className={row.catalog ? "" : "missing"}>{row.catalog ? <CheckCircle size={16} weight="fill" /> : <WarningCircle size={16} />} {row.catalog || "Not available"}</span><span className={row.status}>{row.status === "match" ? <CheckCircle size={16} weight="fill" /> : <WarningCircle size={16} weight={row.status === "conflict" ? "fill" : "regular"} />} {row.file || "Not available"}</span></div>)}{comparison.catalogUrl ? <div className="comparison-source"><a href={comparison.catalogUrl} target="_blank" rel="noreferrer">Open metadata source</a></div> : null}</div>;
}

// Each section's name, the line under its title, and -- for a phone's list of
// them, grouped as iOS Settings groups its rows -- its icon and group.
const SETTINGS_SECTIONS = [
  { id: "profile", label: "Your profile", icon: UserCircle, group: "you",
    detail: "Your name and picture, what switching to you asks for, and this device." },
  { id: "health", label: "Library health", icon: Heartbeat, group: "library",
    detail: "Damaged files and matches waiting for you to confirm or fix." },
  { id: "library", label: "Library folders", icon: FolderOpen, group: "library",
    detail: "The folders Flipparr reads your comics from, and how often it checks them." },
  { id: "matching", label: "Matching and fixes", icon: Sparkle, group: "library",
    detail: "How Flipparr decides what a file is, and which formats it manages." },
  { id: "acquisition", label: "Acquisition services", icon: CloudArrowDown, group: "sources",
    detail: "Where releases are found and downloaded, and in which language." },
  { id: "metadata", label: "Metadata sources", icon: Database, group: "sources",
    detail: "Where issue titles, dates, covers and matches come from." },
  { id: "reader", label: "Reader", icon: BookOpen, group: "reading",
    detail: "Panel view, and the models that can help it with the pages it cannot read." },
  { id: "profiles", label: "Profiles", icon: Users, group: "app",
    detail: "Who reads this library. Each profile keeps its own place, history and ratings." },
  { id: "security", label: "Security", icon: LockSimple, group: "app",
    detail: "Who can reach this app, and how they sign in." },
  { id: "system", label: "System", icon: Pulse, group: "app",
    detail: "Whether the background work is running, what it has waiting, and what went wrong lately." },
  { id: "about", label: "About", icon: Info, group: "app",
    detail: "Flipparr's licence, where its information comes from, and what leaves this server." },
];

// The sections a profile is shown: everything for the admin; a reader's own
// profile and reader settings.
// A reader's sections say only what is theirs: the vision models behind
// panel view are the admin's.
const READER_SECTION_DETAIL = { reader: "How panel view reads a page, one panel at a time." };

function settingsSectionsFor(viewer) {
  return SETTINGS_SECTIONS.filter((item) => can(viewer, `settings.${item.id}`))
    .map((item) => (!isAdmin(viewer) && READER_SECTION_DETAIL[item.id] ? { ...item, detail: READER_SECTION_DETAIL[item.id] } : item));
}

// One block of a settings section, as a titled card; `action` sits at the
// title's end.
// The pages a person fixed by hand, as a file: out of this Flipparr and into
// another, or back into this one after a loss. Each fix is keyed to the
// page's own fingerprint, so it finds its page again under another file.
function PanelFixesCard() {
  const [summary, setSummary] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => apiRequest("/api/v1/panels/fixes").then(setSummary).catch(() => setSummary(null)), []);
  useEffect(() => { load(); }, [load]);
  async function importFile(file) {
    if (!file) return;
    setBusy(true); setError(""); setResult(null);
    try {
      const payload = JSON.parse(await file.text());
      const outcome = await apiRequest("/api/v1/panels/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      setResult(outcome);
      await load();
    } catch (problem) {
      setError(problem instanceof SyntaxError ? "That file is not JSON." : problem.message);
    } finally {
      setBusy(false);
    }
  }
  const pages = summary?.pages ?? 0;
  return <div className="panel-fixes">
    <p className="settings-card-lead">
      {summary === null ? "Your panel fixes." : pages
        ? `You have fixed ${pages} page${pages === 1 ? "" : "s"} in ${summary.comics} comic${summary.comics === 1 ? "" : "s"} by hand.`
        : "No pages fixed by hand yet."}
      {summary?.waitingForPages ? ` ${summary.waitingForPages} imported fix${summary.waitingForPages === 1 ? "" : "es"} are waiting for their pages to be read.` : ""}
      {" "}Each fix is kept against the page itself, so it follows the page to a replaced file or another Flipparr. Export them as a file to keep; import a file to bring them back.
    </p>
    <div className="settings-card-actions">
      <a className="ghost-button" href="/api/v1/panels/export" download>Export panel fixes</a>
      <label className={`ghost-button file-button${busy ? " busy" : ""}`}>
        <input type="file" accept=".json,application/json" disabled={busy} onChange={(event) => { const [file] = event.target.files || []; event.target.value = ""; importFile(file); }} />
        {busy ? <LoadingSpinner size={16} /> : null}{busy ? "Importing…" : "Import panel fixes"}
      </label>
      {result ? <small role="status">{result.attached} attached, {result.waiting} waiting for pages, {result.alreadyFixed} already fixed here{result.unusable ? `, ${result.unusable} unusable` : ""}.</small> : null}
      {error ? <small role="alert">{error}</small> : null}
    </div>
  </div>;
}

// A phone's first level of Settings, after iPhone Settings: grouped rows of
// an icon and a name, each opening its section as a page. A count rides
// beside the name rather than in a column of its own, which would set every
// row's columns differently and stagger the chevrons.
// Which build the app is: the server's, from /healthz, so a phone that has
// held on to an old page can be told apart from a bug in the new one.
function useBuild() {
  const [build, setBuild] = useState(() => lastAnswer("/healthz")?.build || "");
  useEffect(() => {
    let live = true;
    apiRequest("/healthz").then((data) => { if (live && data?.build) setBuild(data.build); }).catch(() => {});
    return () => { live = false; };
  }, []);
  return build;
}

function SettingsIndex({ counts = {}, onOpen, sections = SETTINGS_SECTIONS }) {
  const build = useBuild();
  const groups = [...new Set(sections.map((item) => item.group))];
  return <nav className="settings-index" aria-label="Settings sections">
    {groups.map((group) => <ul key={group}>
      {sections.filter((item) => item.group === group).map(({ id, label, icon: Icon }) => <li key={id}>
        <button type="button" onClick={() => onOpen(id)}
          aria-label={counts[id] ? `${label}. ${counts[id]} need${counts[id] === 1 ? "s" : ""} a decision` : undefined}>
          <span className={`settings-index-icon settings-index-icon--${id}`} aria-hidden="true"><Icon size={18} weight="fill" /></span>
          <span className="settings-index-label">
            <span>{label}</span>
            {counts[id] ? <b className="settings-index-badge">{counts[id]}</b> : null}
          </span>
          <CaretRight size={16} aria-hidden="true" />
        </button>
      </li>)}
    </ul>)}
    {build ? <p className="settings-build">Flipparr build {build}</p> : null}
  </nav>;
}

// The documented Docker mount, so most installs need no typing at all.
const DEFAULT_LIBRARY_FOLDER = "/comics";

const SETUP_STEPS = [
  { id: "welcome", label: "Welcome", required: false },
  {
    id: "library", label: "Folder path", required: true,
    title: "Select Folder Path",
    lead: "Files are read where they are. Nothing is renamed or moved.",
  },
  {
    id: "acquisition", label: "Download services", required: false,
    title: "Setup Download Services (Optional)",
    lead: "Finds and downloads issues you are missing.",
  },
  {
    id: "metadata", label: "Data sources", required: false,
    title: "Setup Data Sources",
    lead: "Where issue details, covers and dates come from.",
  },
];

function SetupStepper({ stepIndex }) {
  const steps = SETUP_STEPS.slice(1);
  const position = stepIndex - 1;
  return <ol className="setup-stepper" aria-label="Setup progress">
    {steps.map((step, index) => {
      const state = index < position ? "done" : index === position ? "current" : "upcoming";
      return <li className={state} key={step.id} aria-current={state === "current" ? "step" : undefined}>
        <span className="setup-step-marker">{state === "done" ? <Check size={14} weight="bold" /> : index + 1}</span>
        <span className="setup-step-label">{step.label}</span>
      </li>;
    })}
  </ol>;
}

function SetupLibraryStep({ folder, onFolderChange, recursive, onRecursiveChange, onCheckedChange, existingRoots }) {
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const notify = useRef(onCheckedChange);
  notify.current = onCheckedChange;

  // Re-check when the tab is returned to. Setting up a library usually means
  // going away to tidy the folder and coming back, and a count from before
  // that is worse than no count: it looks current and is not.
  const [recheck, setRecheck] = useState(0);
  useEffect(() => {
    const again = () => setRecheck((count) => count + 1);
    // Two separate signals rather than one guarded by visibility: a window that
    // regains focus is reason enough on its own, and an embedded or backgrounded
    // view can report itself hidden while still being looked at.
    const onVisibility = () => { if (document.visibilityState === "visible") again(); };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", again);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", again);
    };
  }, []);

  // Checked as you type rather than behind a separate button press. The folder
  // is pre-filled with the documented mount, so the common case is that the
  // count is already on screen before the user has done anything at all.
  useEffect(() => {
    const value = String(folder || "").trim();
    setResult(null);
    setError("");
    notify.current(null);
    if (!value) { setChecking(false); return undefined; }
    setChecking(true);
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const found = await apiRequest("/api/v1/library-folder-check", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ folder: value, recursive }),
        });
        if (cancelled) return;
        setResult(found);
        notify.current(found);
      } catch (caught) {
        if (!cancelled) setError(caught.message);
      } finally {
        if (!cancelled) setChecking(false);
      }
    }, 450);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [folder, recursive, recheck]);

  return <div className="setup-step-body">
    {existingRoots.length ? <div className="setup-existing-roots">
      <strong>Already added</strong>
      {existingRoots.map((root) => <span key={root.id}><FolderOpen size={16} /> {root.path}</span>)}
    </div> : null}
    <label className="setup-field">
      <span>Folder path</span>
      <input value={folder} onChange={(event) => onFolderChange(event.target.value)}
        placeholder={DEFAULT_LIBRARY_FOLDER} spellCheck={false} autoCapitalize="none" autoCorrect="off" />
    </label>
    <label className="setup-checkbox">
      <input type="checkbox" checked={recursive} onChange={(event) => onRecursiveChange(event.target.checked)} />
      <span>Include subfolders</span>
    </label>
    <p className={`setup-check ${checking ? "busy" : result ? "ok" : error ? "bad" : "idle"}`} role="status" aria-live="polite">
      {checking ? <><LoadingSpinner size={17} /> Looking…</>
        : result ? <><CheckCircle size={18} weight="fill" /> <span><strong>{result.comicCount}{result.countTruncatedAt ? "+" : ""}</strong> {result.comicCount === 1 ? "comic" : "comics"} ready to import.</span></>
        : error ? <><WarningCircle size={18} weight="fill" /> <span>{error}</span></>
        : <span>The folder you gave Flipparr access to.</span>}
    </p>
  </div>;
}

function SetupView({ catalog, onFinish }) {
  const [stepIndex, setStepIndex] = useState(0);
  const [folder, setFolder] = useState(DEFAULT_LIBRARY_FOLDER);
  const [folderChecked, setFolderChecked] = useState(null);
  const [recursive, setRecursive] = useState(true);
  const [services, setServices] = useState([]);
  const [editingService, setEditingService] = useState(null);
  const [providers, setProviders] = useState([]);
  const [editingProvider, setEditingProvider] = useState(null);
  const [finishing, setFinishing] = useState(false);
  const [error, setError] = useState("");
  const existingRoots = catalog?.roots || [];

  async function loadServices() {
    try { setServices((await apiRequest("/api/v1/acquisition-services")).services || []); } catch (caught) { setError(caught.message); }
  }
  async function loadProviders() {
    try { setProviders((await apiRequest("/api/v1/providers")).providers || []); } catch (caught) { setError(caught.message); }
  }
  useEffect(() => { loadServices(); loadProviders(); }, []);

  const step = SETUP_STEPS[stepIndex];
  // A folder is the one thing setup cannot invent: there is nothing to scan
  // without it. Everything after this is genuinely optional.
  const canLeaveLibraryStep = Boolean(folderChecked) || existingRoots.length > 0;

  async function finish() {
    setFinishing(true); setError("");
    try {
      await apiRequest("/api/v1/settings", {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ setupCompleted: true }),
      });
      await onFinish(folderChecked ? folderChecked.path : "", recursive);
    } catch (caught) {
      setError(caught.message);
      setFinishing(false);
    }
  }

  return <div className="setup-shell">
    <div className={`setup-card${step.id === "welcome" ? " welcome" : ""}`}>
      <header className="setup-header">
        {/* The welcome screen has the mark as its subject and no stepper to
            label, so the small mark above it would be the mark twice over. */}
        {step.id === "welcome" ? null : <div className="setup-topline">
          <span className="setup-brand"><FlipparrMark size={24} /></span>
          <SetupStepper stepIndex={stepIndex} />
        </div>}
        {step.id === "welcome" ? null : <>
          <div className="setup-step-heading">
            <h1>{step.title}</h1>
            <p>{step.lead}</p>
          </div>
        </>}
      </header>

      {step.id === "welcome" ? <div className="setup-welcome">
        <FlipparrMark size={176} decorative />
        {/* The action belongs to the copy: beside the artwork it follows the
            sentence it answers, and on a phone it lands on the card floor,
            where Back and Continue sit on every step after this one. */}
        <div className="setup-welcome-copy">
          <h1>Set up your comic library</h1>
          <p>Flipparr reads the comics on your disk, identifies them, and shows what is missing.</p>
          <div className="setup-welcome-actions">
            <button type="button" className="setup-pill primary" onClick={() => setStepIndex(1)}>Quick setup</button>
          </div>
        </div>
      </div> : null}

      {step.id === "library" ? <SetupLibraryStep
        folder={folder} recursive={recursive} existingRoots={existingRoots}
        onFolderChange={setFolder} onCheckedChange={setFolderChecked}
        onRecursiveChange={setRecursive} /> : null}

      {step.id === "acquisition" ? <div className="setup-step-body">
        {services.map((service) => <AcquisitionService service={service} concise onConfigure={() => setEditingService(service)} key={service.id} />)}
      </div> : null}

      {step.id === "metadata" ? <div className="setup-step-body">
        {/* Sources you can act on come first. The built-in ones have no button
            and nothing to decide, so they are one informational line at the
            bottom rather than two cards competing with the real choices. */}
        {providers.filter((provider) => !provider.builtIn && provider.kind !== "reading").map((provider) => <Provider provider={provider} concise onConfigure={() => setEditingProvider(provider)} key={provider.id} />)}
        <BuiltInSources providers={providers} />
        <aside className="setup-aside">
          <ClockCounterClockwise size={18} weight="fill" />
          <span>On a large library, a second source is the difference between a few minutes and most of an afternoon.</span>
        </aside>
      </div> : null}

      {error ? <p className="setup-check-error" role="alert"><WarningCircle size={18} weight="fill" /> {error}</p> : null}

      {step.id === "welcome" ? null : <footer className="setup-actions">
        <button type="button" className="setup-back" onClick={() => setStepIndex(stepIndex - 1)} disabled={finishing} aria-label="Back"><ArrowLeft size={18} /></button>
        {stepIndex < SETUP_STEPS.length - 1
          ? <button type="button" className="setup-pill primary setup-continue" disabled={step.id === "library" && !canLeaveLibraryStep} onClick={() => setStepIndex(stepIndex + 1)}>Continue</button>
          : <button type="button" className="setup-pill primary setup-continue" onClick={finish} disabled={finishing} aria-busy={finishing}>{finishing ? <LoadingSpinner size={18} /> : <CheckCircle size={18} weight="fill" />} Finish and scan</button>}
      </footer>}
    </div>
    {editingService ? <AcquisitionServiceSettingsModal service={editingService} onClose={() => setEditingService(null)} onSaved={async () => { await loadServices(); setEditingService(null); }} /> : null}
    {editingProvider ? <ProviderSettingsModal provider={editingProvider} onClose={() => setEditingProvider(null)} onSaved={async () => { await loadProviders(); setEditingProvider(null); }} /> : null}
  </div>;
}

// ---- Reader profiles ----------------------------------------------------------
//

// Adding a profile the way Plex adds one: a name. Its colour is given to it
// (one nobody else has); a picture, a PIN, a sign-in of its own come later,
// from Edit.
function AddProfileSheet({ onClose, onAdded }) {
  const dialogRef = useDialog(onClose);
  const [name, setName] = useState("");
  const [colour, setColour] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Your own profile needs a PIN (or a password) before anyone else can
  // join, or anyone at a shared device could open it; asked here, once,
  // rather than refused.
  const [guarded, setGuarded] = useState(null);
  const [ownPin, setOwnPin] = useState("");
  useEffect(() => {
    Promise.all([apiRequest("/api/v1/me"), apiRequest("/api/v1/users")])
      .then(([me, list]) => { setGuarded(isLocked(me?.profile)); setColour(nextProfileColour(list?.users)); })
      .catch(() => setGuarded(true));
  }, []);
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (guarded === false) {
        await apiRequest("/api/v1/me", {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pin: ownPin }),
        });
        setGuarded(true);
      }
      const created = await apiRequest("/api/v1/users", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
      });
      onAdded(created);
    } catch (failure) {
      setError(failure.message);
      setBusy(false);
    }
  }
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal add-profile-sheet" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="add-profile-title" onMouseDown={(event) => event.stopPropagation()}>
    <DialogCloseButton onClose={onClose} label="Close" />
    <h2 id="add-profile-title">Add a profile</h2>
    <p className="workbench-intro">Everyone who reads here gets their own place, history and ratings. You can give them a picture, a PIN or a sign-in of their own afterwards.</p>
    <form onSubmit={submit}>
      <div className="add-profile-preview"><ProfileAvatar profile={{ name: name || "?", colour }} size="lg" /></div>
      <label className="form-field"><span>Name</span>
        <input value={name} maxLength={40} autoFocus onChange={(event) => setName(event.target.value)} placeholder="Their name" />
      </label>
      {guarded === false ? <label className="form-field"><span>A PIN for your own profile</span>
        <input type="password" inputMode="numeric" autoComplete="off" value={ownPin}
          onChange={(event) => setOwnPin(event.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="Four to six digits" />
        <small>Asked when someone switches to you, so nobody else can open your profile.</small>
      </label> : null}
      {error ? <p className="workbench-error" role="alert">{error}</p> : null}
      <button className="primary-button add-profile-submit" disabled={busy || !name.trim() || guarded === null || (guarded === false && ownPin.length < 4)} aria-busy={busy}>
        {busy ? <LoadingSpinner size={17} /> : <Plus size={17} />} Add profile
      </button>
    </form>
  </section></div>;
}

// A profile's picture: a photo, or a cover from the library, or none.
function ProfilePictureEditor({ profile, catalog, onChanged }) {
  const fileRef = useRef(null);
  const [choosing, setChoosing] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  async function run(kind, request) {
    setBusy(kind);
    setError("");
    try {
      onChanged(await request());
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy("");
    }
  }
  const upload = (file) => run("upload", () => apiRequest(`/api/v1/profiles/${profile.id}/avatar/upload`, {
    method: "POST", headers: { "Content-Type": file.type || "image/jpeg" }, body: file,
  }));
  const choose = (fileId) => run("library", () => apiRequest(`/api/v1/profiles/${profile.id}/avatar`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fileId, page: 0 }),
  }));
  const remove = () => run("remove", () => apiRequest(`/api/v1/profiles/${profile.id}/avatar`, { method: "DELETE" }));
  return <div className="profile-picture-editor">
    <ProfileAvatar profile={profile} size="lg" />
    <div className="profile-picture-actions">
      <input ref={fileRef} type="file" accept="image/*" hidden onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) upload(file); }} />
      <button type="button" className="secondary-button" onClick={() => fileRef.current?.click()} disabled={Boolean(busy)}>
        {busy === "upload" ? <LoadingSpinner size={16} /> : <UploadSimple size={16} />} Upload a photo
      </button>
      <button type="button" className="secondary-button" onClick={() => setChoosing(true)} disabled={Boolean(busy) || !catalog?.series?.length}>
        {busy === "library" ? <LoadingSpinner size={16} /> : <Books size={16} />} Choose a cover
      </button>
      {profile.avatar ? <button type="button" className="secondary-button" onClick={remove} disabled={Boolean(busy)}>Remove picture</button> : null}
    </div>
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
    {choosing ? <LibraryPicturePicker catalog={catalog} onClose={() => setChoosing(false)} onChoose={(fileId) => { setChoosing(false); choose(fileId); }} /> : null}
  </div>;
}

// Any cover in the library as a picture: the comic reader's own answer to a
// profile photo.
function LibraryPicturePicker({ catalog, onClose, onChoose }) {
  const dialogRef = useDialog(onClose);
  const [filter, setFilter] = useState("");
  const runs = (catalog?.series || []).filter((series) => series.fileDetails?.length
    && (!filter || String(series.title || "").toLowerCase().includes(filter.toLowerCase())));
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal library-picture-picker" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="library-picture-title" onMouseDown={(event) => event.stopPropagation()}>
    <DialogCloseButton onClose={onClose} label="Close" />
    <h2 id="library-picture-title">Choose a cover</h2>
    <label className="form-field"><span>Find a run</span>
      <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Title" />
    </label>
    <div className="library-picture-grid">
      {runs.slice(0, 120).map((series) => <button type="button" key={series.id} onClick={() => onChoose(series.fileDetails[0].id)} aria-label={`Use the cover of ${series.title}`}>
        <SeriesCover series={series} decorative />
        <span>{series.title}</span>
      </button>)}
    </div>
  </section></div>;
}

// A profile's own page, as Plex has one: the picture large, the name, what
// they have read, and their reading history. On a phone it is a sheet over
// whatever page it was opened from (ProfileSheet) -- reached from the header,
// it has no tab, and as a page it left the tab bar nothing to show -- and
// `onLeave` closes the sheet before anything in it takes you elsewhere.
function ProfileView({ onRead, onNavigate, authStatus, onSwitch, sheet = false, onLeave = null }) {
  const viewer = useViewer();
  const [profile, setProfile] = useState(() => lastAnswer("/api/v1/me")?.profile ?? null);
  const [activity, setActivity] = useState(() => lastAnswer("/api/v1/me/activity"));
  useEffect(() => {
    apiRequest("/api/v1/me").then((data) => setProfile(data.profile)).catch(() => {});
    apiRequest("/api/v1/me/activity").then(setActivity).catch(() => {});
  }, []);
  const shown = profile || viewer;
  const stats = activity?.stats;
  const since = String(profile?.createdAt || "").slice(0, 4);
  const figures = stats ? [
    [stats.issuesRead, stats.issuesRead === 1 ? "issue read" : "issues read"],
    [stats.inProgress, "in progress"],
    [stats.runs, stats.runs === 1 ? "run" : "runs"],
    [stats.pagesRead, stats.pagesRead === 1 ? "page" : "pages"],
    [stats.finishedThisYear, `in ${stats.year}`],
  ] : [];
  const history = activity?.history || [];
  const leaving = (action) => (...args) => { onLeave?.(); action(...args); };
  return <>
    {sheet ? null : <PageHeader title="Profile" />}
    <section className="profile-hero">
      {shown?.avatar ? <img className="profile-hero-backdrop" src={shown.avatar} alt="" aria-hidden="true" /> : null}
      <ProfileAvatar profile={shown} size="xl" />
      <h2>{shown?.name}</h2>
      {since ? <p className="profile-hero-since">Reading here since {since}</p> : null}
      {figures.length ? <dl className="profile-stats">{figures.map(([value, label]) => <div key={label}>
        <dd>{Number(value || 0).toLocaleString()}</dd><dt>{label}</dt>
      </div>)}</dl> : <p className="profile-stats-loading"><LoadingSpinner size={18} /></p>}
      <div className="profile-hero-actions">
        <button type="button" className="secondary-button" onClick={leaving(() => onNavigate("settings", "profile"))}><PencilSimple size={18} /> Edit profile</button>
        {authStatus?.household ? <button type="button" className="secondary-button" onClick={leaving(onSwitch)}><UserCircle size={18} /> Switch profile</button> : null}
      </div>
    </section>
    <section className="profile-history" aria-labelledby="profile-history-title">
      <h2 id="profile-history-title">Reading history</h2>
      {activity && !history.length ? <p className="profile-history-empty">Comics you read appear here, newest first.</p> : null}
      <div className="profile-history-shelf">{history.map((item) => {
        const fraction = item.finishedAt ? 1 : item.pageCount ? Math.min(1, (item.page + 1) / item.pageCount) : 0;
        return <button type="button" className="profile-history-card" key={item.fileId} onClick={leaving(() => onRead({ id: item.fileId }))}>
          <span className="profile-history-cover">
            {/* A comic's first page is its cover, and a page a reader may fetch. */}
            <img src={`/api/v1/files/${item.fileId}/pages/0`} alt="" loading="lazy" />
            {item.finishedAt ? <b className="profile-history-done" aria-label="Finished"><Check size={14} weight="bold" /></b> : null}
            {!item.finishedAt && fraction ? <i className="profile-history-progress" style={{ "--progress": `${Math.round(fraction * 100)}%` }} aria-hidden="true" /> : null}
          </span>
          <strong>{item.seriesTitle || item.filename}</strong>
          <small>{[item.issueNumber ? `#${item.issueNumber}` : "", item.finishedAt ? "Finished" : item.pageCount ? `Page ${item.page + 1} of ${item.pageCount}` : ""].filter(Boolean).join(" · ")}</small>
        </button>;
      })}</div>
    </section>
  </>;
}

// Your profile on a phone: a sheet the height of the screen over the page you
// were on, as an iOS app's account sheet is, with the picture's blur filling
// its top. It pulls down and slides away like every sheet.
function ProfileSheet({ onClose, ...props }) {
  const dialogRef = useDialog(onClose);
  return createPortal(<div className="modal-backdrop" onMouseDown={onClose}>
    <section className="modal profile-page-sheet" ref={dialogRef} role="dialog" aria-modal="true" aria-label="Your profile"
      onMouseDown={(event) => event.stopPropagation()}>
      <SheetGrabber onClose={onClose} pullAnywhere />
      <button type="button" className="glass-button glass-button--icon profile-page-sheet-close"
        onClick={() => slideSheetAway(dialogRef.current, onClose)} aria-label="Close"><X size={20} /></button>
      <ProfileView {...props} sheet onLeave={onClose} />
    </section>
  </div>, document.body);
}

// "Who's reading?" -- a shared device with more than one profile asks before
// showing anyone's library. A profile with a PIN asks for it; the admin
// without a PIN asks for their password; a reader with neither just opens.
function WhoIsReadingView({ signInAvailable, overlay = false, current = null, ask = null, onClose, canAdd = false, onAdd }) {
  const [profiles, setProfiles] = useState(null);
  const [asking, setAsking] = useState(ask);
  // Escape while a PIN or password is asked goes back to the profiles, not out.
  const overlayRef = useDialog(overlay ? () => (asking ? back() : onClose?.()) : null);
  function back() { setAsking(null); setError(""); }
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    apiRequest("/api/v1/profiles").then((data) => setProfiles(data.profiles || [])).catch((failure) => setError(failure.message));
  }, []);
  async function enter(profile, proof = {}) {
    setBusy(true);
    setError("");
    try {
      const result = await apiRequest("/api/v1/profiles/switch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ userId: profile.id, ...proof }),
      });
      enterProfile(result.viewer.id);
      return true;
    } catch (failure) {
      setError(failure.message);
      setBusy(false);
      return false;
    }
  }
  function choose(profile) {
    // Choosing who is already reading is choosing to carry on.
    if (profile.id === current) { onClose?.(); return; }
    if (isLocked(profile)) {
      setAsking(profile);
      setSecret("");
      setError("");
    } else {
      enter(profile);
    }
  }
  return <div className={`login-shell profile-picker${overlay ? " profile-picker--overlay" : ""}`}
    ref={overlay ? overlayRef : undefined} role={overlay ? "dialog" : undefined} aria-modal={overlay ? "true" : undefined} aria-label={overlay ? "Who's reading?" : undefined}>
    {overlay ? <button type="button" className="glass-button glass-button--icon profile-picker-close" onClick={() => onClose?.()} aria-label="Close"><X size={20} /></button> : null}
    <div className="profile-picker-card">
      <FlipparrMark size={48} />
      <h1>{asking ? asking.name : "Who’s reading?"}</h1>
      {asking?.lock === "pin" ? <div className="profile-picker-secret">
        <ProfileAvatar profile={asking} size="lg" />
        <PinPad length={asking.pinLength} busy={busy} error={error} onCancel={back} onInput={() => setError("")}
          onSubmit={(pin) => enter(asking, { pin })} />
      </div> : asking ? <form className="profile-picker-secret" onSubmit={(event) => {
        event.preventDefault();
        enter(asking, asking.lock === "pin" ? { pin: secret } : { password: secret });
      }}>
        <ProfileAvatar profile={asking} size="lg" />
        <label className="form-field"><span>{asking.lock === "pin" ? "PIN" : "Password"}</span>
          <input type="password" autoFocus value={secret} name={asking.lock === "pin" ? "pin" : "password"}
            inputMode={asking.lock === "pin" ? "numeric" : undefined}
            autoComplete={asking.lock === "pin" ? "off" : "current-password"}
            onChange={(event) => setSecret(asking.lock === "pin" ? event.target.value.replace(/\D/g, "").slice(0, 6) : event.target.value)} />
        </label>
        {error ? <p className="login-error" role="alert">{error}</p> : null}
        <div className="profile-picker-actions">
          <button type="button" className="secondary-button" onClick={() => { setAsking(null); setError(""); }}>Back</button>
          <button className="primary-button" disabled={busy || !secret} aria-busy={busy}>
            {busy ? <LoadingSpinner size={18} /> : null} Continue
          </button>
        </div>
      </form> : <>
        {profiles === null && !error ? <LoadingSpinner size={22} /> : null}
        <ProfileTiles profiles={profiles || []} current={current} onChoose={choose} busy={busy}
          onAdd={canAdd && profiles ? () => onAdd?.() : null} />
        {error ? <p className="login-error" role="alert">{error}</p> : null}
        {signInAvailable ? <button type="button" className="profile-picker-other" onClick={() => enterProfile(null)}>
          Sign in with a password instead
        </button> : null}
      </>}
    </div>
  </div>;
}

// The admin's list of profiles. Readers read and rate; the library, what is
// downloaded and every setting stay the admin's.
function ProfilesSettings({ catalog, onCatalogChanged }) {
  const header = useContext(HeaderContext);
  const [users, setUsers] = useState(() => lastAnswer("/api/v1/users")?.users ?? null);
  const [security, setSecurity] = useState(() => lastAnswer("/api/v1/auth") ?? null);
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState("");
  async function load() {
    try {
      const [list, auth] = await Promise.all([apiRequest("/api/v1/users"), apiRequest("/api/v1/auth")]);
      setUsers(list.users || []);
      setSecurity(auth);
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
  }
  // Reloaded when a profile is added from anywhere -- the header's menu, the picker.
  useEffect(() => { load(); }, [header?.profile?.version]);
  const owner = users?.find((user) => user.id === 1);
  const guarded = isLocked(owner);
  return <>
    <SettingsCard title="Profiles" action={<button type="button" className="secondary-button" onClick={() => header?.profile?.onAdd()} disabled={!guarded}><Plus size={18} /> Add profile</button>}>
      <p className="settings-card-lead">Readers can read and rate; the library, downloads and settings stay yours.</p>
      {users && !guarded ? <p className="settings-card-note">First give your own profile a PIN (in Your profile) or a password (in Security), so nobody at a shared device can open yours.</p> : null}
      {users === null && !error ? <CatalogLoading title="Loading profiles…" detail="" /> : null}
      {error ? <p className="workbench-error" role="alert">{error}</p> : null}
      <div className="profile-rows">
        {(users || []).map((user) => <button type="button" className="profile-row" key={user.id} onClick={() => setEditing(user)}>
          <ProfileAvatar profile={user} />
          <span><strong>{user.name}</strong><small>{[
            user.role === "admin" ? "Admin" : "Reader",
            user.disabled ? "Off" : "",
            LOCK_LABELS[user.lock] ?? "",
            limitLabel(user),
            user.loginName ? `Signs in as ${user.loginName}` : "",
          ].filter(Boolean).join(" · ")}</small></span>
          <CaretRight size={16} aria-hidden="true" />
        </button>)}
      </div>
    </SettingsCard>
    <RatingsCard catalog={catalog} profiles={users || []} onCatalogChanged={onCatalogChanged} />
    <SettingsCard title="Shared devices">
      <p className="settings-card-lead">A device you sign in on with your password asks &ldquo;Who&rsquo;s reading?&rdquo; from then on, like a Plex Home. A reader who signs in with their own password on their own phone gets only their profile.</p>
    </SettingsCard>
    {editing ? <ProfileEditorDrawer user={editing} catalog={catalog} adminPassword={Boolean(security?.configured)} onClose={() => setEditing(null)}
      onPictureChanged={async (updated) => { setEditing(updated); await load(); }}
      onSaved={async () => { setEditing(null); await load(); }} /> : null}
  </>;
}

// What switching to a profile asks for, as the profile chooses: a tap, its
// PIN or its password (the admin's is the one in Security). Your profile and
// the admin's editor both draw it; the form around it saves.
function SwitchLockFields({ profile, self = false, adminPassword, lock, onLock, pin, onPin, password, onPassword }) {
  const owner = profile?.id === 1;
  const choices = lockChoices(profile, { adminPassword });
  const reasons = choices.filter((choice) => choice.disabled && choice.reason).map((choice) => choice.reason);
  const whom = self ? "you" : profile?.name || "them";
  const detail = {
    open: `Anyone at a shared device can switch to ${whom} with a tap.`,
    pin: `Switching to ${whom} asks for a PIN.`,
    password: owner ? `Switching to ${whom} asks for the password in Security.` : `Switching to ${whom} asks for ${self ? "your" : "their"} password.`,
  }[lock];
  return <div className="switch-lock">
    <GlassSegmented label="When switching, ask for" items={choices} value={lock} onChange={onLock} />
    <p className="settings-card-note">{[detail, ...reasons].join(" ")}</p>
    {lock === "pin" ? <label className="form-field"><span>{profile?.hasPin ? "New PIN" : "PIN"}</span>
      <input type="password" inputMode="numeric" autoComplete="off" value={pin}
        onChange={(event) => onPin(event.target.value.replace(/\D/g, "").slice(0, 6))}
        placeholder={profile?.hasPin ? "Leave blank to keep it" : "Four to six digits"} />
    </label> : null}
    {lock === "password" && !owner && !profile?.hasPassword ? <label className="form-field"><span>Password</span>
      <input type="password" autoComplete="new-password" value={password} onChange={(event) => onPassword(event.target.value)}
        placeholder="At least 8 characters" />
    </label> : null}
  </div>;
}

// How many runs have a rating and where it came from, and the switch that
// lets the vision connector read the ratings printed on covers.
function RatingsCard({ catalog, profiles = [], onCatalogChanged }) {
  const [summary, setSummary] = useState(null);
  const [reviewing, setReviewing] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  async function load() {
    try { setSummary(await apiRequest("/api/v1/ratings")); } catch (failure) { setError(failure.message); }
  }
  useEffect(() => { load(); }, []);
  async function run(kind, action) {
    setBusy(kind);
    setError("");
    try { await action(); await load(); } catch (failure) { setError(failure.message); }
    setBusy("");
  }
  const toggleCovers = (on) => run("covers", () => apiRequest("/api/v1/settings", {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ratingsFromCovers: on }),
  }));
  const lookAgain = () => run("again", () => apiRequest("/api/v1/ratings/check", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  }));
  const parts = summary ? [
    summary.by_metron ? `${summary.by_metron} from Metron` : null,
    summary.by_cover ? `${summary.by_cover} from covers` : null,
    summary.by_admin ? `${summary.by_admin} set by you` : null,
  ].filter(Boolean) : [];
  return <SettingsCard title="Ratings" action={summary ? <button type="button" className="secondary-button" onClick={lookAgain} disabled={Boolean(busy)} aria-busy={busy === "again"}>
    {busy === "again" ? <LoadingSpinner size={16} /> : <ArrowsClockwise size={16} />} Look again</button> : null}>
    {summary ? <>
      <p className="settings-card-lead">{summary.rated} of {summary.runs} run{summary.runs === 1 ? " has" : "s have"} a rating{parts.length ? `: ${parts.join(", ")}` : ""}.{summary.waiting ? ` ${summary.waiting} still to look at.` : ""} A profile&rsquo;s limit is set in its profile, and a run&rsquo;s rating in its Edit sheet.</p>
      <Toggle checked={summary.fromCovers} onChange={busy || !summary.visionConnected ? () => {} : toggleCovers} disabled={!summary.visionConnected}
        title="Read ratings printed on covers"
        description={summary.visionConnected
          ? "For runs Metron has no rating for, the newest cover is sent to your vision connector to read the rating printed there, such as DC's 13+ TEEN or Marvel's RATED T+. One cover per run, on your key."
          : "Connect Claude or ChatGPT in Settings, Reader first."} />
      <div className="ratings-card-review">
        <span><strong>Rate runs yourself</strong><small>{summary.runs - summary.rated ? `${summary.runs - summary.rated} have no rating. ` : ""}Rate many at once, or check what a profile can see.</small></span>
        <button type="button" className="secondary-button" onClick={() => setReviewing(true)} disabled={!catalog?.series}><ListChecks size={18} /> Set ratings</button>
      </div>
    </> : error ? null : <LoadingSpinner size={18} />}
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
    {reviewing ? <RatingReviewModal catalog={catalog} profiles={profiles} onClose={() => setReviewing(false)}
      onSaved={async () => { await onCatalogChanged?.(); load(); }} /> : null}
  </SettingsCard>;
}

/**
 * Many runs rated at once. The view is what nothing rated, what one profile
 * can see right now, or everything; a publisher or a title narrows it; the
 * ticked runs get one rating, or go back to what was found. Saying who no
 * longer sees them is the proof it did what it says.
 */
function RatingReviewModal({ catalog, profiles, onClose, onSaved }) {
  const dialogRef = useDialog(onClose);
  const [rows, setRows] = useState(() => ratingRows(catalog?.series));
  useEffect(() => { setRows(ratingRows(catalog?.series)); }, [catalog]);
  const limited = limitedProfiles(profiles);
  const [show, setShow] = useState("unrated");
  const [publisher, setPublisher] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(() => new Set());
  const [rating, setRating] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const shown = filterRatingRows(rows, { show, profiles: limited, publisher, query });
  const chosen = rows.filter((row) => selected.has(row.id)).map((row) => row.id);
  const allShown = shown.length > 0 && shown.every((row) => selected.has(row.id));
  const count = (view) => filterRatingRows(rows, { show: view, profiles: limited }).length;
  const showOptions = [
    { value: "unrated", label: `No rating (${count("unrated")})` },
    ...limited.map((profile) => ({ value: String(profile.id), label: `What ${profile.name} can see (${count(String(profile.id))})` })),
    { value: "all", label: `All runs (${rows.length})` },
  ];
  const publisherOptions = [{ value: "any", label: "All publishers" },
    ...publisherChoices(rows).map((house) => ({ value: house.key, label: `${house.label} (${house.count})` }))];
  function view(next) {
    setShow(next);
    setSelected(new Set());
  }
  function toggle(id) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  function toggleShown() {
    setSelected((current) => {
      const next = new Set(current);
      for (const row of shown) {
        if (allShown) next.delete(row.id); else next.add(row.id);
      }
      return next;
    });
  }
  async function apply() {
    if (busy || !chosen.length || !rating) return;
    const value = rating === "found" ? null : rating;
    setBusy(true);
    setError("");
    try {
      await apiRequest("/api/v1/ratings/runs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seriesIds: chosen.map(Number), rating: value }),
      });
      setStatus(ratingOutcome(rows, chosen, value, profiles));
      setRows((current) => current.map((row) => chosen.includes(row.id)
        ? { ...row, override: value, rating: value || row.found, source: value ? "admin" : row.foundSource }
        : row));
      setSelected(new Set());
      await onSaved?.();
    } catch (failure) {
      setError(failure.message);
    }
    setBusy(false);
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}>
    <section className="modal rating-review" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="rating-review-title" onMouseDown={(event) => event.stopPropagation()}>
      <DialogCloseButton onClose={onClose} label="Close ratings" />
      <span className="eyebrow">Ratings</span>
      <h2 id="rating-review-title">Set ratings</h2>
      <p className="workbench-intro">Rate the runs nothing has rated, or go through what a profile can see. Your rating outranks Metron&rsquo;s and the cover&rsquo;s; &ldquo;What was found&rdquo; takes it back.</p>
      <div className="rating-review-filters">
        <GlassSelect label="Show" value={show} onChange={view} options={showOptions} className="glass-select--fill" />
        <GlassSelect label="Publisher" value={publisher || "any"} onChange={(next) => setPublisher(next === "any" ? "" : next)}
          options={publisherOptions} className="glass-select--fill" />
        <label className="form-field rating-review-search"><span className="sr-only">Find a run</span>
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Title or year" />
        </label>
      </div>
      <label className="check-row rating-review-all">
        <input type="checkbox" checked={allShown} onChange={toggleShown} disabled={!shown.length} />
        <span><strong>{allShown ? "Clear these" : "Select these"}</strong><small>{shown.length} run{shown.length === 1 ? "" : "s"} shown</small></span>
      </label>
      {shown.length ? <ul className="rating-review-list">
        {shown.map((row) => <li key={row.id}><label className="rating-review-row">
          <input type="checkbox" checked={selected.has(row.id)} onChange={() => toggle(row.id)} />
          <span className="rating-review-cover"><CoverArt id={row.id} title={row.title} cover={row.cover} coverCandidates={row.coverCandidates} decorative placeholderSize={16} /></span>
          <span><strong>{row.title}{row.year ? ` (${row.year})` : ""}</strong><small>{[row.publisher, ratingNote(row)].filter(Boolean).join(" · ")}</small></span>
        </label></li>)}
      </ul> : <p className="rating-review-empty">{show === "unrated" && !publisher && !query ? "Every run has a rating." : "No runs here."}</p>}
      <p className="rating-review-status" aria-live="polite">{status}</p>
      {error ? <p className="workbench-error" role="alert">{error}</p> : null}
      <div className="rating-review-actions">
        <span>{chosen.length} selected</span>
        <GlassSelect label="Rating" placeholder="Choose a rating" value={rating} onChange={setRating}
          options={[...RATINGS.map((id) => ({ value: id, label: RATING_LABELS[id] })), { value: "found", label: "What was found" }]} />
        <button type="button" className="primary-button" onClick={apply} disabled={busy || !chosen.length || !rating} aria-busy={busy || undefined}>
          {busy ? <LoadingSpinner size={18} /> : <Check size={18} />} Rate
        </button>
      </div>
    </section>
  </div>;
}

// A drawer, as a run's details are: a profile's settings are a long form,
// and a centred modal outgrew an iPad's screen with Save below the fold.
function ProfileEditorDrawer({ user, catalog, adminPassword, onClose, onSaved, onPictureChanged }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  useSwipeToDismiss(dialogRef, requestClose);
  const creating = !user.id;
  const owner = user.id === 1;
  const [name, setName] = useState(user.name || "");
  const [lock, setLock] = useState(user.lock || "open");
  const [pin, setPin] = useState("");
  const [loginName, setLoginName] = useState(user.loginName || "");
  const [password, setPassword] = useState("");
  const [showOnPicker, setShowOnPicker] = useState(user.showOnPicker ?? true);
  const [maxRating, setMaxRating] = useState(user.maxRating || "");
  const [allowUnrated, setAllowUnrated] = useState(Boolean(user.allowUnrated));
  const [canDiscover, setCanDiscover] = useState(user.canDiscover ?? true);
  // A first limit keeps them out of Discover too, as the server does; the
  // switch is there to say otherwise.
  function chooseLimit(next) {
    if (next && !maxRating) setCanDiscover(false);
    setMaxRating(next);
  }
  const lockEdit = creating ? {} : lockPatch(user, lock, { pin, password });
  // The lock's own password field stands in for the sign-in one while there is none.
  const lockAsksPassword = !creating && !owner && lock === "password" && !user.hasPassword;
  const [enabled, setEnabled] = useState(!user.disabled);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  async function call(path, method, body) {
    return apiRequest(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  }
  async function save(event) {
    event.preventDefault();
    setBusy("save");
    setError("");
    const payload = { name, showOnPicker, ...lockEdit };
    if (!owner) Object.assign(payload, { disabled: !enabled, maxRating: maxRating || null, allowUnrated, canDiscover });
    if (creating && pin) payload.pin = pin;
    if (!owner && (loginName || user.loginName) && loginName !== (user.loginName || "")) payload.loginName = loginName || null;
    if (!owner && password) payload.password = password;
    try {
      if (creating) await call("/api/v1/users", "POST", payload);
      else await call(`/api/v1/users/${user.id}`, "PATCH", payload);
      await onSaved();
    } catch (failure) {
      setError(failure.message);
      setBusy("");
    }
  }
  async function act(kind) {
    setBusy(kind);
    setError("");
    try {
      if (kind === "signout") await call(`/api/v1/users/${user.id}/sign-out`, "POST", {});
      if (kind === "remove") await call(`/api/v1/users/${user.id}`, "DELETE");
      await onSaved();
    } catch (failure) {
      setError(failure.message);
      setBusy("");
    }
  }
  // Grouped as Settings groups a section: a card for each thing the admin
  // decides about the profile, and the profile's actions at the foot.
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}><aside className={`series-drawer profile-editor ${closing ? "closing" : ""}`} ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="profile-editor-title" onMouseDown={(event) => event.stopPropagation()}>
    <DialogCloseButton onClose={requestClose} label="Close profile" drawer />
    <span className="eyebrow">{creating ? "New profile" : owner ? "Admin" : "Reader"}</span>
    <h2 id="profile-editor-title">{creating ? "Add a profile" : user.name}</h2>
    <div className="profile-editor-cards">
      {/* Outside the form, the name joined to it by id: the cover picker's
          search must not submit it. */}
      <SettingsCard title="Profile">
        <div className="profile-form">
          {creating ? null : <ProfilePictureEditor profile={user} catalog={catalog} onChanged={(updated) => onPictureChanged?.(updated)} />}
          <label className="form-field"><span>Name</span>
            <input form="profile-editor-form" value={name} maxLength={40} autoFocus={creating} onChange={(event) => setName(event.target.value)} placeholder="Their name" />
          </label>
        </div>
      </SettingsCard>
      <form id="profile-editor-form" className="profile-editor-cards" onSubmit={save}>
        {creating ? null : <SettingsCard title="When switching">
          <SwitchLockFields profile={user} adminPassword={adminPassword} lock={lock} onLock={setLock}
            pin={pin} onPin={setPin} password={password} onPassword={setPassword} />
        </SettingsCard>}
        <SettingsCard title={owner ? "Sign-in" : "Their own sign-in"}>
          {owner ? <p className="settings-card-note">Your sign-in name and password are in Security.</p> : <div className="profile-form">
            <p className="settings-card-lead">A name and password let them sign in on their own phone, without a shared device.</p>
            <label className="form-field"><span>Sign-in name</span>
              <input value={loginName} autoCapitalize="none" autoCorrect="off" spellCheck={false}
                onChange={(event) => setLoginName(event.target.value.trim())} placeholder="Optional" />
            </label>
            {lockAsksPassword ? <p className="settings-card-note">They sign in with the password set under When switching.</p>
              : <label className="form-field"><span>{user.hasPassword ? "New password" : "Password"}</span>
                <input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)}
                  placeholder={user.hasPassword ? "Leave blank to keep it" : "At least 8 characters"} />
              </label>}
          </div>}
        </SettingsCard>
        {owner || creating ? null : <SettingsCard title="What they can read">
          <div className="profile-form">
            <div className="form-field"><span>Highest rating</span>
              <GlassSelect label="Highest rating" value={maxRating || "any"} onChange={(next) => chooseLimit(next === "any" ? "" : next)}
                className="glass-select--fill"
                options={[{ value: "any", label: "No limit" }, ...RATINGS.filter((id) => id !== "mature").map((id) => ({ value: id, label: `Up to ${RATING_LABELS[id]}` }))]} /></div>
            <p className="settings-card-note">{maxRating
              ? `Runs rated above ${RATING_LABELS[maxRating]} are hidden from ${name || "them"}, with everything in them.`
              : `${name || "They"} can read everything in the library.`}</p>
            {maxRating ? <>
              <Toggle checked={allowUnrated} onChange={setAllowUnrated} title="Unrated comics"
                description="Many runs have no rating yet, and Image and Boom print none. Off, they are hidden too." />
              <Toggle checked={canDiscover} onChange={setCanDiscover} title="Discover"
                description="New releases and online search carry no rating to filter by, so they show every cover." />
            </> : null}
          </div>
        </SettingsCard>}
        <SettingsCard title="Access">
          <Toggle checked={showOnPicker} onChange={setShowOnPicker} title="Show on shared devices"
            description="Listed when a shared device asks who is reading." />
          {owner || creating ? null : <Toggle checked={enabled} onChange={setEnabled} title="Profile on"
            description="Off, this profile cannot be opened or signed in to. Its history is kept." />}
          {creating ? null : <div className="settings-card-actions">
            <button type="button" className="secondary-button" onClick={() => act("signout")} disabled={Boolean(busy)}>Sign out everywhere</button>
          </div>}
        </SettingsCard>
        {error ? <p className="workbench-error" role="alert">{error}</p> : null}
        <div className="provider-modal-actions">
          {!creating && !owner ? <button type="button" className="danger-button" onClick={() => act("remove")} disabled={Boolean(busy)}>Remove</button> : null}
          <span />
          <button className="primary-button" disabled={Boolean(busy) || !name.trim() || !lockEdit}>
            {busy === "save" ? <><LoadingSpinner size={17} /> Saving…</> : creating ? "Add profile" : "Save"}
          </button>
        </div>
      </form>
    </div>
  </aside></div>;
}

// A profile about itself: name, picture, what switching to it asks for; a
// reader's own sign-in; and who is reading on this device.
function YourProfileSettings({ authStatus, onSignOut, catalog, onViewerChanged }) {
  const viewer = useViewer();
  const header = useContext(HeaderContext);
  const [profile, setProfile] = useState(() => lastAnswer("/api/v1/me")?.profile ?? null);
  const [name, setName] = useState(profile?.name ?? viewer?.name ?? "");
  const [lock, setLock] = useState(profile?.lock ?? "open");
  const [pin, setPin] = useState("");
  const [lockPassword, setLockPassword] = useState("");
  const [adminPassword, setAdminPassword] = useState(false);
  const [loginName, setLoginName] = useState(profile?.loginName ?? "");
  const [password, setPassword] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState({ card: "", text: "" });
  const admin = isAdmin(viewer);
  useEffect(() => {
    apiRequest("/api/v1/me").then((data) => {
      setProfile(data.profile);
      setName(data.profile?.name ?? "");
      setLock(data.profile?.lock ?? "open");
      setLoginName(data.profile?.loginName ?? "");
    }).catch(() => {});
    // Whether the admin has a password in Security to be asked for.
    if (admin) apiRequest("/api/v1/auth").then((auth) => setAdminPassword(Boolean(auth?.configured))).catch(() => {});
  }, []);
  async function patch(body, done) {
    setBusy(done);
    setError({ card: "", text: "" });
    setMessage("");
    try {
      const updated = await apiRequest("/api/v1/me", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setProfile(updated);
      setLock(updated.lock ?? "open");
      setPin("");
      setLockPassword("");
      setPassword("");
      setCurrentPassword("");
      setMessage(done);
      onViewerChanged?.();
    } catch (failure) {
      setError({ card: done, text: failure.message });
    } finally {
      setBusy("");
    }
  }
  function saveProfile(event) {
    event.preventDefault();
    patch({ name }, "profile");
  }
  const lockEdit = profile && (lock !== profile.lock || pin || lockPassword)
    ? lockPatch(profile, lock, { pin, password: lockPassword }) : null;
  function saveLock(event) {
    event.preventDefault();
    if (lockEdit) patch(lockEdit, "lock");
  }
  function saveSignIn(event) {
    event.preventDefault();
    const body = { loginName: loginName || null };
    if (password) Object.assign(body, { password, currentPassword });
    patch(body, "signin");
  }
  const household = Boolean(authStatus?.household);
  return <>
    <SettingsCard title="Profile">
      <div className="profile-form">
        {/* The picture saves itself; the form is the name's alone, so the
            cover picker's search cannot submit it. */}
        {profile ? <ProfilePictureEditor profile={profile} catalog={catalog} onChanged={(updated) => { setProfile(updated); onViewerChanged?.(); }} /> : <LoadingSpinner size={18} />}
        <form className="profile-form" onSubmit={saveProfile}>
          <label className="form-field"><span>Name</span><input value={name} maxLength={40} onChange={(event) => setName(event.target.value)} /></label>
          {error.card === "profile" ? <p className="workbench-error" role="alert">{error.text}</p> : null}
          <div className="settings-card-actions">
            <button className="primary-button" disabled={Boolean(busy) || !name.trim()} aria-busy={busy === "profile"}>
              {busy === "profile" ? <LoadingSpinner size={18} /> : null} Save
            </button>
            {message === "profile" && !busy ? <small role="status">Saved</small> : null}
          </div>
        </form>
      </div>
    </SettingsCard>
    <SettingsCard title="Switching to you">
      {profile ? <form className="profile-form" onSubmit={saveLock}>
        <SwitchLockFields profile={profile} self adminPassword={adminPassword} lock={lock} onLock={setLock}
          pin={pin} onPin={setPin} password={lockPassword} onPassword={setLockPassword} />
        {error.card === "lock" ? <p className="workbench-error" role="alert">{error.text}</p> : null}
        <div className="settings-card-actions">
          <button className="primary-button" disabled={Boolean(busy) || !lockEdit} aria-busy={busy === "lock"}>
            {busy === "lock" ? <LoadingSpinner size={18} /> : null} Save
          </button>
          {message === "lock" && !busy ? <small role="status">Saved</small> : null}
        </div>
      </form> : <LoadingSpinner size={18} />}
    </SettingsCard>
    {admin ? null : <SettingsCard title="Your own sign-in">
      <form className="profile-form" onSubmit={saveSignIn}>
        <p className="settings-card-lead">A name and password let you sign in to your profile on your own phone, without a shared device.</p>
        <label className="form-field"><span>Sign-in name</span>
          <input value={loginName} autoCapitalize="none" autoCorrect="off" spellCheck={false} autoComplete="username"
            onChange={(event) => setLoginName(event.target.value.trim())} />
        </label>
        <label className="form-field"><span>{profile?.hasPassword ? "New password" : "Password"}</span>
          <input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="At least 8 characters" />
        </label>
        {profile?.hasPassword && password ? <label className="form-field"><span>Current password</span>
          <input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
        </label> : null}
        {error.card === "signin" ? <p className="workbench-error" role="alert">{error.text}</p> : null}
        <div className="settings-card-actions">
          <button className="primary-button" disabled={Boolean(busy)} aria-busy={busy === "signin"}>{busy === "signin" ? <LoadingSpinner size={18} /> : null} Save sign-in</button>
        </div>
      </form>
    </SettingsCard>}
    <SettingsCard title="This device">
      <p className="settings-card-lead">{household
        ? "This is a shared device: switching asks who is reading."
        : "Signed in on this device as you."}</p>
      <div className="settings-card-actions">
        {household ? <button type="button" className="secondary-button" onClick={() => header?.profile?.onPicker()}><UserCircle size={18} /> Switch profile</button> : null}
        {authStatus?.method === "forms" ? <button type="button" className="secondary-button" onClick={() => onSignOut({ forgetDevice: true })}>
          <SignOut size={18} /> {household ? "Sign out and forget this device" : "Sign out"}
        </button> : null}
      </div>
    </SettingsCard>
  </>;
}

// A setting that is one switch: shown at once, saved, and put back if the
// save fails.
function useSettingToggle(key, last) {
  const [value, setValue] = useState(Boolean(last?.[key]));
  const [saving, setSaving] = useState(false);
  async function save(next) {
    setSaving(true);
    setValue(next);
    try {
      const result = await apiRequest("/api/v1/settings", {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [key]: next }),
      });
      setValue(Boolean(result?.[key]));
    } catch {
      setValue(!next);
    } finally {
      setSaving(false);
    }
  }
  return [value, save, saving];
}

// Settings -> About: the licence, the credits each source asks for, and
// what leaves this server. NOTICE and docs/PRIVACY.md say the same for
// whoever reads the repository.
const DATA_CREDITS = [
  { name: "Grand Comics Database", href: "https://www.comics.org/",
    text: "Series, issue and creator data from the Grand Comics Database\u2122, used under the Creative Commons Attribution-ShareAlike 4.0 licence.",
    licence: { label: "CC BY-SA 4.0", href: "https://creativecommons.org/licenses/by-sa/4.0/" } },
  { name: "Metron", href: "https://metron.cloud/",
    text: "Issue details, release dates, age ratings and story arcs, through Metron\u2019s API with your account." },
  { name: "Comic Vine", href: "https://comicvine.gamespot.com/",
    text: "Issue and volume details through Comic Vine\u2019s API with your key, for the non-commercial use its terms allow." },
  { name: "Open Library", href: "https://openlibrary.org/",
    text: "Book details and covers for collected editions, from the Internet Archive\u2019s Open Library." },
  { name: "Google Books", href: "https://books.google.com/",
    text: "Book details for collected editions, when you add a Google Books API key. Powered by Google." },
  { name: "Community reading lists", href: "https://github.com/DieselTech/CBL-ReadingLists",
    text: "Story-arc reading lists their authors share on GitHub, fetched when you pick one. Flipparr ships none of them." },
];

const SOFTWARE_CREDITS = [
  { name: "React", licences: [["MIT", "/licenses/React-MIT.txt"]] },
  { name: "Phosphor Icons", licences: [["MIT", "/licenses/Phosphor-Icons-MIT.txt"]] },
  { name: "Heroicons", licences: [["MIT", "/licenses/Heroicons-MIT.txt"]] },
  { name: "Material Design Icons", licences: [["Pictogrammers Free License", "/licenses/MaterialDesignIcons-Pictogrammers-License.txt"], ["Apache 2.0", "/licenses/Apache-2.0.txt"]] },
  { name: "Inter", licences: [["SIL Open Font License 1.1", "/licenses/Inter-OFL-1.1.txt"]] },
];

const PRIVACY_SECTIONS = [
  { title: "Stays on this server", points: [
    "Your library, its catalogue, and each profile\u2019s reading history, ratings and requests.",
    "Passwords, stored only as scrypt hashes, and API keys, which are never sent back to a browser.",
    "No analytics, no telemetry, nothing sent to Flipparr\u2019s authors. Fonts and icons are part of the app; it loads no outside scripts.",
  ] },
  { title: "Goes to the services you use", points: [
    "Metadata sources get titles, issue numbers and ISBNs from your files as searches: the Grand Comics Database and Open Library without an account, Metron, Comic Vine and Google Books with yours.",
    "Prowlarr, your download clients and a direct-download site you enter get the issues you want.",
    "A vision model, only if you add a key: the pages you read in panel view, sent to Anthropic or OpenAI under your account. Every page only with \u201cAsk about every page\u201d on; readers\u2019 pages only with \u201cReaders\u2019 pages too\u201d on; covers for age ratings only when that is on.",
    "GitHub, for community reading lists, only when they are turned on.",
  ] },
  { title: "In your browser", points: [
    "A sign-in cookie, and on a shared device a device cookie for its profiles. No tracking cookies.",
    "Some cover previews load straight from a metadata source. Flipparr sends no referrer with them, or with any link out.",
  ] },
  { title: "In the server log", points: [
    "Each request\u2019s page, result and profile. The address it came from only with \u201cRecord visitor addresses\u201d on, under Security.",
  ] },
];

// Settings -> System, after the *arr apps' System -> Status: is the background
// work alive, what does it have waiting, what went wrong lately -- and a
// support file that is safe to hand to someone else. Admin only.
function SystemSettings({ onNavigate, onSectionChange }) {
  const [status, setStatus] = useState(() => lastAnswer("/api/v1/system/status"));
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await apiRequest("/api/v1/system/status"));
      setError("");
    } catch (failure) {
      setError(failure.message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    load();
    const timer = window.setInterval(load, 30000);
    return () => window.clearInterval(timer);
  }, [load]);
  const go = (target) => (target === "health" ? onSectionChange("health") : onNavigate(target));
  if (!status) {
    return error
      ? <p className="workbench-error" role="alert">System status is unavailable: {error}</p>
      : <CatalogLoading title="Checking the background work…" detail="Workers, waiting work and recent problems." />;
  }
  const rows = waitingWork(status.work);
  const cooling = coolingProviders(status.work);
  const silent = status.services || [];
  const scan = status.work?.lastScan;
  const disk = (item) => item?.error ? "Unavailable" : `${formatBytes(item?.freeBytes)} free of ${formatBytes(item?.totalBytes)}`;
  return <>
    <SettingsCard title="Flipparr" action={<button type="button" className="secondary-button" onClick={load} disabled={loading} aria-busy={loading}>
      {loading ? <LoadingSpinner size={16} /> : <ArrowsClockwise size={16} />} Refresh</button>}>
      <dl className="system-facts">
        <div><dt>Build</dt><dd>{status.version} · {status.build}</dd></div>
        <div><dt>Running since</dt><dd>{new Date(status.startedAt).toLocaleString()}</dd></div>
        <div><dt>Database</dt><dd>SQLite {status.database?.sqlite} · {String(status.database?.journalMode || "").toUpperCase()} · schema {status.database?.schema}</dd></div>
        <div><dt>Server</dt><dd>{status.server?.name} · {status.server?.threads} threads</dd></div>
        <div><dt>Library data</dt><dd>{disk(status.disk?.config)}</dd></div>
        <div><dt>Temporary files</dt><dd>{disk(status.disk?.temp)}</dd></div>
      </dl>
      {error ? <p className="workbench-error" role="alert">Could not refresh: {error}</p> : null}
    </SettingsCard>
    <SettingsCard title="Background work">
      <ul className="about-list system-workers">
        {(status.workers || []).map((worker) => {
          const badge = workerBadge(worker.state);
          const advice = workerAdvice(worker);
          return <li key={worker.id}>
            <span className="system-row-head"><strong>{worker.name}</strong><StatusBadge tone={badge.tone}>{badge.label}</StatusBadge></span>
            <span>{worker.description}{worker.lastActivityAt ? ` Last active ${agoPhrase(worker.lastActivityAt)}.` : ""}</span>
            {advice ? <span>{advice}</span> : null}
            {worker.lastProblem ? <span>Last problem, {agoPhrase(worker.lastProblem.at)}: {worker.lastProblem.message}</span> : null}
          </li>;
        })}
      </ul>
    </SettingsCard>
    <SettingsCard title="Waiting work">
      {rows.length || cooling.length || silent.length || scan ? <ul className="about-list">
        {silent.map((item) => <li key={item.service}>
          <span className="system-row-head"><strong>{item.title}</strong><StatusBadge tone="amber">since {new Date(item.since).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</StatusBadge></span>
          <span>{item.detail}{item.lastProblem && item.reason === "silent" ? ` Last: ${item.lastProblem}` : ""}</span>
        </li>)}
        {rows.map((row) => <li key={row.id}>
          <span className="system-row-head"><strong>{row.label}</strong>{row.tone ? <StatusBadge tone={row.tone}>{row.value}</StatusBadge> : <b>{row.value}</b>}</span>
          {row.go ? <span><button type="button" className="discover-text-button" onClick={() => go(row.go)}>{row.go === "health" ? "Open Library health" : "Open Requests"}</button></span> : null}
        </li>)}
        {cooling.map((item) => <li key={item.provider}>
          <span className="system-row-head"><strong>{item.provider === "comic_vine" ? "Comic Vine" : item.provider === "gcd" ? "Grand Comics Database" : item.provider[0].toUpperCase() + item.provider.slice(1)} is asking Flipparr to wait</strong><StatusBadge tone="amber">until {new Date(item.until).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</StatusBadge></span>
          <span>Metadata from it resumes on its own{item.lastError ? `. It said: ${item.lastError}` : ""}.</span>
        </li>)}
        {scan ? <li>
          <span className="system-row-head"><strong>Last library scan</strong><StatusBadge tone={scanBadge(scan.status).tone}>{scanBadge(scan.status).label}</StatusBadge></span>
          <span>{scan.completed_at ? `Finished ${new Date(scan.completed_at).toLocaleString()}` : `Started ${new Date(scan.started_at).toLocaleString()}`} · {Number(scan.processed_files || 0).toLocaleString()} files checked, {Number(scan.changed_files || 0).toLocaleString()} changed{scan.error ? `. ${scan.error}` : ""}</span>
        </li> : null}
      </ul> : <p className="settings-card-lead">Nothing is waiting.</p>}
    </SettingsCard>
    <SettingsCard title="Recent problems">
      {status.problems?.length ? <ul className="about-list system-problems">
        {status.problems.map((problem, index) => <li className="job-failure-copy" key={`${problem.at}-${problem.event}-${index}`}>
          <span className="system-row-head"><strong>{problemTitle(problem.event)}</strong><StatusBadge tone={problem.level === "error" ? "red" : "amber"}>{timeAgo(problem.at)}</StatusBadge></span>
          {problem.message ? <span>{problem.message}</span> : null}
          {problem.traceback || Object.keys(problem.detail || {}).length ? <details><summary>Technical details</summary>
            <code>{[Object.keys(problem.detail || {}).length ? JSON.stringify(problem.detail, null, 2) : "", problem.traceback || ""].filter(Boolean).join("\n\n")}</code>
          </details> : null}
        </li>)}
      </ul> : <p className="settings-card-lead">No problems since Flipparr started.</p>}
    </SettingsCard>
    <SettingsCard title="Support">
      <p className="settings-card-lead">A file with everything on this page and which services are set up, for when you ask someone for help. It holds no passwords, keys, tokens or service addresses.</p>
      <button type="button" className="secondary-button" onClick={() => downloadFrom("/api/v1/system/diagnostics")}><DownloadSimple size={18} /> Download diagnostics</button>
    </SettingsCard>
  </>;
}

function AboutSettings() {
  const build = useBuild();
  return <>
    <SettingsCard title="Flipparr">
      <p className="settings-card-lead">Free software under the <a href="/licenses/gpl-3.0.txt" target="_blank" rel="noopener noreferrer">GNU General Public License, version 3</a>. It ships no comics, no catalogue data and no download sources: every service it uses is one whoever runs it sets up.{build ? ` Build ${build}.` : ""}</p>
    </SettingsCard>
    <SettingsCard title="Where the information comes from">
      <ul className="about-list">
        {DATA_CREDITS.map((item) => <li key={item.name}>
          <a href={item.href} target="_blank" rel="noopener noreferrer"><strong>{item.name}</strong></a>
          <span>{item.text}{item.licence ? <> <a href={item.licence.href} target="_blank" rel="noopener noreferrer">{item.licence.label}</a></> : null}</span>
        </li>)}
      </ul>
      <p className="settings-card-note">Flipparr is independent: none of these services makes, endorses or supports it.</p>
    </SettingsCard>
    <SettingsCard title="Software">
      <ul className="about-list">
        {SOFTWARE_CREDITS.map((item) => <li key={item.name}>
          <strong>{item.name}</strong>
          <span>{item.licences.map(([label, href], index) => <span key={href}>{index ? " or " : ""}<a href={href} target="_blank" rel="noopener noreferrer">{label}</a></span>)}</span>
        </li>)}
      </ul>
      <p className="settings-card-note">The server&rsquo;s own components (Python, Pillow, ONNX Runtime, libarchive) are listed with their licences in the NOTICE file that comes with Flipparr.</p>
    </SettingsCard>
    <SettingsCard title="Privacy">
      {PRIVACY_SECTIONS.map((section) => <section className="about-privacy" key={section.title}>
        <h4>{section.title}</h4>
        <ul className="about-list">{section.points.map((point) => <li key={point}><span>{point}</span></li>)}</ul>
      </section>)}
    </SettingsCard>
  </>;
}

function SettingsView({ catalog, backendStatus, logicalSeriesCount, onNavigate, onAuthChanged, onSignOut, section, onSectionChange, health, onScanLibrary, scanState, scanProgress, authStatus, onCatalogChanged }) {
  const viewer = useViewer();
  const admin = isAdmin(viewer);
  const sections = settingsSectionsFor(viewer);
  const settingsLast = lastAnswer("/api/v1/settings");
  const [collectedEditions, setCollectedEditions] = useState(Boolean(settingsLast?.collectedEditionsEnabled));
  const [savingCollectedEditions, setSavingCollectedEditions] = useState(false);
  const [language, setLanguage] = useState(settingsLast?.preferredLanguage ?? "en");
  const [savingLanguage, setSavingLanguage] = useState(false);
  // Where releases are taken from first; each move saves at once.
  const [releaseOrder, setReleaseOrder] = useState(() => sourceOrder(settingsLast?.sourcePriority));
  const [orderStatus, setOrderStatus] = useState("");
  const [autoScan, setAutoScan] = useState(settingsLast?.autoScanEnabled ?? true);
  const [autoScanInterval, setAutoScanInterval] = useState(Number(settingsLast?.autoScanIntervalMinutes) || 60);
  const [savingAutoScan, setSavingAutoScan] = useState(false);
  const [everyPage, setEveryPage] = useState(settingsLast?.visionReadsEveryPage ?? false);
  const [communityLists, saveCommunityLists, savingCommunityLists] = useSettingToggle("communityListsEnabled", settingsLast);
  const [logAddresses, saveLogAddresses, savingLogAddresses] = useSettingToggle("logClientAddresses", settingsLast);
  const [forReaders, setForReaders] = useState(Boolean(settingsLast?.visionForReaders));
  const [savingEveryPage, setSavingEveryPage] = useState(false);
  const [providers, setProviders] = useState(() => lastAnswer("/api/v1/providers")?.providers ?? []);
  const [providerError, setProviderError] = useState("");
  const [editingProvider, setEditingProvider] = useState(null);
  const [services, setServices] = useState(() => lastAnswer("/api/v1/acquisition-services")?.services ?? []);
  const [serviceError, setServiceError] = useState("");
  const [editingService, setEditingService] = useState(null);
  async function loadProviders() {
    try {
      const result = await apiRequest("/api/v1/providers");
      setProviders(result.providers || []);
      setProviderError("");
    } catch (error) {
      setProviderError(error.message);
    }
  }
  async function loadServices() {
    try {
      const result = await apiRequest("/api/v1/acquisition-services");
      setServices(result.services || []);
      setServiceError("");
    } catch (error) {
      setServiceError(error.message);
    }
  }
  async function loadAppSettings() {
    try {
      const result = await apiRequest("/api/v1/settings");
      setCollectedEditions(Boolean(result?.collectedEditionsEnabled));
      setLanguage(result?.preferredLanguage ?? "en");
      setReleaseOrder(sourceOrder(result?.sourcePriority));
      setAutoScan(result?.autoScanEnabled ?? true);
      setAutoScanInterval(Number(result?.autoScanIntervalMinutes) || 60);
      setEveryPage(result?.visionReadsEveryPage ?? true);
      setForReaders(Boolean(result?.visionForReaders));
    } catch {
      /* settings fall back to defaults */
    }
  }
  async function toggleForReaders(next) {
    setSavingEveryPage(true);
    setForReaders(next);
    try {
      const result = await apiRequest("/api/v1/settings", {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ visionForReaders: next }),
      });
      setForReaders(Boolean(result?.visionForReaders));
    } catch {
      setForReaders(!next);
    } finally {
      setSavingEveryPage(false);
    }
  }
  async function toggleEveryPage(next) {
    setSavingEveryPage(true);
    setEveryPage(next);
    try {
      const result = await apiRequest("/api/v1/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ visionReadsEveryPage: next }),
      });
      setEveryPage(Boolean(result?.visionReadsEveryPage));
    } catch {
      setEveryPage(!next);
    } finally {
      setSavingEveryPage(false);
    }
  }
  async function toggleCollectedEditions(next) {
    setSavingCollectedEditions(true);
    setCollectedEditions(next);
    try {
      const result = await apiRequest("/api/v1/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ collectedEditionsEnabled: next }),
      });
      setCollectedEditions(Boolean(result?.collectedEditionsEnabled));
    } catch {
      setCollectedEditions(!next);
    } finally {
      setSavingCollectedEditions(false);
    }
  }
  async function changeLanguage(next) {
    const previous = language;
    setSavingLanguage(true);
    setLanguage(next);
    try {
      const result = await apiRequest("/api/v1/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preferredLanguage: next }),
      });
      setLanguage(result?.preferredLanguage ?? next);
    } catch {
      setLanguage(previous);
    } finally {
      setSavingLanguage(false);
    }
  }
  async function saveReleaseOrder(next) {
    const previous = releaseOrder;
    if (next.join() === previous.join()) return;
    setReleaseOrder(next);
    setOrderStatus("");
    try {
      const result = await apiRequest("/api/v1/settings", {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sourcePriority: next }),
      });
      const saved = sourceOrder(result?.sourcePriority);
      setReleaseOrder(saved);
      const first = sourceRows(saved, services)[0];
      setOrderStatus(`Saved. ${first.name} is tried first.`);
    } catch (error) {
      setReleaseOrder(previous);
      setOrderStatus(error.message || "The order could not be saved");
    }
  }
  // Background scans: whether, and how often. Shown at once, put back if the
  // save fails.
  async function saveAutoScan(patch) {
    const previous = { enabled: autoScan, interval: autoScanInterval };
    setSavingAutoScan(true);
    if ("autoScanEnabled" in patch) setAutoScan(patch.autoScanEnabled);
    if ("autoScanIntervalMinutes" in patch) setAutoScanInterval(patch.autoScanIntervalMinutes);
    try {
      const result = await apiRequest("/api/v1/settings", {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
      });
      setAutoScan(result?.autoScanEnabled ?? true);
      setAutoScanInterval(Number(result?.autoScanIntervalMinutes) || 60);
    } catch {
      setAutoScan(previous.enabled);
      setAutoScanInterval(previous.interval);
    } finally {
      setSavingAutoScan(false);
    }
  }
  // The library's settings are the admin's; a reader's page asks for none of them.
  useEffect(() => { if (admin) { loadProviders(); loadServices(); loadAppSettings(); } }, [admin]);
  const roots = catalog?.roots || [];
  // A scan started here, from the rail or folders page, or in the background.
  const scanning = scanState === "scanning" || Boolean(catalog?.activeScan);
  // Above 640px, the sections are listed beside the one open, and one is
  // always open; on a phone, the list is a page of its own and each section
  // opens over it, as iPhone Settings does.
  const phone = usePhoneWidth();
  const current = section || (phone ? "" : admin ? "health" : "profile");
  const open = sections.find((item) => item.id === current);
  const needAttention = Number(catalog?.stats?.needAttention ?? 0);
  const modals = <>
    {editingProvider ? <ProviderSettingsModal provider={editingProvider} onClose={() => setEditingProvider(null)} onSaved={async () => { await loadProviders(); setEditingProvider(null); }} /> : null}
    {editingService ? <AcquisitionServiceSettingsModal service={editingService} onClose={() => setEditingService(null)} onSaved={async () => { await loadServices(); setEditingService(null); }} /> : null}
  </>;
  if (!open) {
    return <><PageHeader title="Settings" />
      <SettingsIndex onOpen={onSectionChange} counts={{ health: needAttention }} sections={sections} />
      {modals}
    </>;
  }
  const languageOptions = [["en", "English"], ["fr", "French"], ["es", "Spanish"], ["de", "German"], ["it", "Italian"], ["pt", "Portuguese"], ["ru", "Russian"], ["ja", "Japanese"], ["ko", "Korean"], ["zh", "Chinese"], ["pl", "Polish"], ["nl", "Dutch"], ["", "No preference"]];
  return <>
    <PageHeader title={phone ? open.label : "Settings"}
      leading={phone ? <button type="button" className="glass-button glass-button--icon" onClick={() => onSectionChange("")} aria-label="Back to Settings"><ArrowLeft size={20} /></button> : null} />
    <div className={`settings-shell${current === "health" ? " settings-shell--wide" : ""}`}>
      {/* The column runs the panel's full height, for its rule; the list in
          it stays in view as a long section scrolls. */}
      {phone ? null : <nav className="settings-nav" aria-label="Settings sections"><div>
        {sections.map(({ id, label }) => <button type="button" key={id}
          className={id === current ? "active" : ""} aria-current={id === current ? "page" : undefined}
          onClick={() => onSectionChange(id)}>
          <span>{label}</span>{id === "health" && needAttention ? <b>{needAttention}</b> : null}
        </button>)}
      </div></nav>}
      <div className="settings-pane">
        <header className="settings-pane-header">
          {phone ? null : <h2>{open.label}</h2>}
          <p>{open.detail}</p>
        </header>
        {current === "profile" ? <YourProfileSettings authStatus={authStatus} onSignOut={onSignOut} catalog={catalog} onViewerChanged={onAuthChanged} /> : null}
        {current === "profiles" ? <ProfilesSettings catalog={catalog} onCatalogChanged={onCatalogChanged} /> : null}
        {current === "health" ? <MetadataView {...health} /> : null}
        {current === "library" ? catalogPending(catalog, backendStatus)
          ? <CatalogLoading title="Loading your library folders…" detail="Checking which folders Flipparr already scans." />
          : <>
            <SettingsCard title="Folders" className="settings-library-folders"
              action={<button type="button" className="secondary-button" onClick={() => onNavigate("import")}><FolderOpen size={18} /> Manage folders</button>}>
              <p className="settings-card-lead">{roots.length ? `${roots.length} folder${roots.length === 1 ? "" : "s"} scanned for comics.` : "No library folders are configured yet."}</p>
              {roots.length ? <p className="settings-library-size"><strong>{Number(catalog?.stats?.files ?? 0).toLocaleString()}</strong> files · <strong>{Number(logicalSeriesCount ?? 0).toLocaleString()}</strong> series</p> : null}
              {roots.length ? <div className="settings-root-list">{roots.map((root) => <span key={root.id}><FolderOpen size={17} /><strong>{root.path}</strong><small>{root.recursive ? "Includes subfolders" : "Top level only"}</small></span>)}</div> : null}
              <ScanProgress scanState={scanState} scanProgress={scanProgress} />
              {/* The rail's scan button is hidden on a phone; this is the one there. */}
              {roots.length ? <div className="settings-card-actions">
                <button type="button" className={`primary-button ${scanning ? "loading" : ""}`} onClick={onScanLibrary} disabled={scanning} aria-busy={scanning}>{scanning ? <LoadingSpinner size={18} /> : <ArrowsClockwise size={18} />}{scanning ? "Scanning…" : "Scan library"}</button>
                <small>{scanning ? "Scanning now" : catalog?.lastScan?.iso ? `Last scanned ${catalog.lastScan.date} at ${catalog.lastScan.time}` : "Not scanned yet"}</small>
              </div> : null}
            </SettingsCard>
            {roots.length ? <SettingsCard title="Automatic scans">
              <Toggle checked={autoScan} onChange={savingAutoScan ? () => {} : (next) => saveAutoScan({ autoScanEnabled: next })} title="Scan automatically" description="Checks your library folders in the background, so comics added outside Flipparr appear without a manual scan." />
              {autoScan ? <div className="form-field settings-language settings-scan-interval"><span>How often</span><GlassSelect label="How often" value={autoScanInterval} disabled={savingAutoScan}
                onChange={(next) => saveAutoScan({ autoScanIntervalMinutes: Number(next) })}
                options={[{ value: 15, label: "Every 15 minutes" }, { value: 60, label: "Every hour" }, { value: 360, label: "Every 6 hours" }, { value: 1440, label: "Once a day" }]} /></div> : null}
            </SettingsCard> : null}
          </> : null}
        {current === "matching" ? <>
          <SettingsCard title="How matches are accepted">
            <p className="settings-card-lead">Matches are accepted automatically when the evidence is strong. Flipparr scores every match from corroborating and conflicting evidence (filename, embedded metadata, provider agreement). Confident matches are applied without review; anything below that threshold, or with conflicting evidence, waits under Library health for you to confirm or fix.</p>
          </SettingsCard>
          <SettingsCard title="Formats">
            <Toggle checked={collectedEditions} onChange={savingCollectedEditions ? () => {} : toggleCollectedEditions} title="Collected editions (trades, hardcovers, omnibuses)" description="Off by default. Turn on to browse and manage collected editions alongside Issues. Their metadata and file availability are less complete than Issues, and they are never used to fulfill Issue ownership or acquisition." />
          </SettingsCard>
        </> : null}
        {current === "security" ? <>
          <SecuritySettings onChanged={onAuthChanged} onSignOut={onSignOut} />
          <SettingsCard title="Request log">
            <Toggle checked={logAddresses} onChange={savingLogAddresses ? () => {} : saveLogAddresses} title="Record visitor addresses"
              description="On, each line of the server log names the address a request came from, which helps trace who reached the app. Off, the log keeps the page, the result and the profile, and no addresses. Sign-in limits work either way." />
          </SettingsCard>
        </> : null}
        {current === "about" ? <AboutSettings /> : null}
        {current === "system" ? <SystemSettings onNavigate={onNavigate} onSectionChange={onSectionChange} /> : null}
        {current === "acquisition" ? <>
          <SettingsCard title="Download order" className="metadata-source-settings">
            <p className="settings-card-lead">Flipparr takes a release from the first source here that has one good enough. Pulling a whole run, a pack still comes first; pulling one issue, a single does.</p>
            <SourceOrderList rows={sourceRows(releaseOrder, services)} onReorder={saveReleaseOrder} />
            <p className="source-order-status" aria-live="polite">{orderStatus}</p>
          </SettingsCard>
          {groupServices(services).map((group) => <SettingsCard title={group.title} className="metadata-source-settings" key={group.id}>
            <p className="settings-card-lead">{group.lead}</p>
            {group.services.map((service) => <AcquisitionService service={service} onConfigure={() => setEditingService(service)} key={service.id} />)}
          </SettingsCard>)}
          {serviceError ? <p className="workbench-error" role="alert">{serviceError}</p> : null}
          <SettingsCard title="Language">
            <div className="form-field settings-language"><span>Language wanted</span><GlassSelect label="Language wanted" value={language} disabled={savingLanguage} onChange={changeLanguage}
              options={languageOptions.map(([value, label]) => ({ value, label }))} /><small>A release that says it is another language is never grabbed, and one that says so only once downloaded is refused instead of filed under the issue it claims to be. Releases that say nothing are judged on the rest of the evidence.</small></div>
          </SettingsCard>
        </> : null}
        {current === "reader" ? <>
          <SettingsCard title="Panel view">
            <p className="settings-card-lead">Reads a page one panel at a time, zoomed to fit, in reading order. Turn it on from the reader&rsquo;s settings or with the P key; the choice follows your profile to any device. The rest of the page dims around the panel being read, which the same settings can turn off.{admin ? <> Flipparr finds the panels itself, and a page it gets wrong can be fixed by hand from the same settings &mdash; drawn, moved and numbered over the page &mdash; and stays as you left it.</> : null}</p>
            {admin ? <PanelFixesCard /> : null}
          </SettingsCard>
          {/* Claude and ChatGPT are filed here, not under Metadata sources:
              they contribute nothing to what a comic is, only to how a hard
              page is read. They spend the admin's key, so they are the admin's. */}
          {admin ? <><SettingsCard title="Help with hard pages" className="metadata-source-settings">
            <p className="settings-card-lead">Optional. With an API key, one of these is asked about the pages you read in panel view &mdash; each page once, sent as an image &mdash; and the page itself corrects the answer&rsquo;s edges. Nothing is sent while they are off.</p>
            {providers.filter((provider) => provider.kind === "reading").map((provider) => <Provider provider={provider} onConfigure={() => setEditingProvider(provider)} key={provider.id} />)}
            {providerError ? <p className="workbench-error" role="alert">{providerError}</p> : null}
            <Toggle checked={forReaders} onChange={savingEveryPage ? () => {} : toggleForReaders} title="Readers' pages too"
              description="On, pages other profiles open are asked about as well, on your key. Off, they read what Flipparr found itself and every page you have already had read or fixed." />
            <Toggle checked={everyPage} onChange={savingEveryPage ? () => {} : toggleEveryPage} title="Ask about every page" description="On, the model reads every page you open in panel view and its answer replaces what Flipparr found itself, which can be wrong while sure. Off, it is asked only about the pages Flipparr could not read. About half a cent a page with Sonnet." />
            <p className="settings-card-note">Measured on real pages, September 2026: Claude (Sonnet 5) read every layout tried, the hardest to within a few percent. ChatGPT (gpt-4.1-mini) is cheaper and cautious &mdash; it says &ldquo;no panels&rdquo; when unsure and merges panels rather than inventing them. Either way, Flipparr fits a model&rsquo;s boxes to the gutters it finds itself and refuses boxes that cross the art.</p>
          </SettingsCard>
          <aside className="provider-policy-note"><ShieldCheck size={19} weight="fill" /><span><strong>Your API credentials stay on this device</strong><small>Keys are hidden after saving and sent only to the service you configure.</small></span></aside></> : null}
        </> : null}
        {current === "metadata" ? <>
          <SettingsCard title="Community reading lists">
            <Toggle checked={communityLists} onChange={savingCommunityLists ? () => {} : saveCommunityLists} title="Suggest community reading lists"
              description="Story-arc suggestions include reading lists people have shared on GitHub. On, Flipparr fetches the list of them from GitHub once a day; a list itself is downloaded only when you pick it." />
          </SettingsCard>
          <SettingsCard title="Sources with an account" className="metadata-source-settings">
            <p className="settings-card-lead">Built-in sources work immediately. Add API credentials for more issue titles, dates, covers, and matches.</p>
            {providers.filter((provider) => !provider.builtIn && provider.kind !== "reading").map((provider) => <Provider provider={provider} onConfigure={() => setEditingProvider(provider)} key={provider.id} />)}
            <BuiltInSources providers={providers} />
            {providerError ? <p className="workbench-error" role="alert">{providerError}</p> : null}
          </SettingsCard>
          <aside className="provider-policy-note"><ShieldCheck size={19} weight="fill" /><span><strong>Your API credentials stay on this device</strong><small>Keys are hidden after saving and sent only to the service you configure.</small></span></aside>
        </> : null}
      </div>
    </div>
    {modals}
  </>;
}

function SecuritySettings({ onChanged, onSignOut }) {
  const [config, setConfig] = useState(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  async function load() {
    try {
      const result = await apiRequest("/api/v1/auth");
      setConfig(result);
      setUsername(result.username || "");
    } catch (err) {
      setError(err.message);
    }
  }
  useEffect(() => { load(); }, []);
  async function save(patch) {
    setBusy(true);
    setError("");
    setSaved("");
    try {
      // Once a password exists, every change here is confirmed with it: a
      // session alone (a shared device's PIN, say) does not change sign-in.
      const result = await apiRequest("/api/v1/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config?.configured ? { ...patch, currentPassword } : patch),
      });
      setConfig(result);
      setPassword("");
      setCurrentPassword("");
      setSaved("Saved");
      // Keep the shell's auth gate in step with what was just saved.
      if (onChanged) await onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  if (!config) return null;
  const on = config.method === "forms";
  return <>
    <SettingsCard title="Sign-in"
      action={on && onSignOut ? <button type="button" className="secondary-button" onClick={onSignOut}><SignOut size={17} /> Sign out</button> : null}>
    <p className="settings-card-lead">This app stores your metadata and download-client API keys and can start downloads, so require a sign-in if anything other than you can reach it.</p>
    {config.configured ? <label className="form-field"><span>Your current password</span>
      <input type="password" value={currentPassword} autoComplete="current-password" placeholder="Needed to change anything here" onChange={(event) => setCurrentPassword(event.target.value)} />
    </label> : null}
    <Toggle
      checked={on}
      onChange={(next) => next
        ? (config.configured || password
            ? save({ method: "forms", username, ...(password ? { password } : {}) })
            : setError("Set a username and password below first."))
        : save({ method: "none" })}
      title="Require sign-in"
      description="Off by default. When on, the API and library views need a session; the sign-in page itself and the container health check stay reachable."
    />
    <Toggle
      checked={Boolean(config.localBypass)}
      onChange={(next) => save({ localBypass: next })}
      title="Treat local addresses as a shared device"
      description="Off by default. On, a device on the local network is a shared one: with one profile it opens as that profile without signing in; with readers it asks who is reading, and the admin needs their PIN or password. Anything reaching this app through a tunnel, reverse proxy or container bridge arrives from a private address and would be let straight in — so turn this on only if you know the traffic is genuinely local, and set FLIPPARR_TRUSTED_PROXIES when a proxy is in front."
    />
    </SettingsCard>
    <SettingsCard title="Credentials">
    <div className="auth-credentials">
      <div className="field-group">
      <label className="form-field"><span>Username</span>
        <input value={username} autoComplete="username" onChange={(event) => setUsername(event.target.value)} />
      </label>
      <label className="form-field"><span>{config.configured ? "New password" : "Password"}</span>
        <input type="password" value={password} autoComplete="new-password" placeholder={config.configured ? "Leave blank to keep it" : "At least 8 characters"} onChange={(event) => setPassword(event.target.value)} />
      </label>
      </div>
      <button className="secondary-button" disabled={busy || !username || (!config.configured && !password) || (config.configured && !currentPassword)}
        onClick={() => save({ method: config.method, username, ...(password ? { password } : {}) })} aria-busy={busy}>
        {busy ? <LoadingSpinner size={17} /> : <ShieldCheck size={17} />} Save credentials
      </button>
      {error ? <p className="workbench-error" role="alert">{error}</p> : null}
      {saved ? <small className="auth-saved">{saved}</small> : null}
    </div>
    </SettingsCard>
    {on ? <SharedDevicesCard /> : null}
    <aside className="provider-policy-note"><ShieldCheck size={19} weight="fill" /><span><strong>Your password is stored as a scrypt hash</strong><small>It is never returned by the API, and signing in sets an HttpOnly cookie rather than exposing a token to page scripts.</small></span></aside>
  </>;
}

/**
 * Forgetting every shared device: for a tablet that is lost or given away.
 * Each needs the admin's password again before it shows the picker, and
 * every profile opened on one is signed out. This device, and a reader's
 * sign-in on their own phone, are kept.
 */
function SharedDevicesCard() {
  const [state, setState] = useState("idle");
  const [error, setError] = useState("");
  async function forget() {
    setState("busy");
    setError("");
    try {
      await apiRequest("/api/v1/devices/forget", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      setState("done");
    } catch (err) {
      setError(err.message);
      setState("idle");
    }
  }
  return <SettingsCard title="Shared devices">
    <p className="settings-card-lead">A device you sign in on with your password becomes a shared one: it asks who is reading. If one is lost or given away, forget them all. Each will need your password again, and every profile open on one is signed out. This device stays as it is, and so do readers signed in on their own phones.</p>
    <div className="settings-card-actions">
      {state === "confirm"
        ? <>
          <button type="button" className="danger-button" onClick={forget}>Forget them</button>
          <button type="button" className="secondary-button" onClick={() => setState("idle")}>Keep them</button>
        </>
        : <button type="button" className="secondary-button" onClick={() => setState("confirm")} disabled={state === "busy"} aria-busy={state === "busy"}>
          {state === "busy" ? <LoadingSpinner size={17} /> : <DeviceMobile size={17} />} Forget other shared devices
        </button>}
      {state === "done" ? <small role="status">Forgotten. Other devices need your password again.</small> : null}
    </div>
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
  </SettingsCard>;
}

// The sources that need no account, as one line after the ones that do: they
// have nothing to configure, so they sit below the real choices rather than
// as cards competing with them. Setup and Settings both use it.
function BuiltInSources({ providers }) {
  const builtIn = providers.filter((provider) => provider.builtIn);
  if (!builtIn.length) return null;
  return <p className="builtin-sources">
    <CheckCircle size={16} weight="fill" />
    <span>Already on, no account needed: {builtIn.map((provider) => provider.name).join(" and ")}.</span>
  </p>;
}

function Provider({ provider, onConfigure, concise = false }) {
  const status = provider.builtIn ? "Available without an account" : provider.enabled ? "Enabled" : provider.configured ? "Configured but disabled" : "Not configured";
  // In setup nothing is set up yet, so "Not configured" reads identically on
  // every card while the Configure button beside it already says as much. Show
  // the pill only once it reports something the button does not.
  const showState = !concise || provider.builtIn || provider.configured || provider.enabled;
  return <div className={`provider-row ${provider.enabled ? "enabled" : ""}`}><Database size={23} weight="duotone" /><span><span className="provider-title-line"><strong>{provider.name}</strong>{showState ? <b className={`provider-state ${provider.enabled ? "connected" : provider.configured ? "paused" : "optional"}`}>{status}</b> : null}</span><small>{concise ? provider.setupSummary || provider.description : provider.description}</small><em>{provider.capabilities.join(" · ")}</em></span>{provider.builtIn ? <b className="provider-priority">Priority {provider.priority}</b> : <button onClick={onConfigure}>{provider.configured ? "Manage" : "Configure"}</button>}</div>;
}

function SourceOrderList({ rows, onReorder }) {
  const byId = Object.fromEntries(rows.map((row) => [row.id, row]));
  return <DragOrderList ids={rows.map((row) => row.id)} onReorder={onReorder}
    rowClassName={(id) => (byId[id]?.ready ? "" : "source-order-row--off")}
    label={(id, index, total) => `${byId[id]?.name}, ${index + 1} of ${total}`}
    renderRow={(id, index) => {
      const source = byId[id];
      if (!source) return null;
      return <>
        <b className="source-order-position" aria-hidden="true">{index + 1}</b>
        <span><span className="provider-title-line"><strong>{source.name}</strong><b className={`provider-state ${source.ready ? "connected" : "optional"}`}>{source.ready ? "Ready" : "Not connected"}</b></span><small>{source.ready ? `Downloaded through ${source.via}.` : `Skipped until ${source.id === "direct_site" ? "the site and a page fetcher are" : `${source.via} is`} connected below.`}</small></span>
      </>;
    }} />;
}

function AcquisitionService({ service, onConfigure, concise = false }) {
  const status = service.enabled ? "Ready" : service.configured ? "Configured but disabled" : "Not connected";
  const Icon = service.id === "prowlarr" ? MagnifyingGlass : CloudArrowDown;
  const showState = !concise || service.configured || service.enabled;
  return <div className={`provider-row ${service.enabled ? "enabled" : ""}`}><Icon size={23} weight="duotone" /><span><span className="provider-title-line"><strong>{service.name}</strong>{showState ? <b className={`provider-state ${service.enabled ? "connected" : service.configured ? "paused" : "optional"}`}>{status}</b> : null}</span><small>{concise ? service.setupSummary || service.description : service.description}</small><em>{service.kind} · {service.capabilities.join(" · ")}</em></span><button onClick={onConfigure}>{service.configured ? "Manage" : "Connect"}</button></div>;
}

function AcquisitionServiceSettingsModal({ service, onClose, onSaved }) {
  const dialogRef = useDialog(onClose);
  const [url, setUrl] = useState(service.url || "");
  const [apiKey, setApiKey] = useState("");
  // qBittorrent signs in with a name and a password, both optional: a client
  // that lets its own network in needs neither.
  const signsIn = service.id === "qbittorrent";
  const [username, setUsername] = useState(service.username || "");
  const [password, setPassword] = useState("");
  const [category, setCategory] = useState(service.category || "comics");
  const [enabled, setEnabled] = useState(service.enabled || !service.configured);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const hasCategory = service.id === "sabnzbd" || signsIn;
  const payload = () => ({
    url, ...(apiKey ? { apiKey } : {}), ...(hasCategory ? { category } : {}),
    ...(signsIn ? { username, ...(password ? { password } : {}) } : {}),
  });
  const needsKey = !signsIn && !apiKey && !service.configured;
  async function test() {
    setBusy("test"); setError(""); setResult("");
    try {
      const response = await apiRequest(`/api/v1/acquisition-services/${service.id}/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload()) });
      setResult(response.detail);
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(""); }
  }
  async function save(event) {
    event.preventDefault(); setBusy("save"); setError("");
    try {
      await apiRequest(`/api/v1/acquisition-services/${service.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...payload(), enabled }) });
      await onSaved();
    } catch (requestError) { setError(requestError.message); setBusy(""); }
  }
  async function disconnect() {
    setBusy("remove"); setError("");
    try {
      await apiRequest(`/api/v1/acquisition-services/${service.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ clearCredentials: true, enabled: false }) });
      await onSaved();
    } catch (requestError) { setError(requestError.message); setBusy(""); }
  }
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal provider-settings-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="acquisition-service-title" onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close acquisition service settings" /><span className="eyebrow">{service.kind}</span><h2 id="acquisition-service-title">Connect {service.name}</h2><p className="workbench-intro">{service.description}</p><form onSubmit={save}><label className="form-field"><span>Server URL</span><input value={url} onChange={(event) => setUrl(event.target.value)} placeholder={service.id === "prowlarr" ? "http://nas:9696" : "http://nas:8080"} required /></label>{signsIn ? <><label className="form-field"><span>Username</span><input autoComplete="off" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="Leave empty if it lets your network in" /></label><label className="form-field"><span>Password</span><input type="password" autoComplete="off" value={password} onChange={(event) => setPassword(event.target.value)} placeholder={service.credentialHint ? "Saved · type to replace" : "Optional"} /></label></> : <label className="form-field"><span>API key</span><input type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={service.configured ? "Saved · type to replace" : `Enter your ${service.name} API key…`} /></label>}{hasCategory ? <label className="form-field"><span>{service.name} category</span><input value={category} onChange={(event) => setCategory(event.target.value)} placeholder="comics" required /><small>{signsIn ? "Torrents are saved under this category. Its folder is where Flipparr reads finished torrents from, and it has to be named the same." : "Flipparr will use this category to identify and monitor its downloads."}</small></label> : null}<Toggle checked={enabled} onChange={setEnabled} title={`Use ${service.name}`} description={service.id === "prowlarr" ? "Search configured Usenet and torrent indexers for wanted comics." : signsIn ? "Send chosen torrents to qBittorrent, fetch only the wanted issues of a pack, and import them while they seed." : "Send selected NZBs to SABnzbd and monitor their progress."} />{result ? <p className="provider-test-result"><CheckCircle size={17} weight="fill" /> {result}</p> : null}{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="provider-modal-actions"><button type="button" className="secondary-button" onClick={test} disabled={Boolean(busy) || !url.trim() || needsKey}>{busy === "test" ? <><LoadingSpinner size={17} /> Testing…</> : "Test"}</button><span />{service.configured ? <button type="button" className="danger-button" onClick={disconnect} disabled={Boolean(busy)}>Disconnect</button> : null}<button className="primary-button" disabled={Boolean(busy) || !url.trim() || needsKey}>{busy === "save" ? <><LoadingSpinner size={17} /> Saving…</> : "Save"}</button></div><small className="provider-credential-help">{signsIn ? "The password is stored locally and is never returned to the browser after saving." : "The API key is stored locally and is never returned to the browser after saving."}</small></form></section></div>;
}

function ProviderSettingsModal({ provider, onClose, onSaved }) {
  // Claude and ChatGPT read pages for panel view; they fill no metadata.
  const reading = provider.kind === "reading";
  const dialogRef = useDialog(onClose);
  const field = provider.credentialField;
  const [credential, setCredential] = useState("");
  const [enabled, setEnabled] = useState(provider.enabled || !provider.configured);
  const [priority, setPriority] = useState(provider.priority || 20);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const label = provider.id === "metron" ? "API token" : "API key";
  async function test() {
    setBusy("test"); setError(""); setResult("");
    try {
      const payload = credential ? { [field]: credential } : {};
      const response = await apiRequest(`/api/v1/providers/${provider.id}/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      setResult(response.detail);
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(""); }
  }
  async function save(event) {
    event.preventDefault();
    setBusy("save"); setError("");
    try {
      const payload = { enabled, priority, ...(credential ? { [field]: credential } : {}) };
      await apiRequest(`/api/v1/providers/${provider.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      await onSaved();
    } catch (requestError) { setError(requestError.message); setBusy(""); }
  }
  async function removeCredentials() {
    setBusy("remove"); setError("");
    try {
      await apiRequest(`/api/v1/providers/${provider.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ clearCredentials: true, enabled: false, priority }) });
      await onSaved();
    } catch (requestError) { setError(requestError.message); setBusy(""); }
  }
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal provider-settings-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="provider-settings-title" onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close provider settings" /><span className="eyebrow">{reading ? "Page reading" : "Metadata provider"}</span><h2 id="provider-settings-title">Connect {provider.name}</h2><p className="workbench-intro">{provider.description}</p><form onSubmit={save}><label className="form-field"><span>{label}</span><input type="password" autoComplete="off" value={credential} onChange={(event) => setCredential(event.target.value)} placeholder={provider.configured ? "Saved · type to replace" : `Enter your ${provider.name} ${label.toLowerCase()}…`} /></label><small className="provider-credential-help">{provider.credentialHelp || "Your key is stored locally and is never returned to the browser after saving."}{provider.credentialUrl ? <> <a className="provider-credential-link" href={provider.credentialUrl} target="_blank" rel="noreferrer noopener">Get a key<ArrowUpRight size={13} weight="bold" /></a></> : null}</small>{reading
            ? <Toggle checked={enabled} onChange={setEnabled} title={`Use ${provider.name} to read pages`} description={`Panel view asks ${provider.name} about the pages it reads, as set in Settings → Reader. Each page is sent once, as an image.`} />
            : <Toggle checked={enabled} onChange={setEnabled} title={`Use ${provider.name} for enrichment`} description="Fill missing fields automatically while preserving locked local corrections and higher-priority source data." />}<div className="form-field provider-priority-field"><span>{reading ? "When both are connected" : "Provider priority"}</span><GlassSelect label={reading ? "When both are connected" : "Provider priority"} value={priority} onChange={(next) => setPriority(Number(next))} options={reading
            ? [{ value: 15, label: "Ask this one first" }, { value: 20, label: "Normal order" }, { value: 30, label: "Ask the other one first" }]
            : [{ value: 15, label: "Before other optional providers" }, { value: 20, label: "Normal priority" }, { value: 30, label: "Fallback priority" }]} /><small>{reading
            ? "Only one connector reads a page: with Claude and ChatGPT both on, the one asked first is the one used."
            : "Built-in GCD structure remains first. Optional providers fill fields that are still missing."}</small></div>{result ? <p className="provider-test-result"><CheckCircle size={17} weight="fill" /> {result}</p> : null}{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="provider-modal-actions"><button type="button" className="secondary-button" onClick={test} disabled={Boolean(busy) || (!credential && !provider.configured)}>{busy === "test" ? <><LoadingSpinner size={17} /> Testing…</> : "Test"}</button><span />{provider.configured ? <button type="button" className="danger-button" onClick={removeCredentials} disabled={Boolean(busy)}>Remove</button> : null}<button className="primary-button" disabled={Boolean(busy) || (!credential && !provider.configured)}>{busy === "save" ? <><LoadingSpinner size={17} /> Saving…</> : "Save"}</button></div></form></section></div>;
}

function SeriesMergeWorkbench({ data, busy, error, onClose, onTargetChange, onConfirm }) {
  const dialogRef = useDialog(onClose);
  const [allowProviderConflicts, setAllowProviderConflicts] = useState(false);
  const preview = data.preview;
  useEffect(() => setAllowProviderConflicts(false), [data.targetId]);
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal series-merge-workbench" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="series-merge-title" onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close duplicate run recovery" /><span className="eyebrow">Combine duplicate run</span><h2 id="series-merge-title">Repair {data.source.title}</h2><p className="workbench-intro">Choose the canonical run to keep. Flipparr will move catalog relationships, issue and volume coverage, provider evidence, wanted items, and download history. Comic files are not renamed or moved.</p><label className="form-field"><span>Run to keep</span><GlassSelect label="Run to keep" placeholder="Choose the correct run…" value={data.targetId || ""} onChange={onTargetChange} className="glass-select--fill" options={data.candidates.map((candidate) => ({ value: candidate.id, label: `${candidate.title}${candidate.year ? ` (${candidate.year})` : ""} · ${candidate.publisher || "Publisher unknown"}` }))} /></label>{busy ? <div className="merge-loading"><LoadingSpinner size={28} label="Checking both runs" /><span>Checking files, issues, volumes, downloads, and provider identities…</span></div> : null}{preview && !busy ? <><div className="merge-direction"><article><small>Combine</small><strong>{preview.source.title}</strong><span>{preview.source.year || "Year unknown"} · {preview.source.counts.files} files</span></article><ArrowRight size={22} /><article className="keep"><small>Keep</small><strong>{preview.target.title}</strong><span>{preview.target.year || "Year unknown"} · {preview.target.counts.files} files</span></article></div><section className="merge-impact"><strong>After combining</strong><span>{preview.source.counts.files + preview.target.counts.files} comic files</span><span>{preview.source.counts.issues + preview.target.counts.issues} issue records before duplicate numbers are collapsed</span><span>{preview.source.counts.volumes + preview.target.counts.volumes} volumes</span></section>{preview.blockers?.length ? <div className="merge-warning blocked"><WarningCircle size={21} weight="fill" /><span><strong>These runs cannot be combined yet</strong>{preview.blockers.map((blocker) => <small key={blocker}>{blocker}</small>)}</span></div> : null}{preview.providerConflicts?.length ? <div className="merge-warning"><WarningCircle size={21} weight="fill" /><span><strong>Provider identities disagree</strong><small>This can indicate a real reboot or an incorrect match. Confirm only if these entries represent the same publication run.</small>{preview.providerConflicts.map((conflict) => <small key={conflict.provider}>{conflict.provider}: {conflict.sourceId} → {conflict.targetId}</small>)}<label><input type="checkbox" checked={allowProviderConflicts} onChange={(event) => setAllowProviderConflicts(event.target.checked)} /> I reviewed these provider IDs and want to combine the runs</label></span></div> : null}<div className="metadata-edit-actions"><button className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || preview.blockers?.length || (preview.providerConflicts?.length && !allowProviderConflicts)} onClick={() => onConfirm(allowProviderConflicts)}><ShieldCheck size={18} /> Combine runs</button></div></> : null}{error ? <p className="workbench-error" role="alert">{error}</p> : null}</section></div>;
}

function StoryArcList({ arcs, emptyTitle, onOpenSeries }) {
  if (!arcs.length) return <div className="drawer-empty"><Books size={27} weight="duotone" /><strong>{emptyTitle}</strong><span>Runs are discovered automatically. Manual grouping is available under Advanced tools.</span></div>;
  return <div className="story-arc-list">{arcs.map((arc) => <article key={arc.id}><header><div><span>{arc.type === "specials" ? "Specials / one-shots" : "Series run"}</span><strong>{arc.name}</strong></div><b className={arc.status}>{arc.status === "complete" ? "Complete" : arc.status === "partial" ? "Partially owned" : arc.status === "cataloged" ? "Not owned" : "Missing"}</b></header><div className="arc-progress-copy"><strong>{arc.ownedIssueCount} of {arc.issueCount || "unknown"} issues owned</strong><span>{arc.volumeCount} volume{arc.volumeCount === 1 ? "" : "s"} · {arc.fileCount} file{arc.fileCount === 1 ? "" : "s"}</span></div><div className="arc-run-links">{arc.runs.map((run) => <button onClick={() => onOpenSeries(run)} key={run.id}><span>{run.title} ({run.year})</span><ArrowRight size={15} /></button>)}</div></article>)}</div>;
}

function CollectionDrawer({ collection, tab, onTabChange, onClose, onFindStructure, onOpenSeries, onOpenContents, onRequest, onViewRequests, requestBusy, onEditIssue, onUnfollow, unfollowBusy = false, requested = false }) {
  const viewer = useViewer();
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  useSwipeToDismiss(dialogRef, requestClose);
  const toned = toneProps(useArtTone(collection?.cover || null));
  const [tabsRef, tabGlass] = useGlassIndicator("button.active", [tab, collection?.id]);
  if (!collection) return null;
  const arcs = collection.storyArcs || [];
  const files = collection.runs.flatMap((run) => (run.fileDetails || []).map((file) => ({ ...file, run })));
  const arcByRunId = new Map(arcs.flatMap((arc) => (arc.runIds || []).map((runId) => [String(runId), arc])));
  const volumes = collection.runs.flatMap((run) => (run.editions || []).map((volume) => {
    const storyGroup = arcByRunId.get(String(run.id));
    const hasResolvedCoverage = (volume.coverageGroups || []).some((group) => group.resolved);
    const hasUnresolvedCoverage = (volume.coverageGroups || []).some((group) => !group.resolved);
    const linkedFile = files.find((file) => (volume.files || []).includes(file.filename));
    return {
      ...volume,
      run,
      linkedFile,
      storyGroup,
      coverageStatus: volume.contentsStatus || (hasResolvedCoverage && hasUnresolvedCoverage ? "partial" : hasResolvedCoverage ? "verified" : hasUnresolvedCoverage ? "unresolved" : "unknown"),
    };
  }));
  const unverifiedVolumes = volumes.filter((volume) => volume.coverageStatus !== "verified");
  const unverifiedVolumeFileCount = unverifiedVolumes.reduce(
    (count, volume) => count + (volume.copyCount || 1), 0,
  );
  const issueCoveragePending = unverifiedVolumes.length > 0;
  const displayArcs = arcs.map((arc) => {
    const runIds = new Set((arc.runIds || []).map(String));
    const arcVolumes = volumes.filter((volume) => runIds.has(String(volume.run.id)));
    const linkedFileNames = new Set(arcVolumes.flatMap((volume) => volume.files || []));
    const directFiles = files.filter((file) => runIds.has(String(file.run.id)) && file.identityKind === "issue");
    return { ...arc, volumeCount: arcVolumes.length, fileCount: linkedFileNames.size + directFiles.length };
  });
  const mainArcs = displayArcs.filter((arc) => arc.type === "main");
  const specials = displayArcs.filter((arc) => arc.type === "specials");
  const issues = collection.runs.flatMap((run) => {
    const storyGroup = arcByRunId.get(String(run.id));
    return (run.issues || []).map((issue) => ({
      ...issue,
      contextRunId: run.id,
      contextLabel: run.title,
      contextYear: run.year,
      contextGroupName: storyGroup?.name || run.title,
      contextType: storyGroup?.type || "main",
    }));
  });
  const ownedIssueCount = issues.filter((issue) => issue.ownership !== "unowned").length;
  const missingIssueCount = Math.max(0, issues.length - ownedIssueCount);
  const coverage = {
    ...collection,
    owned: ownedIssueCount,
    total: issues.length,
    unowned: issueCoveragePending ? 0 : missingIssueCount,
    catalogKnown: issues.length > 0 && !issueCoveragePending,
    status: issueCoveragePending ? "cataloged" : issues.length ? (missingIssueCount ? "partial" : "complete") : collection.status,
    ownership: issueCoveragePending ? "Volume issue coverage is still being identified" : issues.length ? (missingIssueCount ? `${missingIssueCount} issues missing` : "All issues owned") : collection.ownership,
  };
  const display = { ...collection, id: `collection-${collection.id}`, title: collection.name };
  const preferenceLabel = ACQUISITION_LABELS[collection.acquisitionPreference] || ACQUISITION_LABELS.either;
  // The comic drawers' frame (2026-10-01): Follow as the top bar's eye, the
  // ownership bar in the header, the glass tab bar.
  const following = collection.monitoringStatus === "monitored";
  const followAction = isAdmin(viewer)
    ? <button type="button" className={`glass-button glass-button--icon comic-drawer-follow-button${following ? " active" : ""}`}
      onClick={() => (following ? onUnfollow(collection) : onRequest())} disabled={requestBusy || unfollowBusy} aria-pressed={following}
      aria-label={following ? `Stop following ${collection.name}` : `Follow ${collection.name}`} title={following ? "Following" : "Follow collection"}>
      {requestBusy || unfollowBusy ? <LoadingSpinner size={18} /> : <FollowedIcon size={20} />}
      <b>{following ? "Unfollow" : "Follow"}</b>
    </button>
    : !following ? <button type="button" className={`glass-button glass-button--icon comic-drawer-follow-button${requested ? " active" : ""}`}
      onClick={() => onRequest()} disabled={requestBusy || requested}
      aria-label={requested ? `Following ${collection.name} is requested` : `Request a follow of ${collection.name}`}
      title={requested ? "Waiting for approval" : "Ask for this to be followed"}>
      {requestBusy ? <LoadingSpinner size={18} /> : requested ? <Hourglass size={20} /> : <FollowedIcon size={20} />}
      <b>{requested ? "Follow requested" : "Request follow"}</b>
    </button> : null;
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer comic-drawer collection-drawer${toned.className} ${closing ? "closing" : ""}`} style={toned.style} ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="collection-drawer-title" onMouseDown={(event) => event.stopPropagation()}>
      <DiscoverDrawerHero art={collection.cover} titleId="collection-drawer-title" title={collection.name}
        cover={<SeriesCover series={display} />}
        byline={[collection.publisher, collection.year, `${mainArcs.length} runs${specials.length ? ` + ${specials.length} specials` : ""}`].filter(Boolean).join(" • ")}
        onClose={requestClose} closeLabel="Close collection details" actions={followAction}
        ownership={<Ownership series={coverage} compact />}>
        <MonitoringStatus series={collection} />
        {collection.structureStatus === "unmapped" || issueCoveragePending ? <StatusBadge tone="amber">Contents being identified</StatusBadge> : null}
      </DiscoverDrawerHero>
      {/* A tab with nothing in it is not offered: six did not fit a phone. */}
      <DrawerTabs tabs={[["overview", "Overview"], ["issues", `Issues (${issues.length})`], ...(volumes.length ? [["volumes", `Volumes (${volumes.length})`]] : []),
        ...(specials.length ? [["specials", `Specials (${specials.length})`]] : []), ["arcs", `Runs (${mainArcs.length})`], ["files", `Files (${files.length})`]]}
        tab={tab} onTab={onTabChange} label="Collection details" tabsRef={tabsRef} glass={tabGlass} />
      <div className="comic-drawer-body" key={tab}>
        {tab === "overview" ? <><div className="drawer-facts collection-coverage-facts">{issueCoveragePending ? <><span><strong>{volumes.length}</strong>Volumes identified</span><span><strong>{unverifiedVolumes.length}</strong>Need issue ranges</span><span><strong>{ownedIssueCount}</strong>Issues confirmed</span><span><strong>{files.length}</strong>Comic files</span></> : <><span><strong>{ownedIssueCount} <em>of {issues.length}</em></strong>Issues confirmed</span><span><strong>{missingIssueCount}</strong>Issues missing</span><span><strong>{volumes.length}</strong>Volumes owned</span><span><strong>{files.length}</strong>Comic files</span></>}</div><section className="monitoring-summary"><div><strong>{preferenceLabel}</strong><small>{collection.includeSpecials === false ? "Main series only" : "Main series + specials"}</small></div><span><CheckCircle size={18} weight="fill" /> Series connections updated automatically</span></section>{unverifiedVolumes.length ? <section className="placement-callout"><WarningCircle size={21} weight="fill" /><div><strong>Issue contents are still being identified</strong><small>All {unverifiedVolumes.length} logical volume{unverifiedVolumes.length === 1 ? " is" : "s are"} already matched to this series across {unverifiedVolumeFileCount} comic file{unverifiedVolumeFileCount === 1 ? "" : "s"}. Flipparr will keep looking for the issue ranges in the background.</small></div><button onClick={() => onTabChange("volumes")}>View volumes</button></section> : null}{collection.structureStatus === "unmapped" ? <section className="structure-callout"><div><strong>Complete-series details are still being found</strong><small>Flipparr will keep your current files safe while it looks for a confident series structure.</small></div><button onClick={() => onFindStructure(collection)}><MagnifyingGlass size={17} /> Review details</button></section> : null}<details className="advanced-collection-tools"><summary><Gear size={16} /> Advanced tools</summary><p>Inspect provider runs, split or combine groups, and correct unusual series structures.</p><button onClick={() => onFindStructure(collection)}><PencilSimple size={16} /> Review series structure</button></details></> : null}
        {tab === "issues" ? <GroupedIssueInventory issues={issues} onEditIssue={onEditIssue} /> : null}
        {tab === "arcs" ? <StoryArcList arcs={mainArcs} emptyTitle="No runs found" onOpenSeries={onOpenSeries} /> : null}
        {tab === "specials" ? <StoryArcList arcs={specials} emptyTitle="No specials found" onOpenSeries={onOpenSeries} /> : null}
        {tab === "volumes" ? <div className="collection-volume-list">{volumes.length ? volumes.map((volume) => { const title = volume.subtitle ? `${volume.title}: ${volume.subtitle}` : volume.title; const needsContents = volume.coverageStatus !== "verified"; const canReviewContents = needsContents && volume.linkedFile; return <button className={needsContents ? "unplaced" : "placed"} onClick={() => canReviewContents ? onOpenContents(volume.linkedFile) : onOpenSeries(volume.run)} key={`${volume.run.id}-${volume.logicalVolumeKey || volume.id}`}><span className="collection-volume-cover"><CoverArt id={`collection-volume-${volume.id}`} title={title} cover={volume.cover} decorative placeholderSize={17} /></span><span><strong>{title}</strong><small>{volume.run.title} · {editionKindLabel(volume.editionKind)}{volume.volume ? ` · Vol. ${volume.volume}` : ""}{volume.copyCount > 1 ? ` · ${volume.copyCount} files` : ""}</small>{needsContents ? <b>{volume.coverageStatus === "partial" ? `${volume.contentsIssueCount || 0} issues confirmed · more may be included` : "Issue contents not confirmed"}</b> : <b>{volume.contentsIssueCount || 0} issues confirmed</b>}</span><ArrowRight size={16} /></button>; }) : <div className="drawer-empty"><Books size={27} /><strong>No volumes cataloged</strong></div>}</div> : null}
        {tab === "files" ? <div className="collection-volume-list">{files.map((file) => <button onClick={() => onOpenSeries(file.run)} key={file.id}><HardDrive size={20} weight="duotone" /><span><strong>{file.filename}</strong><small>{file.run.title} · {file.identityKind === "issue" ? "Single issue" : editionKindLabel(file.editionKind)}</small></span><ArrowRight size={16} /></button>)}</div> : null}
      </div>
      {tab === "overview" && isAdmin(viewer) ? <div className="drawer-actions collection-drawer-links">{issueCoveragePending ? <button className="ghost-button" onClick={() => onTabChange("volumes")}><Books size={18} /> Review volume contents</button> : null}{collection.monitoringStatus === "monitored" && !issueCoveragePending && missingIssueCount ? <button className="ghost-button" onClick={onViewRequests}><CheckCircle size={18} weight="fill" /> View {missingIssueCount} wanted issue{missingIssueCount === 1 ? "" : "s"}</button> : null}<button className="ghost-button" onClick={() => onTabChange("files")}><Eye size={18} /> View files</button></div> : null}
    </aside>
  </div>;
}

function StoryStructureWorkbench({ data, busy, error, onClose, onSave }) {
  const dialogRef = useDialog(onClose);
  const [arcs, setArcs] = useState(() => (data.arcs || []).map((arc) => ({ ...arc, runIds: [...arc.runIds], runLabels: [...arc.runLabels] })));
  function update(index, fields) { setArcs((items) => items.map((arc, position) => position === index ? { ...arc, ...fields } : arc)); }
  function move(index, direction) { setArcs((items) => { const target = index + direction; if (target < 0 || target >= items.length) return items; const next = [...items]; [next[index], next[target]] = [next[target], next[index]]; return next; }); }
  function addGroup() { setArcs((items) => [...items, { id: null, name: "New story group", type: "main", runIds: [], runLabels: [], issueCount: 0, volumeCount: 0, confidence: "manual", reason: "Created for manual organization" }]); }
  function moveRun(fromIndex, runIndex, toIndex) { setArcs((items) => { const next = items.map((arc) => ({ ...arc, runIds: [...arc.runIds], runLabels: [...arc.runLabels] })); const [runId] = next[fromIndex].runIds.splice(runIndex, 1); const [runLabel] = next[fromIndex].runLabels.splice(runIndex, 1); next[toIndex].runIds.push(runId); next[toIndex].runLabels.push(runLabel); return next; }); }
  function submit(event) { event.preventDefault(); onSave(arcs.filter((arc) => arc.runIds.length).map((arc) => ({ name: arc.name.trim(), type: arc.type, runIds: arc.runIds }))); }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal structure-workbench" role="dialog" aria-modal="true" aria-labelledby="structure-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close series structure" /><span className="eyebrow">Find series structure</span><h2 id="structure-workbench-title">{data.collection.name}</h2><p className="workbench-intro">Review the proposed hierarchy before saving. Runs can be grouped into one story arc or split into separate arcs and specials; files and metadata are not changed.</p><form onSubmit={submit}><div className="structure-groups">{arcs.map((arc, index) => <article className={!arc.runIds.length ? "empty" : ""} key={`${arc.id || "new"}-${index}`}><header><span>{index + 1}</span><input value={arc.name} onChange={(event) => update(index, { name: event.target.value })} aria-label={`Story group ${index + 1} name`} /><GlassSelect label={`Story group ${index + 1} kind`} value={arc.type} onChange={(next) => update(index, { type: next })} className="glass-select--compact" options={[{ value: "main", label: "Story arc" }, { value: "specials", label: "Specials / one-shots" }]} /><button type="button" onClick={() => move(index, -1)} disabled={index === 0} aria-label="Move group up">↑</button><button type="button" onClick={() => move(index, 1)} disabled={index === arcs.length - 1} aria-label="Move group down">↓</button></header><small>{arc.reason} · {arc.confidence} confidence</small><div className="structure-runs">{arc.runIds.length ? arc.runIds.map((runId, runIndex) => <div key={runId}><span>{arc.runLabels[runIndex]}</span>{arcs.length > 1 ? <GlassSelect label={`Move ${arc.runLabels[runIndex]} to another group`} value={index} onChange={(next) => moveRun(index, runIndex, Number(next))} className="glass-select--compact" options={arcs.map((target, targetIndex) => ({ value: targetIndex, label: `Move to ${target.name || `group ${targetIndex + 1}`}` }))} /> : null}</div>) : <em>Empty group — move a run here or it will not be saved.</em>}</div></article>)}</div><button type="button" className="ghost-button add-structure-group" onClick={addGroup}><Plus size={17} /> Add story group</button>{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="metadata-edit-actions"><button type="button" className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || !arcs.some((arc) => arc.runIds.length)} aria-busy={busy}>{busy ? <LoadingSpinner size={18} /> : <ShieldCheck size={18} />} Save series structure</button></div></form></section></div>;
}

function candidateValue(value) {
  if (value === null || value === undefined || value === "") return "Not set";
  if (value === "issue") return "Single issue";
  if (value === "edition") return "Volume";
  return String(value);
}

function MatchCandidateCard({ candidate, current, selectedKey, busy, onMatch }) {
  const preview = candidate.selectionPreview || { changes: [], associations: [], fields: {} };
  // Identical visible fields do not prove that this provider identity was selected.
  // Fix Match results remain actionable until their retained candidate key is applied.
  const isSelected = Boolean(selectedKey) && selectedKey === candidate.key;
  const edition = candidate.matched_edition || {};
  const creators = candidate.creators || [];
  const rank = candidate.identity_confidence?.score ?? candidate.match_score;
  const facts = [
    ["Record", candidateValue(preview.fields.recordType)],
    ["Publisher", candidate.publisher || "Not supplied"],
    ["Year", candidate.publication_year || candidate.published_date || "Not supplied"],
    ["Format", candidate.format || "Not supplied"],
    ["ISBN", (candidate.isbns || []).join(", ") || "Not supplied"],
    ["Creators", creators.join(", ") || "Not supplied"],
    ["Pages", edition.number_of_pages || candidate.page_count || "Not supplied"],
    ["Cover", preview.coverEffect],
  ];
  return <article className={`match-candidate-card ${isSelected ? "selected" : ""}`}><div className="candidate-cover">{candidate.cover ? <img src={candidate.cover} alt={`${candidate.title || "Candidate"} cover`} /> : <span><Books size={28} weight="duotone" />No cover</span>}</div><div className="candidate-body"><header><div><span>{candidate.source ? <ProviderCredit source={candidate.source} url={candidate.url} /> : "Catalog candidate"}</span><h3>{candidate.title || "Untitled candidate"}</h3>{candidate.subtitle ? <strong>{candidate.subtitle}</strong> : null}</div><div className="candidate-badges">{isSelected ? <b className="selected-badge"><CheckCircle size={14} weight="fill" /> Current</b> : null}{rank != null ? <b>{candidate.identity_confidence ? `Identity ${rank}%` : `Rank ${rank}`}</b> : null}{candidate.cover ? <b>Cover</b> : null}</div></header>{candidate.verification_status ? <p className="candidate-verification">{volumeTerminology(candidate.verification_status)}</p> : null}<dl className="candidate-facts">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><section className="candidate-associations"><h4>Issue association</h4>{preview.associations.length ? preview.associations.map((association, index) => <div key={`${association.series}-${association.issueLabel}-${index}`}><strong>{association.series} #{association.issueLabel}</strong><small>{association.source || "Catalog evidence"} · {association.confidence}</small></div>) : <p>No issue or volume coverage association supplied by this match.</p>}</section><section className="candidate-impact"><h4>What selecting this will change</h4>{preview.changes.length ? <div>{preview.changes.map((change) => <span key={change.field}><b>{change.label}</b><del>{candidateValue(change.from)}</del><ArrowRight size={13} /><ins>{candidateValue(change.to)}</ins></span>)}</div> : <p>This candidate matches the currently applied fields.</p>}<small>{candidate.cover ? "Its provider image will also become available in the Cover picker; automatic cover priority will not be changed." : "No provider cover will be added by this candidate."}</small></section>{candidate.match_reasons?.length || candidate.description ? <details className="candidate-evidence"><summary>Evidence and description</summary>{candidate.match_reasons?.length ? <ul>{candidate.match_reasons.map((reason) => <li key={reason}>{volumeTerminology(reason)}</li>)}</ul> : null}{candidate.description ? <p>{candidate.description}</p> : null}</details> : null}<button className="candidate-select" disabled={busy || isSelected} onClick={() => onMatch(candidate.key)}>{isSelected ? "Currently selected" : `Use this match · ${preview.changes.length} change${preview.changes.length === 1 ? "" : "s"}`}</button></div></article>;
}

function MetadataWorkbench({ data, mode, busy, error, onClose, onSave, onMatch, onSearch, onReset }) {
  const dialogRef = useDialog(onClose);
  const [fields, setFields] = useState(() => ({ ...data.current, ...data.override }));
  const [matchQuery, setMatchQuery] = useState(data.suggestedSearchQuery || data.current?.seriesTitle || data.current?.title || "");
  const set = (key) => (event) => setFields({ ...fields, [key]: event.target.value });
  const isIssue = fields.recordType === "issue";
  function submit(event) {
    event.preventDefault();
    const lockedFields = Object.keys(fields).filter((key) => fields[key] !== "" && fields[key] != null);
    onSave(fields, lockedFields);
  }
  function searchMatches(event) {
    event.preventDefault();
    const query = matchQuery.trim();
    if (query) onSearch(query);
  }
  const searchInfo = data.candidateSearch || {};
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className={`modal metadata-workbench ${mode === "match" ? "match-workbench" : ""}`} role="dialog" aria-modal="true" aria-labelledby="metadata-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close metadata workbench" /><span className="eyebrow">{mode === "match" ? "Fix match" : "Edit metadata"}</span><h2 id="metadata-workbench-title">{data.file.filename}</h2>{mode === "match" ? <><p className="workbench-intro">Search your enabled metadata services, then compare the complete metadata and ownership impact before selecting a match.</p><form className="match-search-form" onSubmit={searchMatches}><label htmlFor="fix-match-query"><span>Search metadata services</span><small>Starts with your latest saved title and year. You can broaden or replace the query.</small></label><div><input id="fix-match-query" value={matchQuery} onChange={(event) => setMatchQuery(event.target.value)} placeholder="Series, volume, year, or ISBN…" autoComplete="off" /><button type="submit" className="primary-button" disabled={busy || matchQuery.trim().length < 2}>{busy ? <LoadingSpinner size={18} /> : <MagnifyingGlass size={18} />}{busy ? "Searching…" : "Search"}</button></div>{searchInfo.query ? <p className="match-search-summary"><strong>{searchInfo.resultCount || 0} result{searchInfo.resultCount === 1 ? "" : "s"}</strong> for “{searchInfo.query}” · {(searchInfo.providersChecked || []).join(", ") || "metadata services"}{searchInfo.errors?.length ? <span>{searchInfo.errors.length} service{searchInfo.errors.length === 1 ? "" : "s"} could not respond</span> : null}</p> : null}</form><div className="match-candidates">{data.candidates.length ? data.candidates.map((candidate) => <MatchCandidateCard candidate={candidate} current={data.current} selectedKey={data.selectedCandidateKey} busy={busy} onMatch={onMatch} key={candidate.key} />) : <div className="drawer-empty"><MagnifyingGlass size={26} /><strong>{searchInfo.query ? `No matches found for “${searchInfo.query}”` : "No alternate candidates were retained"}</strong><span>Try a cleaner series title, publication year, volume number, or ISBN. Saved metadata corrections automatically become the next suggested search.</span></div>}</div></> : <form className="metadata-edit-form" onSubmit={submit}><p className="workbench-intro">Saved values are stored locally and locked against future provider refreshes. Correcting the series also heals other files connected to the same local run; volume-specific details stay separate. The comic file itself is not modified.</p><label><span>Series</span><input value={fields.seriesTitle || ""} onChange={set("seriesTitle")} required /></label><label><span>Display title</span><input value={fields.title || ""} onChange={set("title")} required /></label><label><span>Subtitle</span><input value={fields.subtitle || ""} onChange={set("subtitle")} /></label><label><span>Record type</span><GlassSelect label="Record type" value={fields.recordType || "edition"} onChange={(next) => setFields({ ...fields, recordType: next })} className="glass-select--fill" options={[{ value: "issue", label: "Single issue" }, { value: "edition", label: "Volume" }]} /></label>{isIssue ? <label><span>Issue number</span><input value={fields.issueNumber || ""} onChange={set("issueNumber")} /></label> : <><label><span>Volume number</span><input type="number" min="0" value={fields.volumeNumber ?? ""} onChange={set("volumeNumber")} /></label><label><span>Volume type</span><GlassSelect label="Volume type" value={fields.editionKind || "edition"} onChange={(next) => setFields({ ...fields, editionKind: next })} className="glass-select--fill" options={Object.entries(EDITION_KIND_LABELS).map(([value, label]) => ({ value, label }))} /></label></>}<label><span>Publisher</span><input value={fields.publisher || ""} onChange={set("publisher")} /></label><label><span>Publication year</span><input type="number" min="1800" max="2200" value={fields.publicationYear ?? ""} onChange={set("publicationYear")} /></label><label><span>ISBN / GTIN</span><input value={fields.isbn || ""} onChange={set("isbn")} /></label><label><span>Format</span><input value={fields.format || ""} onChange={set("format")} /></label><div className="metadata-edit-actions">{Object.keys(data.override).length ? <button type="button" className="danger-button" onClick={onReset} disabled={busy}>Restore provider metadata</button> : <span />}<button className="primary-button" disabled={busy} aria-busy={busy}>{busy ? <LoadingSpinner size={18} /> : <ShieldCheck size={18} />} Save and lock</button></div></form>}{error ? <p className="workbench-error" role="alert">{error}</p> : null}</section></div>;
}

function IssueMetadataWorkbench({ issue, busy, error, onClose, onSave, onReset }) {
  const dialogRef = useDialog(onClose);
  const [title, setTitle] = useState(issue.title || "");
  const [publicationYear, setPublicationYear] = useState(issue.publicationYear || "");
  const providerTitle = issue.providerTitle || "Not supplied by provider";
  const providerYear = issue.providerPublicationYear || "Not supplied by provider";
  function submit(event) {
    event.preventDefault();
    onSave({ title: title.trim(), publicationYear: publicationYear || null });
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal issue-metadata-workbench" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="issue-metadata-title" onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close issue metadata editor" /><span className="eyebrow">Edit issue metadata</span><h2 id="issue-metadata-title">{issue.contextLabel || "Issue"} #{issue.number}</h2><p className="workbench-intro">Correct the catalog after ingestion without modifying the comic file. Saved values remain locked when provider metadata is refreshed.</p><form className="issue-metadata-form" onSubmit={submit}><label className="form-field"><span>Issue title</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder={`Issue ${issue.number} title…`} /></label><label className="form-field"><span>Publication year</span><input type="number" min="1800" max="2200" value={publicationYear} onChange={(event) => setPublicationYear(event.target.value)} /></label><section className="provider-issue-evidence"><header><Database size={18} /><span><strong>Provider metadata retained</strong><small>You can restore these values at any time.</small></span></header><dl><div><dt>Title</dt><dd>{providerTitle}</dd></div><div><dt>Year</dt><dd>{providerYear}</dd></div></dl></section>{issue.metadataLocked ? <p className="issue-lock-note"><ShieldCheck size={17} weight="fill" /> A local correction is currently locked for this issue.</p> : null}{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="metadata-edit-actions">{issue.metadataLocked ? <button type="button" className="danger-button" onClick={onReset} disabled={busy}>Restore provider metadata</button> : <button type="button" className="ghost-button" onClick={onClose}>Cancel</button>}<button className="primary-button" disabled={busy || (!title.trim() && !publicationYear)} aria-busy={busy}>{busy ? <LoadingSpinner size={18} /> : <ShieldCheck size={18} />} Save and lock</button></div></form></section></div>;
}

function SeriesMatchWorkbench({ data, loading, busy, error, onClose, onSearch, onConfirm }) {
  const dialogRef = useDialog(onClose);
  const [draft, setDraft] = useState(data.query || data.series?.title || "");
  const candidates = data.candidates || [];
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}>
    <section className="modal series-match-workbench" role="dialog" aria-modal="true" aria-labelledby="series-match-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}>
      <DialogCloseButton onClose={onClose} label="Close series match" />
      <span className="eyebrow">Fix series match</span>
      <h2 id="series-match-title">{data.series?.title} <em>({data.series?.year})</em></h2>
      <p className="workbench-intro">Pick the publication run this series really is. Its issue list is replaced with the one you choose; your own corrections and your comic files are untouched.</p>
      <form className="series-match-search" onSubmit={(event) => { event.preventDefault(); onSearch(draft); }}>
        <input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Search publication runs…" aria-label="Search for the right publication run" />
        <button className="secondary-button" disabled={loading || !draft.trim()}>{loading ? <LoadingSpinner size={17} /> : <MagnifyingGlass size={17} />} Search</button>
      </form>
      {data.providersChecked?.length ? <small className="series-match-providers">Searched {data.providersChecked.join(", ")}</small> : null}
      {loading ? <div className="series-match-empty"><LoadingSpinner size={20} label="Searching metadata services" /></div>
        : candidates.length ? <div className="discovery-results">{candidates.map((item) => {
            const itemId = `${item.provider || data.provider}-${item.providerSeriesId}`;
            return <article className="discovery-result" key={itemId}>
              <DiscoverCover src={item.cover} alt={`${item.title} cover`} />
              <div className="discovery-copy">
                <div className="discovery-title-row"><strong>{item.title}</strong><b>{item.yearLabel}</b></div>
                <span>{item.publisher || "Publisher unknown"} · {item.issueCount} known issue{item.issueCount === 1 ? "" : "s"}</span>
                <small>{item.providerName || data.providerName || data.provider}</small>
              </div>
              <Button busy={busy === itemId} busyLabel="Matching…" icon={<CheckCircle size={16} />} onClick={() => onConfirm(item)}>Use this match</Button>
            </article>;
          })}</div>
        : <div className="series-match-empty"><MagnifyingGlass size={22} weight="duotone" /><strong>No publication runs found</strong><span>Try the exact title, or add the publication year.</span></div>}
      {error ? <p className="workbench-error" role="alert">{error}</p> : null}
    </section>
  </div>;
}

function CoverWorkbench({ data, title, busy, error, onClose, onSelect, onUpload }) {
  const dialogRef = useDialog(onClose);
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal cover-workbench" role="dialog" aria-modal="true" aria-labelledby="cover-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close cover picker" /><span className="eyebrow">Change cover</span><h2 id="cover-workbench-title">{title || data.file?.filename}</h2><p className="workbench-intro">Choose art from the comic, a metadata provider, or upload your own image. This does not alter the original comic file.</p>
    <CoverPicker data={data} busy={busy} error={error} onSelect={onSelect} onUpload={onUpload} />
  </section></div>;
}

function BackdropWorkbench({ series, current, busy, error, onClose, onChoose, onAutomatic }) {
  const dialogRef = useDialog(onClose);
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal backdrop-workbench" role="dialog" aria-modal="true" aria-labelledby="backdrop-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}>
    <DialogCloseButton onClose={onClose} label="Close" />
    <h2 id="backdrop-workbench-title">Header background</h2>
    <p className="backdrop-workbench-intro">Choose a page from {series.title} to show behind the drawer&rsquo;s header.</p>
    <BackdropPicker series={series} current={current} busy={busy} error={error} onChoose={onChoose} onAutomatic={onAutomatic} />
  </section></div>;
}

function VolumeContentsWorkbench({ data, busy, error, onClose, onChange, onReset }) {
  const dialogRef = useDialog(onClose);
  const contents = data.collectionContents;
  const [seriesId, setSeriesId] = useState(contents.seriesId);
  const [issueInput, setIssueInput] = useState("");
  const [note, setNote] = useState("");
  const [inputError, setInputError] = useState("");
  function addIssues(event) {
    event.preventDefault();
    try {
      const issueNumbers = parseIssueInput(issueInput);
      if (!issueNumbers.length) throw new Error("Enter at least one issue number");
      setInputError("");
      onChange({ seriesId, issueNumbers, included: true, note });
    } catch (parseError) { setInputError(parseError.message); }
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal contents-workbench" role="dialog" aria-modal="true" aria-labelledby="contents-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close volume contents" /><span className="eyebrow">Volume contents</span><h2 id="contents-workbench-title">{data.file.filename}</h2><p className="workbench-intro">Review what this volume contains. Provider evidence remains visible; local corrections control which canonical issues count as owned.</p><div className="contents-summary"><strong>{contents.includedCount}</strong><span>canonical issues currently included</span>{contents.hasOverrides ? <b><ShieldCheck size={14} weight="fill" /> Local corrections applied</b> : null}</div><div className="contents-list">{contents.items.length ? contents.items.map((item) => <article className={!item.included ? "excluded" : item.resolved ? "resolved" : "unresolved"} key={`${item.seriesId}-${item.seriesLabel}-${item.issueNumber}-${item.source}`}><div><strong>{item.seriesLabel} #{item.issueNumber}</strong><small>{item.source} · {item.confidence}{item.evidence ? ` · ${item.evidence}` : ""}</small></div><span>{!item.included ? "Excluded" : item.resolved ? "Counts as owned" : "Needs series match"}</span>{item.seriesId ? <button disabled={busy} onClick={() => onChange({ seriesId: item.seriesId, issueNumbers: [item.issueNumber], included: !item.included, note: item.overrideNote || "Adjusted in volume contents" })}>{item.included ? "Exclude" : "Restore"}</button> : null}</article>) : <div className="drawer-empty"><ListBullets size={26} weight="duotone" /><strong>No issue contents established</strong><span>Add the known issues below; they will be stored as a local correction.</span></div>}</div><form className="contents-add-form" onSubmit={addIssues}><h3>Add included issues</h3><label><span>Canonical series</span><GlassSelect label="Canonical series" value={seriesId} onChange={setSeriesId} className="glass-select--fill" options={data.seriesOptions.map((series) => ({ value: series.id, label: `${series.title}${series.year ? ` (${series.year})` : ""}` }))} /></label><label><span>Issue numbers</span><input value={issueInput} onChange={(event) => setIssueInput(event.target.value)} placeholder="1-6, 8, Annual 1" /></label><label><span>Correction note</span><input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Publisher contents page, checked manually…" /></label><button className="primary-button" disabled={busy}><Plus size={18} /> Add issues</button></form>{contents.hasOverrides ? <button className="danger-button contents-reset" disabled={busy} onClick={onReset}>Restore provider contents</button> : null}{inputError || error ? <p className="workbench-error" role="alert">{inputError || error}</p> : null}</section></div>;
}

function SeriesRunWorkbench({ data, loading, busy, error, onClose, onConfirm, onBuildCollection }) {
  const dialogRef = useDialog(onClose);
  const series = data?.series || {};
  const candidates = data?.candidates;
  const [selected, setSelected] = useState([]);
  const [collectionName, setCollectionName] = useState(series.title || "");
  const [preference, setPreference] = useState("volumes");
  const [includeSpecials, setIncludeSpecials] = useState(true);
  useEffect(() => {
    setCollectionName(series.title || "");
    setSelected((data?.candidates || []).map((candidate) => candidate.providerSeriesId));
    setPreference("volumes");
    setIncludeSpecials(true);
  }, [series.id, candidates?.length]);
  const specialIds = new Set((candidates || []).filter((candidate) => /one[- ]shot|special/i.test(candidate.publishingFormat || "")).map((candidate) => candidate.providerSeriesId));
  const automaticSelection = selected.filter((id) => includeSpecials || !specialIds.has(id));
  const selectedCandidates = (candidates || []).filter((candidate) => automaticSelection.includes(candidate.providerSeriesId));
  const issueCount = selectedCandidates.reduce((count, candidate) => count + candidate.issueCount, 0);
  const specialCount = selectedCandidates.filter((candidate) => specialIds.has(candidate.providerSeriesId)).length;
  const mainCount = selectedCandidates.length - specialCount;
  function toggle(providerSeriesId) {
    setSelected((items) => items.includes(providerSeriesId) ? items.filter((id) => id !== providerSeriesId) : [...items, providerSeriesId]);
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}>
    <section className="modal series-run-workbench simple-series-plan" role="dialog" aria-modal="true" aria-labelledby="series-run-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}>
      <DialogCloseButton onClose={onClose} label="Close full-series search" />
      <span className="eyebrow">Find full series</span>
      <h2 id="series-run-workbench-title">{series.title}{series.year ? ` (${series.year})` : ""}</h2>
      <p className="workbench-intro">Add the whole series once. Flipparr will maintain its publication runs, issue coverage, volumes, and specials in the background.</p>
      {loading ? <div className="run-loading"><LoadingSpinner size={28} /><strong>Finding the full series…</strong><span>Checking related runs, issue lists, and specials.</span></div> : null}
      {!loading && candidates?.length ? <>
        <section className="automatic-series-plan">
          <div className="plan-heading"><span className="plan-icon"><CheckCircle size={23} weight="fill" /></span><div><strong>Complete series found</strong><small>{mainCount} main publication run{mainCount === 1 ? "" : "s"}{specialCount ? ` + ${specialCount} special${specialCount === 1 ? "" : "s"}` : ""} · {issueCount} canonical issues</small></div></div>
          <div className="plan-outcomes"><span><CheckCircle size={16} /> One series in your library</span><span><CheckCircle size={16} /> Volumes count toward their underlying issues</span><span><CheckCircle size={16} /> New releases and gaps stay monitored</span></div>
        </section>
        <section className="acquisition-choice">
          <div><strong>How should missing comics be acquired?</strong><small>You can change this later. Flipparr still recognizes every format you already own.</small></div>
          <div className="preference-grid">{[
            ["volumes", "Volumes first", "Prefer trades, hardcovers, and omnibuses that cover several issues."],
            ["issues", "Single issues first", "Prefer the original individual issue releases."],
            ["either", "Best available", "Use the fewest available releases to fill each gap."],
          ].map(([value, label, detail]) => <button type="button" className={preference === value ? "selected" : ""} onClick={() => setPreference(value)} key={value}><span>{preference === value ? <CheckCircle size={18} weight="fill" /> : <BookOpen size={18} />}</span><strong>{label}</strong><small>{detail}</small></button>)}</div>
          <Toggle checked={includeSpecials} onChange={setIncludeSpecials} title="Include specials and crossovers" description="One-shots stay visible in Specials and count toward complete-series coverage." />
        </section>
        <div className="full-series-actions"><button className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || automaticSelection.length < 1 || !collectionName.trim()} onClick={() => onBuildCollection(automaticSelection, collectionName.trim(), { acquisitionPreference: preference, includeSpecials })}>{busy ? <LoadingSpinner size={18} /> : <Plus size={18} />} Add &amp; monitor {issueCount} issues</button></div>
        <details className="advanced-run-details"><summary><span><Gear size={17} /> Advanced: review provider runs</span><small>Optional · automatic selection includes {automaticSelection.length} records</small></summary><div className="advanced-run-toolbar"><label><span>Series name</span><input value={collectionName} onChange={(event) => setCollectionName(event.target.value)} /></label><button type="button" onClick={() => setSelected(selected.length === candidates.length ? [] : candidates.map((candidate) => candidate.providerSeriesId))}>{selected.length === candidates.length ? "Clear all" : "Select all"}</button></div><div className="advanced-run-list">{candidates.map((candidate) => {
          const included = selected.includes(candidate.providerSeriesId) && (includeSpecials || !specialIds.has(candidate.providerSeriesId));
          return <article className={included ? "included" : ""} key={candidate.providerSeriesId}><label><input type="checkbox" checked={selected.includes(candidate.providerSeriesId)} onChange={() => toggle(candidate.providerSeriesId)} /><span><strong>{candidate.title}</strong><small>{candidate.yearLabel} · {candidate.publishingFormat || "Publication run"} · {candidate.issueCount} issues</small></span></label><div><a href={`https://www.comics.org/series/${candidate.providerSeriesId}/`} target="_blank" rel="noreferrer">Source</a><button disabled={busy} onClick={() => onConfirm(candidate.providerSeriesId)}>Use only this run</button></div></article>;
        })}</div></details>
      </> : null}
      {!loading && candidates && !candidates.length ? <div className="drawer-empty"><MagnifyingGlass size={28} /><strong>No confident full-series match found</strong><span>The library was not changed. This exception can be reviewed later in Metadata.</span></div> : null}
      {error ? <p className="workbench-error" role="alert">{error}</p> : null}
    </section>
  </div>;
}

function FileRunWorkbench({ data, busy, error, onClose, onMove }) {
  const dialogRef = useDialog(onClose);
  const current = data.current || {};
  const existingRuns = (data.seriesOptions || []).filter((run) => run.title !== current.seriesTitle);
  const suggestedTitle = current.title && current.title !== current.seriesTitle ? current.title : "";
  const [mode, setMode] = useState("new");
  const [seriesId, setSeriesId] = useState(existingRuns[0]?.id || "");
  const [title, setTitle] = useState(suggestedTitle);
  const [year, setYear] = useState(current.publicationYear || "");
  const [publisher, setPublisher] = useState(current.publisher || "");
  function submit(event) {
    event.preventDefault();
    if (mode === "existing") onMove({ seriesId });
    else onMove({ title: title.trim(), year: year || null, publisher: publisher.trim() });
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal file-run-workbench" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="file-run-title" onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close run assignment" /><span className="eyebrow">Change publication run</span><h2 id="file-run-title">{data.file.filename}</h2><p className="workbench-intro">This file is currently attached to <strong>{current.seriesTitle}</strong>. Changing its run updates only the catalog relationship—the comic file will not be renamed, moved, or modified.</p><form onSubmit={submit} className="run-assignment-form"><div className="run-assignment-choice"><button type="button" className={mode === "new" ? "active" : ""} onClick={() => setMode("new")}><Plus size={18} /><span><strong>Separate into a new run</strong><small>Use this when the file represents a distinct series, miniseries, or group of one-shots.</small></span></button><button type="button" className={mode === "existing" ? "active" : ""} onClick={() => setMode("existing")}><Books size={18} /><span><strong>Move to an existing run</strong><small>Attach this file to another canonical run already in the library.</small></span></button></div>{mode === "existing" ? <label className="form-field"><span>Publication run</span><GlassSelect label="Publication run" placeholder="Choose a run…" value={seriesId} onChange={setSeriesId} className="glass-select--fill" options={existingRuns.map((run) => ({ value: run.id, label: `${run.title}${run.year ? ` (${run.year})` : ""}` }))} /></label> : <div className="run-assignment-fields"><label className="form-field"><span>New run title</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Publication run title…" required /></label><label className="form-field"><span>Start year</span><input type="number" min="1800" max="2200" value={year} onChange={(event) => setYear(event.target.value)} /></label><label className="form-field"><span>Publisher</span><input value={publisher} onChange={(event) => setPublisher(event.target.value)} /></label></div>}<div className="run-assignment-note"><ShieldCheck size={20} weight="fill" /><span><strong>Reversible catalog change</strong><small>You can use Change run again later. Covers, volume metadata, and issue-content evidence remain attached to this file.</small></span></div>{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="metadata-edit-actions"><button type="button" className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || (mode === "existing" ? !seriesId : !title.trim())}>{busy ? <LoadingSpinner size={18} /> : <ArrowRight size={18} />} {mode === "existing" ? "Move file" : "Create run and move file"}</button></div></form></section></div>;
}

// The way back in for a forgotten password. There is no email to send a reset
// link to, so recovery is a command on the server (see OPERATING.md, "Forgot
// your password"); this puts it where someone locked out will look.
const RESET_PASSWORD_COMMAND = "docker exec -it flipparr python app.py reset-password";

function ForgotPassword() {
  // "copied", "failed", or "" before either.
  const [copyState, setCopyState] = useState("");
  const commandRef = useRef(null);
  // The clipboard API exists only on HTTPS and localhost. Over plain HTTP the
  // button would do nothing, so it is left out and the command stays
  // selectable instead.
  const canCopy = typeof window !== "undefined" && window.isSecureContext
    && Boolean(navigator.clipboard?.writeText);
  useEffect(() => {
    if (copyState !== "copied") return undefined;
    const timer = setTimeout(() => setCopyState(""), 2000);
    return () => clearTimeout(timer);
  }, [copyState]);
  async function copy() {
    try {
      await navigator.clipboard.writeText(RESET_PASSWORD_COMMAND);
      setCopyState("copied");
    } catch {
      // A browser can still refuse (permissions, an unfocused page). Select
      // the command so copying it by hand is one keystroke, and say so --
      // a button that silently does nothing reads as broken.
      const selection = window.getSelection();
      if (commandRef.current && selection) selection.selectAllChildren(commandRef.current);
      setCopyState("failed");
    }
  }
  const copied = copyState === "copied";
  return <details className="login-help">
    <summary>Forgot your password?</summary>
    {/* Its own box: a <details> lays its children out in an internal slot,
        so layout set on the element itself never reaches them. */}
    <div className="login-help-body">
      <p>Run this where Flipparr is installed:</p>
      <div className="copy-field">
        {/* Each word kept whole: a line break inside "reset-password" reads
            as two words. The spaces stay real, so a hand selection copies
            the command exactly. */}
        <code ref={commandRef}>{RESET_PASSWORD_COMMAND.split(" ").map((word, index) =>
          <span key={index}>{index ? " " : ""}<span className="copy-field-word">{word}</span></span>)}</code>
        {canCopy ? <button type="button" className={copied ? "copied" : ""} onClick={copy}
          aria-label={copied ? "Copied" : "Copy command"} title={copied ? "Copied" : "Copy command"}>
          {copied ? <Check size={18} weight="bold" /> : <Copy size={18} />}
        </button> : null}
      </div>
      {copyState === "failed" ? <p role="status">Couldn’t copy it here. The command is selected, so copy it by hand.</p> : null}
      <p>It signs every device out. If your container isn’t named flipparr, use its name.</p>
      <span className="sr-only" aria-live="polite">{copied ? "Command copied" : ""}</span>
    </div>
  </details>;
}

function LoginView({ onSignedIn }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await apiRequest("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      onSignedIn(result);
    } catch (err) {
      setError(err.message || "Could not sign in");
    } finally {
      setBusy(false);
    }
  }
  return <div className="login-shell"><form className="login-card" onSubmit={submit}>
    {/* Its own brand block rather than the sidebar's: centred over a
        left-aligned form is the usual shape of a sign-in, and the sidebar's
        rules are tuned for a row in a nav. */}
    <div className="login-brand">
      <FlipparrMark size={72} />
    </div>
    <label>
      <span>Username</span>
      {/* name= matters as much as autoComplete=: password managers read both
          when deciding what to offer and what to save. */}
      <input name="username" value={username} autoFocus autoComplete="username"
        autoCapitalize="none" autoCorrect="off" spellCheck={false}
        onChange={(event) => setUsername(event.target.value)} />
    </label>
    <label>
      <span>Password</span>
      <input name="password" type="password" value={password} autoComplete="current-password"
        onChange={(event) => setPassword(event.target.value)} />
    </label>
    {error ? <p className="login-error" role="alert">{error}</p> : null}
    <button className="primary-button login-submit" disabled={busy || !username || !password} aria-busy={busy}>
      {busy ? <LoadingSpinner size={18} /> : <ShieldCheck size={18} />} {busy ? "Signing in…" : "Sign in"}
    </button>
    <ForgotPassword />
  </form></div>;
}

// URL <-> view. The server already serves the app shell for any path that is
// not under /api and not a real asset, and the auth gate lets those through so
// a login form can render, so these can be real paths rather than hash
// fragments. Hand-rolled because seven routes do not justify a router.
const ROUTE_BY_VIEW = {
  search: "/search",
  library: "/library",
  discover: "/discover",
  requests: "/pull-list",
  settings: "/settings",
  import: "/import",
  profile: "/profile",
};
// Paths this app used to answer on. A bookmark to one lands where it meant
// to rather than silently on the library.
const RETIRED_ROUTES = { "/requests": "requests", "/health": "settings" };
const VIEW_BY_ROUTE = Object.fromEntries(
  Object.entries(ROUTE_BY_VIEW).map(([view, path]) => [path, view])
);

// The drawer rides as a query parameter rather than a path segment: it can be
// open over the library, a search or a collection, so it is orthogonal to which
// view is showing.
// The views that carry a query: the Search page above 640px, and Discover,
// where a phone searches.
// The one view that carries a query: search is a page of its own at every
// width (2026-10-01); Discover is the week's releases everywhere.
const SEARCH_VIEWS = new Set(["search"]);
// How often the library's own polling may refresh the bell.
const BELL_REFRESH_MIN_MS = 30_000;
// At 900px and below the sidebar is a rail (or a phone's tab bar) with no
// room for a field, so search's field is the Search page's own.
const NARROW_QUERY = "(max-width: 900px)";
function useNarrowWidth() {
  const [narrow, setNarrow] = useState(() => Boolean(window.matchMedia?.(NARROW_QUERY).matches));
  useEffect(() => {
    const query = window.matchMedia?.(NARROW_QUERY);
    if (!query) return undefined;
    const change = () => setNarrow(query.matches);
    change();
    query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);
  return narrow;
}

// Comics' tabs after Recommended, which is plain /library: Plex and Komga
// open a library on its home, and so does a tap on Comics.
const LIBRARY_TABS = ["all", "collections", "arcs", "reading"];

function locationForState({ active, settingsSection, libraryTab, searchQuery, seriesId, readFileId }) {
  let path = ROUTE_BY_VIEW[active] || ROUTE_BY_VIEW.library;
  if (active === "settings" && settingsSection) path += `/${settingsSection}`;
  if (active === "library" && LIBRARY_TABS.includes(libraryTab)) path += `/${libraryTab}`;
  const params = new URLSearchParams();
  if (SEARCH_VIEWS.has(active) && searchQuery) params.set("q", searchQuery);
  if (seriesId) params.set("series", String(seriesId));
  // The reader is a parameter rather than a path, so Back closes it -- which
  // is what a phone's edge swipe is, and what a reader expects of it.
  if (readFileId) params.set("read", String(readFileId));
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

function stateFromLocation(pathname, search) {
  const params = new URLSearchParams(search || "");
  const segments = String(pathname || "").split("/").filter(Boolean);
  const first = `/${segments[0] || ""}`;
  const routed = VIEW_BY_ROUTE[first] || RETIRED_ROUTES[first] || "library";
  // An old link that searched Discover is a search.
  const active = routed === "discover" && params.get("q") ? "search" : routed;
  const requestedSection = segments[1];
  return {
    active,
    // No section is the list of them on a phone; above 640px it opens the first.
    settingsSection: active === "settings" && SETTINGS_SECTIONS.some((item) => item.id === requestedSection)
      ? requestedSection
      : "",
    libraryTab: active === "library" && LIBRARY_TABS.includes(requestedSection) ? requestedSection : "",
    searchQuery: SEARCH_VIEWS.has(active) ? params.get("q") || "" : "",
    seriesId: params.get("series") || "",
    readFileId: params.get("read") || "",
  };
}

const BOOT_ROUTE = (() => {
  const route = stateFromLocation(window.location.pathname, window.location.search);
  // On a phone your profile is a sheet over the library, not a page. Decided
  // here, before anything reads the address: left to an effect, the boot's
  // own canonicalisation put /profile back into history, and every Back onto
  // that entry opened the sheet again.
  if (route.active === "profile" && isPhoneWidth()) {
    window.history.replaceState(null, "", ROUTE_BY_VIEW.library);
    return { ...route, active: "library", profileSheet: true };
  }
  return route;
})();

export function App() {
  const [active, setActive] = useState(BOOT_ROUTE.active);
  const [profileSheet, setProfileSheet] = useState(Boolean(BOOT_ROUTE.profileSheet));
  const [searchQuery, setSearchQuery] = useState(BOOT_ROUTE.searchQuery);
  // A phone has no Search page, and above 640px Discover shows only the week's
  // releases -- so a search follows the width it is read at: a /search link
  // opens on a phone as Discover's results, and Discover's results widen into
  // the Search page.
  //
  // Only when the width changes: choosing Discover from the sidebar with a
  // search still open goes to the releases, not back to the results.
  const phoneWidth = usePhoneWidth();
  // What is typed into search, live; `searchQuery` is what was submitted --
  // the catalogs are asked only for that.
  const [searchDraft, setSearchDraft] = useState(BOOT_ROUTE.searchQuery);
  // Where search was opened from, to go back to when it is cleared.
  const searchReturn = useRef(null);
  // Arriving at /profile on a phone (a bookmark, a reload, a narrowed window)
  // is the library with your profile's sheet over it.
  // Reached later -- Back onto an old /profile entry, or a window narrowed
  // on the profile page -- the same: the entry becomes the library's and the
  // sheet opens over it, so no entry is left that reopens it on every Back.
  useEffect(() => {
    if (active !== "profile" || !phoneWidth) return;
    window.history.replaceState(null, "", ROUTE_BY_VIEW.library);
    setActive("library");
    setProfileSheet(true);
  }, [active, phoneWidth]);
  // Above 640px Discover is the releases alone; the search stays open on the
  // Search page, and Discover neither shows it nor carries it in its address.
  const viewQuery = active === "search" ? searchQuery : "";
  // A ?series= link cannot be honoured until the catalog it refers to exists.
  const [pendingSeriesId, setPendingSeriesId] = useState(BOOT_ROUTE.seriesId);
  // What is being read, if anything: a file id, from ?read=.
  const [readFileId, setReadFileId] = useState(BOOT_ROUTE.readFileId);
  // Leaving the reader is the one moment the continue shelf is certainly out
  // of date, so that is when it is asked again -- by the reader closing or by
  // Back, which is why this watches the file rather than the close button.
  // The page to open at, when the caller means a particular one rather than
  // wherever the comic was left.
  const [readFrom, setReadFrom] = useState(null);
  // Which comic was finished, rather than a flag: opening a different one
  // falsifies this in the same render that swaps the reader's file, so there
  // is no clean-up effect to get wrong.
  const [finishedFileId, setFinishedFileId] = useState("");
  const [readingVersion, setReadingVersion] = useState(0);
  const noteReadingChanged = useCallback(() => setReadingVersion((version) => version + 1), []);
  // Marking read or unread: this profile's own place, so it needs no
  // confirmation; the drawer and the grid re-read their state after.
  async function markIssueRead(issue, read) {
    try {
      await apiRequest(`/api/v1/files/${issue.fileId}/progress`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ read }) });
      noteReadingChanged();
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  // Every run of a collection read or unread, then one word about it.
  async function markRunsRead(runs, read) {
    for (const run of runs) {
      await apiRequest(`/api/v1/series/${run.id}/reading`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ read }) });
    }
    noteReadingChanged();
    showToast(`${runs.length} run${runs.length === 1 ? "" : "s"} marked as ${read ? "read" : "unread"}`);
  }
  async function markRunRead(series, read) {
    try {
      await apiRequest(`/api/v1/series/${series.id}/reading`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ read }) });
      noteReadingChanged();
      showToast(read ? `${series.title} marked as read` : `${series.title} marked as unread`);
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  /**
   * Open a comic. Callers know different things: the Continue shelf and the
   * issue rows know a file, a card in the grid may only know a run that has
   * never been opened. The run is resolved here rather than in the grid,
   * which would otherwise need the whole library's archives sniffed on every
   * visit to know what is readable.
   */
  const readComic = useCallback(async (target) => {
    // Opening a comic puts the drawer away. The reader sits *under* an open
    // drawer -- that is what lets the drawer open over a comic you are
    // reading -- so leaving it up would mean pressing Read and watching
    // nothing happen, with the reader hidden behind it.
    // "From the beginning" has to say so. Handing the reader a file id alone
    // gets the place that file was left at -- which, for the first issue of a
    // run you have read, is its last page.
    const open = (fileId) => {
      setReadFrom(target?.fromStart ? 0 : null);
      setReadFileId(String(fileId));
      setReadingList(target?.listId ? { id: String(target.listId), name: target.listName || "" } : null);
      setSelectedSeries(null);
      setSelectedList(null);
      // Reading the issue you just finished keeps the same file id, so the
      // drawer would otherwise be left standing over page one.
      setFinishedFileId("");
    };
    const fileId = typeof target === "string" ? target : target?.id;
    if (fileId) { open(fileId); return; }
    // A run, or a story arc, resolves to the comic its Read button means.
    const path = target?.runId ? `/api/v1/series/${target.runId}/reading`
      : target?.listId ? `/api/v1/reading-lists/${target.listId}` : null;
    if (!path) return;
    try {
      const data = await apiRequest(path);
      if (data?.resume?.fileId) open(data.resume.fileId);
      else showToast("There is nothing here that can be read yet.", "error");
    } catch (error) { showToast(error.message, "error"); }
  }, []);
  useEffect(() => {
    if (readFileId) return;
    setReadingVersion((version) => version + 1);
  }, [readFileId]);
  // Bumped when the address no longer names an open drawer, so the drawer can
  // play its exit animation rather than being removed from the tree outright.
  const [drawerDismissSignal, setDrawerDismissSignal] = useState(0);
  const [selectedSeries, setSelectedSeries] = useState(null);
  // What the drawer shows right now, for work that finishes later. A check,
  // a save or a sync that ended after the drawer was closed used to set the
  // run again and open the drawer on its own; refreshing is only for a drawer
  // that is still open on that run.
  const selectedSeriesRef = useRef(null);
  useEffect(() => { selectedSeriesRef.current = selectedSeries; }, [selectedSeries]);
  const refreshOpenSeries = useCallback((refreshed) => {
    if (refreshed && selectedSeriesRef.current && String(selectedSeriesRef.current.id) === String(refreshed.id)) {
      setSelectedSeries(refreshed);
    }
  }, []);
  const [selectedCollection, setSelectedCollection] = useState(null);
  // A household collection's drawer, by id, so it redraws from the catalog
  // after every change; and the dialogs that make one or add runs to it.
  const [runCollectionId, setRunCollectionId] = useState(null);
  const [newCollectionOpen, setNewCollectionOpen] = useState(false);
  // "Add to collection" from a run: the same sheet an arc's "Add to" is.
  const [collectionAdd, setCollectionAdd] = useState(null);
  const [collectionPickerId, setCollectionPickerId] = useState(null);
  // This profile's reading list: what it added to come back to.
  const [readingListEntries, setReadingListEntries] = useState(null);
  // The saved story arc whose drawer is open, and a counter the Comics grid
  // reloads its arcs on when one is saved, refreshed or deleted.
  const [selectedList, setSelectedList] = useState(null);
  const [listsVersion, setListsVersion] = useState(0);
  // The story arc the open comic was opened from, if any: when the comic
  // ends, the next one is the arc's next, across runs.
  const [readingList, setReadingList] = useState(null);
  const [importingList, setImportingList] = useState(false);
  // "Add to story arc…": { issues, name } from an issue's menu or a run's
  // selection, or { issues: null } for a new arc.
  const [arcPicker, setArcPicker] = useState(null);
  const [newArcOpen, setNewArcOpen] = useState(false);
  // "Add issues" inside an arc: the arc it adds to.
  const [issuePickerList, setIssuePickerList] = useState(null);
  const [seriesParentCollection, setSeriesParentCollection] = useState(null);
  const [collectionTab, setCollectionTab] = useState("overview");
  const [scanState, setScanState] = useState("idle");
  const [scanProgress, setScanProgress] = useState(null);
  const [toast, setToast] = useState("");
  const [toastTone, setToastTone] = useState("success");
  const [toastLeaving, setToastLeaving] = useState(false);
  const toastTimers = useRef([]);
  const [reviewFocus, setReviewFocus] = useState(null);
  const [requestFocus, setRequestFocus] = useState(null);
  // The bell's news, kept per profile on the server, with what the profile
  // has dismissed of the things needing attention (notifications.js).
  const [bellNews, setBellNews] = useState(() => lastAnswer("/api/v1/notifications") || { items: [], unread: 0, dismissed: [] });
  const [bellError, setBellError] = useState("");
  const [catalog, setCatalog] = useState(null);
  const catalogRef = useRef(null);
  catalogRef.current = catalog;
  const catalogEtag = useRef(null);
  const [backendStatus, setBackendStatus] = useState("loading");
  const [authStatus, setAuthStatus] = useState(null);
  // The picker, opened over the app (Switch profile), and the Add sheet.
  const [picker, setPicker] = useState(null);
  const [addingProfile, setAddingProfile] = useState(false);
  const [profilesVersion, setProfilesVersion] = useState(0);
  const [settingsSection, setSettingsSection] = useState(BOOT_ROUTE.settingsSection);
  const [libraryTab, setLibraryTab] = useState(BOOT_ROUTE.libraryTab);
  const [workbench, setWorkbench] = useState(null);
  const [workbenchBusy, setWorkbenchBusy] = useState(false);
  const [workbenchError, setWorkbenchError] = useState("");
  const [issueWorkbench, setIssueWorkbench] = useState(null);
  const [issueBusy, setIssueBusy] = useState(false);
  const [issueError, setIssueError] = useState("");
  const [coverWorkbench, setCoverWorkbench] = useState(null);
  const [seriesCoverWorkbench, setSeriesCoverWorkbench] = useState(null);
  const [backdropWorkbench, setBackdropWorkbench] = useState(null);
  const [backdropBusy, setBackdropBusy] = useState(false);
  const [backdropError, setBackdropError] = useState("");
  const [backdropVersion, setBackdropVersion] = useState(0);
  const [unfollowBusy, setUnfollowBusy] = useState(false);
  const [matchWorkbench, setMatchWorkbench] = useState(null);
  const [matchLoading, setMatchLoading] = useState(false);
  const [matchBusy, setMatchBusy] = useState("");
  const [matchError, setMatchError] = useState("");
  const [coverBusy, setCoverBusy] = useState(false);
  const [coverError, setCoverError] = useState("");
  const [contentsWorkbench, setContentsWorkbench] = useState(null);
  const [contentsBusy, setContentsBusy] = useState(false);
  const [contentsError, setContentsError] = useState("");
  const [runWorkbench, setRunWorkbench] = useState(null);
  const [runLoading, setRunLoading] = useState(false);
  const [runBusy, setRunBusy] = useState(false);
  const [runError, setRunError] = useState("");
  const [fileRunWorkbench, setFileRunWorkbench] = useState(null);
  const [fileRunBusy, setFileRunBusy] = useState(false);
  const [fileRunError, setFileRunError] = useState("");
  const [structureWorkbench, setStructureWorkbench] = useState(null);
  const [structureBusy, setStructureBusy] = useState(false);
  const [structureError, setStructureError] = useState("");
  const [replacementFile, setReplacementFile] = useState(null);
  const [replacementBusy, setReplacementBusy] = useState(false);
  const [replacementError, setReplacementError] = useState("");
  const [requestBusyKey, setRequestBusyKey] = useState("");
  const [setupCompleted, setSetupCompleted] = useState(null);
  // Remembered per browser: someone who works in the icon rail should not have
  // to collapse it again on every visit. Storage can throw, and an unreadable
  // preference is simply the default.
  const [mergeWorkbench, setMergeWorkbench] = useState(null);
  const [mergeBusy, setMergeBusy] = useState(false);
  const [mergeError, setMergeError] = useState("");
  async function loadSetupState() {
    try {
      const settings = await apiRequest("/api/v1/settings");
      setSetupCompleted(Boolean(settings?.setupCompleted));
    } catch {
      // An unreachable or unauthenticated backend is not evidence that setup is
      // outstanding, and guessing wrong puts the wizard over a working library.
      setSetupCompleted(true);
    }
  }
  async function finishSetup(folder, recursive) {
    setSetupCompleted(true);
    navigate("library");
    if (folder) await scanLibrary(folder, recursive);
    else await loadCatalog();
  }
  // A notification names one problem, so clicking it opens that problem
  // rather than whichever happens to be first. A fresh object every time, so
  // clicking the same row again after browsing elsewhere re-aims.
  function reviewProblem(item) {
    setReviewFocus(item?.id ? { id: item.id } : null);
    navigate("settings", "health");
  }

  // The bell's rows each carry their destination, so this is the one place
  // that has to know how to land on each screen. Called with no argument by
  // "Review all", which opens the screen without aiming at anything.
  function openNotification(item) {
    if (!item) { navigate("settings", "health"); return; }
    if (item.view === "requests") {
      setRequestFocus({ ...item.focus });
      navigate("requests");
      return;
    }
    if (item.view === "settings") {
      navigate("settings", item.focus?.section);
      return;
    }
    reviewProblem(item.focus);
  }

  // News opens what it is about: the run that comics arrived in, or the
  // request that was decided.
  function openNews(target) {
    if (target?.view === "series") {
      const series = (catalog?.series || []).find((item) => String(item.id) === String(target.seriesId));
      navigate("library");
      if (series) openSeries(series);
      return;
    }
    if (target?.view === "requests") {
      setRequestFocus({ ...target.focus });
      navigate("requests");
    }
    // A connector in trouble (out of credit, key refused) opens where it is set up.
    if (target?.view === "settings") navigate("settings", target.section);
  }

  // Dismissals made here and not yet confirmed: an answer to some other
  // request (a refresh, a clear) must not put them back.
  const pendingDismissals = useRef(new Set());
  const withPending = (dismissed) => [...new Set([...(dismissed || []), ...pendingDismissals.current])];
  const bellRefreshedAt = useRef(0);
  const refreshBell = useCallback(async () => {
    try {
      const data = await apiRequest("/api/v1/notifications");
      bellRefreshedAt.current = Date.now();
      setBellNews({ ...data, dismissed: withPending(data.dismissed) });
      setBellError("");
      return data;
    } catch (error) {
      if (error.status !== 401) setBellError("Notifications could not be loaded. They will be tried again.");
      return null;
    }
  }, []);
  async function sendBell(path, body) {
    try {
      const result = await apiRequest(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      // Only the dismissals endpoint speaks for the dismissals.
      if (path.endsWith("/dismissed")) {
        setBellNews((current) => ({ ...current, dismissed: withPending(result.dismissed) }));
      } else if (result.items) {
        setBellNews((current) => ({ ...result, dismissed: current.dismissed }));
      } else {
        setBellNews((current) => ({ ...current, ...result, dismissed: current.dismissed }));
      }
      setBellError("");
      return result;
    } catch (error) {
      setBellError(error.message);
      refreshBell();
      return null;
    }
  }
  function changeDismissals({ add = [], remove = [] }) {
    // At once on screen; the server's answer settles it.
    add.forEach((key) => pendingDismissals.current.add(key));
    setBellNews((current) => ({
      ...current,
      dismissed: [...new Set([...(current.dismissed || []).filter((key) => !remove.includes(key)), ...add])],
    }));
    return sendBell("/api/v1/notifications/dismissed", { add, remove })
      .finally(() => add.forEach((key) => pendingDismissals.current.delete(key)));
  }

  const viewerIsAdmin = isAdmin(authStatus?.viewer);
  // What the shared screens need from the web app (src/host.js): its
  // requests, and whether this viewer is shown the panel editor. Images load
  // from their own addresses here, so there is no `pageSource`.
  const readerHost = useMemo(() => ({ api: apiRequest, canEditPanels: viewerIsAdmin }), [viewerIsAdmin]);
  const attention = useMemo(
    () => needsAttention(catalog, bellNews.dismissed || [], { admin: viewerIsAdmin }),
    [catalog, bellNews.dismissed, viewerIsAdmin]);
  // The news is asked for when the library is -- an import that brought
  // comics changes both -- but not on every one of the library's 5-second
  // polls while something downloads: once every half minute from here, and
  // at once when the bell is opened or a profile signs in.
  const bellViewer = useRef(null);
  useEffect(() => {
    if (!catalog || !authStatus?.viewer) return;
    const changed = bellViewer.current !== authStatus.viewer.id;
    bellViewer.current = authStatus.viewer.id;
    if (changed || Date.now() - bellRefreshedAt.current >= BELL_REFRESH_MIN_MS) refreshBell();
  }, [catalog, authStatus?.viewer?.id]);
  // A dismissal whose notification is gone is forgotten: a retried job fails
  // again under the same id, and must be able to say so.
  useEffect(() => {
    if (!catalog || !viewerIsAdmin || !bellNews.dismissed?.length) return;
    const stale = staleDismissals(catalog, bellNews.dismissed);
    if (stale.length) changeDismissals({ remove: stale });
  }, [catalog, bellNews.dismissed, viewerIsAdmin]);
  // Dismissals this browser kept before the server did move up once, and
  // are forgotten here only once the server has them.
  useEffect(() => {
    if (!authStatus?.viewer) return;
    const legacy = readLegacyDismissed(profileStorage());
    if (!legacy.length) { forgetLegacyState(profileStorage()); return; }
    changeDismissals({ add: legacy }).then((result) => { if (result) forgetLegacyState(profileStorage()); });
  }, [authStatus?.viewer?.id]);

  const bell = {
    attention, activity: bellNews, error: bellError,
    onRefresh: refreshBell,
    onOpen: (item) => (item.target ? openNews(item.target) : openNotification(item)),
    onDismiss: (item) => changeDismissals({ add: [item.id] }),
    onSeen: () => sendBell("/api/v1/notifications/read", { all: true }),
    onClear: (item) => {
      setBellNews((current) => ({ ...current, items: current.items.filter((row) => row.id !== item.id) }));
      return sendBell("/api/v1/notifications/clear", { ids: [item.id] });
    },
    // Clear all clears the news. What needs someone stays until it is dealt
    // with, or dismissed one by one.
    onClearAll: () => {
      setBellNews((current) => ({ ...current, items: [], unread: 0 }));
      sendBell("/api/v1/notifications/clear", { all: true });
    },
    onRetry: async (item) => {
      try {
        const result = await apiRequest(`/api/v1/acquisition-jobs/${item.retryJobId}/retry`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
        });
        showToast(result.detail || "Trying again");
        await loadCatalog();
      } catch (error) {
        showToast(error.message, "error");
      }
    },
  };

  function navigate(id, sectionId) {
    // A phone opens your profile as a sheet over this page, not as a page.
    if (id === "profile" && isPhoneWidth()) { setProfileSheet(true); return; }
    setActive(id); setSelectedSeries(null); setSelectedCollection(null); setSeriesParentCollection(null);
    // Settings shows one section at a time, so a deep link selects the section
    // rather than scrolling to it.
    if (id === "settings" && sectionId) setSettingsSection(sectionId);
    // Choosing Settings again, from inside a section, goes back to the list,
    // as tapping the tab you are on does on an iPhone.
    else if (id === "settings" && active === "settings") setSettingsSection("");
    // Comics opens on Recommended, from the nav or a link, unless a tab is named.
    if (id === "library") setLibraryTab(LIBRARY_TABS.includes(sectionId) ? sectionId : "");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  function goSearch() {
    if (active === "search") return;
    searchReturn.current = { active, section: active === "settings" ? settingsSection : active === "library" ? libraryTab : undefined };
    navigate("search");
  }
  // "/" and Cmd/Ctrl-K go to search from anywhere but a text field, the
  // reader or a dialog: the sidebar's field above 900px, the page below.
  // Cmd-F stays the browser's find.
  const goSearchRef = useRef(goSearch);
  goSearchRef.current = goSearch;
  useEffect(() => {
    function onKey(event) {
      const slash = event.key === "/" && !event.metaKey && !event.ctrlKey && !event.altKey;
      const commandK = (event.metaKey || event.ctrlKey) && !event.altKey && String(event.key).toLowerCase() === "k";
      if (!slash && !commandK) return;
      const target = event.target;
      if (target?.closest?.("input, textarea, select, [contenteditable='true']")) return;
      // The reader, drawers and dialogs are all aria-modal.
      if (document.querySelector("[aria-modal='true']")) return;
      event.preventDefault();
      const field = document.getElementById("sidebar-search-input");
      if (field && field.offsetParent !== null) field.focus();
      else goSearchRef.current();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  // Clearing search goes back to where it was opened from.
  function leaveSearch() {
    setSearchDraft("");
    setSearchQuery("");
    const back = searchReturn.current;
    searchReturn.current = null;
    if (active === "search") navigate(back?.active && back.active !== "search" ? back.active : "library", back?.section);
  }
  function openSearch(value) {
    const cleaned = String(value || "").trim();
    // An empty value is the clear button, not a rejected search.
    if (cleaned && cleaned.length < 2) return;
    setSearchQuery(cleaned);
    setSearchDraft(cleaned);
    goSearch();
    if (!cleaned) return;
    // The two halves of this screen read different libraries otherwise. The
    // "in your library" list filters the catalog this client last fetched,
    // while Discover's "already in your library" is decided server-side
    // against the current one -- and the poll below only runs while a scan,
    // a download or enrichment is active, so on an idle library the client's
    // copy is whatever it loaded on mount and never changes. Add a series and
    // search for it and the page contradicts itself, indefinitely.
    loadCatalog();
  }
  // Fetched per profile: switching profiles is switching lists.
  useEffect(() => {
    if (!authStatus) return undefined;
    let live = true;
    setReadingListEntries(null);
    apiRequest("/api/v1/me/reading-list")
      .then((data) => { if (live) setReadingListEntries(data); })
      .catch(() => { if (live) setReadingListEntries(EMPTY_ENTRIES); });
    return () => { live = false; };
  }, [authStatus?.viewer?.id, Boolean(authStatus)]);
  async function toggleReadingList(kind, id, on) {
    const before = readingListEntries;
    setReadingListEntries((current) => withEntry(current, kind, id, on));
    try {
      const saved = await apiRequest(`/api/v1/me/reading-list/${kind}/${id}`, on
        ? { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }
        : { method: "DELETE" });
      setReadingListEntries(saved);
      showToast(on ? "Added to your reading list" : "Taken off your reading list");
    } catch (error) {
      setReadingListEntries(before);
      showToast(error.message, "error");
    }
  }
  // An imported reading list opens as its story arc, the count of what is
  // here said once (not for one imported before).
  function listImported(result) {
    setListsVersion((version) => version + 1);
    if (!result.existed) showToast(`${result.name} imported · ${result.owned} of ${result.issueCount} in your library`);
    openList({ id: result.id, name: result.name, cover: result.cover, issueCount: result.issueCount, owned: result.owned, missing: result.missing, seriesTitles: (result.series || []).map((group) => group.title) });
  }
  async function importCommunityList(entry) {
    try {
      listImported(await apiRequest("/api/v1/reading-lists/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: entry.url }) }));
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  // A new story arc, as a new collection: named (and described, if you
  // like), then opened on what to put in it.
  async function createStoryArc({ name, summary }) {
    const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const made = await apiRequest("/api/v1/reading-lists/manual", json("POST", { name, issueIds: [] }));
    if (summary) await apiRequest(`/api/v1/reading-lists/${made.id}`, json("PATCH", { description: summary }));
    setNewArcOpen(false);
    setListsVersion((version) => version + 1);
    openList({ id: made.id, name: made.name, cover: made.cover, issueCount: 0, owned: 0, missing: 0, seriesTitles: [] });
    setIssuePickerList({ id: made.id, name: made.name });
    showToast(`${made.name} created`);
  }
  async function createRunCollection({ name, summary, seriesIds = [] }, { open = true, pick = false } = {}) {
    const made = await apiRequest("/api/v1/run-collections", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, summary, seriesIds }),
    });
    await loadCatalog();
    setNewCollectionOpen(false);
    if (open) setRunCollectionId(String(made.id));
    // A new, empty collection opens on what to put in it, as a new arc does.
    if (pick) setCollectionPickerId(String(made.id));
    showToast(`${made.name} created`);
    return made;
  }
  async function saveRunCollection(id, changes) {
    const saved = await apiRequest(`/api/v1/run-collections/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes),
    });
    if (saved.visible === false) {
      // The admin took someone's collection back out of the household: it is
      // no longer theirs to see, so the drawer closes.
      const name = catalog?.runCollections?.find((item) => String(item.id) === String(id))?.name;
      setRunCollectionId(null);
      await loadCatalog();
      showToast(`${name || "The collection"} is no longer shared with the household`);
      return saved;
    }
    // The server's answer is the collection as it now is: drawn at once,
    // rather than after the whole catalog has come back, which took seconds
    // on a real library. The catalog follows behind.
    setCatalog((current) => current ? {
      ...current,
      runCollections: (current.runCollections || []).map((item) => (String(item.id) === String(saved.id) ? { ...item, ...saved } : item)),
    } : current);
    loadCatalog();
    return saved;
  }
  async function uploadRunCollectionCover(id, file) {
    await apiRequest(`/api/v1/run-collections/${id}/cover/upload`, { method: "POST", headers: { "Content-Type": file.type }, body: file });
    await loadCatalog();
    showToast("Cover picture saved");
  }
  async function deleteRunCollection(id) {
    const name = catalog?.runCollections?.find((item) => String(item.id) === String(id))?.name;
    await apiRequest(`/api/v1/run-collections/${id}`, { method: "DELETE" });
    setRunCollectionId(null);
    await loadCatalog();
    showToast(`${name || "Collection"} deleted`);
  }
  async function addRunsToCollection(id, seriesIds) {
    const saved = await saveRunCollection(id, { add: seriesIds });
    setCollectionPickerId(null);
    showToast(`Added ${seriesIds.length} run${seriesIds.length === 1 ? "" : "s"} to ${saved.name}`);
  }
  function openSeries(series) {
    setSelectedCollection(null);
    setSeriesParentCollection(null);
    setSelectedList(null);
    setSelectedSeries(series);
  }
  function openList(list) {
    setSelectedSeries(null);
    setSeriesParentCollection(null);
    setSelectedCollection(null);
    setSelectedList(list);
  }
  async function saveStoryArc({ arcId, name }) {
    try {
      const saved = await apiRequest("/api/v1/reading-lists", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: "metron", arcId }),
      });
      setListsVersion((version) => version + 1);
      showToast(saved.existed ? `${saved.name || name} is already in your comics` : `${saved.name || name} saved to your comics`);
      return { ok: true, saved };
    } catch (error) {
      showToast(error.message, "error");
      return { ok: false, error: error.message };
    }
  }
  async function pullReadingList(list) {
    try {
      const result = await apiRequest(`/api/v1/reading-lists/${list.id}/pull`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await loadCatalog();
      const failed = (result.failed || []).map((item) => item.title).filter(Boolean);
      showToast(`${list.name} · ${result.count} issue${result.count === 1 ? "" : "s"} added to your Pull List${failed.length ? ` · ${failed.join(", ")} could not be pulled` : ""}`,
        failed.length ? "error" : undefined);
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  function requestReadingListMissing(list) {
    return askAdmin({ kind: "discover_arc", provider: "metron", arcId: list.providerArcId, title: list.name, cover: list.cover, issueCount: list.issueCount });
  }
  async function refreshReadingList(list) {
    try {
      const result = await apiRequest(`/api/v1/reading-lists/${list.id}/refresh`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      setListsVersion((version) => version + 1);
      showToast(result.added ? `${list.name} · ${result.added} new issue${result.added === 1 ? "" : "s"} from Metron` : `${list.name} is up to date`);
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  async function deleteReadingList(list) {
    try {
      await apiRequest(`/api/v1/reading-lists/${list.id}`, { method: "DELETE" });
      setSelectedList(null);
      setListsVersion((version) => version + 1);
      showToast(`${list.name} removed from your comics`);
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  function addIssueToArc(issue) {
    setArcPicker({ issues: [{ id: String(issue.id) }], name: `${issue.seriesTitle || issue.contextLabel || "Issue"} #${issue.number}` });
  }
  function addIssuesToArc(issues, runTitle) {
    if (issues.length === 1) { addIssueToArc({ ...issues[0], seriesTitle: issues[0].seriesTitle || runTitle }); return; }
    setArcPicker({ issues: issues.map((issue) => ({ id: String(issue.id) })), name: `${issues.length} issues of ${runTitle}` });
  }
  function arcGone(message) {
    setSelectedList(null);
    setListsVersion((version) => version + 1);
    showToast(message);
  }
  async function markListRead(list, read) {
    try {
      await apiRequest(`/api/v1/reading-lists/${list.id}/reading`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ read }) });
      noteReadingChanged();
      showToast(read ? `${list.name} marked as read` : `${list.name} marked as unread`);
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  function openCollection(collection) { if (!catalog?.collectedEditionsEnabled) return; setSelectedSeries(null); setSeriesParentCollection(null); setCollectionTab("overview"); setSelectedCollection(collection); }
  function openCollectionRun(series) { setSeriesParentCollection(selectedCollection); setSelectedCollection(null); setSelectedSeries(series); }
  function returnToCollection() {
    if (!seriesParentCollection) return;
    const refreshed = catalog?.families?.find((collection) => collection.id === seriesParentCollection.id) || seriesParentCollection;
    setSelectedSeries(null);
    setSeriesParentCollection(null);
    setSelectedCollection(refreshed);
  }
  // A new message replaces the one showing, and restarts its time; the last
  // 300ms play its exit rather than cutting it off.
  // `tone` is "success" (a green check) or "error" (a red warning): a failed
  // action said with a check read as though it had worked.
  function showToast(message, tone = "success") {
    toastTimers.current.forEach((timer) => window.clearTimeout(timer));
    setToast(message);
    setToastTone(tone);
    setToastLeaving(false);
    toastTimers.current = [
      window.setTimeout(() => setToastLeaving(true), 2900),
      window.setTimeout(() => { setToast(""); setToastLeaving(false); }, 3200),
    ];
  }
  async function loadAuthStatus() {
    try {
      const status = await apiRequest("/api/v1/auth/status");
      // A different profile from the one this page was drawn for: start
      // again as that profile, so its storage and nothing else is read.
      const viewerId = status?.viewer?.id ?? null;
      if (viewerId && viewerId !== storageProfile() && storable()) {
        enterProfile(viewerId);
        return status;
      }
      setAuthStatus(status);
      if (viewerId) syncReaderPrefs();
      return status;
    } catch {
      // Treat an unreachable status endpoint as "no gate", so a backend problem
      // surfaces as the existing offline banner rather than a stuck login form.
      setAuthStatus({ method: "none", authenticated: true });
      return null;
    }
  }
  async function signOut({ forgetDevice = false } = {}) {
    try {
      await apiRequest("/api/v1/auth/logout", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ forgetDevice }),
      });
    } catch {
      // Even if the call fails the session may already be gone; the fresh
      // page decides what the user actually sees -- the picker on a shared
      // device, the sign-in form elsewhere.
    }
    enterProfile(null);
  }
  async function loadCatalog() {
    try {
      // The catalog is asked for again every few seconds while something is
      // moving. With the tag of the answer in hand, the server says "nothing
      // changed" in a few bytes rather than the whole library again.
      const known = catalogEtag.current;
      const response = await fetch("/api/v1/catalog", known ? { headers: { "If-None-Match": known } } : undefined);
      if (response.status === 304) {
        setBackendStatus("live");
        return catalogRef.current;
      }
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(data.error || `Request failed (${response.status})`);
        error.status = response.status;
        error.reason = data.reason || "";
        throw error;
      }
      catalogEtag.current = response.headers.get("ETag") || null;
      rememberAnswer("/api/v1/catalog", data);
      setCatalog(data);
      setBackendStatus("live");
      return data;
    } catch (error) {
      // A session can expire, or sign-in can be switched on, while this tab is
      // open. Re-check before deciding the backend is down, or the tab sits on
      // a stale library behind an offline banner with no way to sign in.
      if (error.status === 401 || error.status === 503) {
        await loadAuthStatus();
        return null;
      }
      setBackendStatus("offline");
      return null;
    }
  }
  async function pollScan(scanId, context = {}) {
    for (;;) {
      const scan = await apiRequest(`/api/v1/scans/${scanId}`);
      setScanProgress({ ...scan, ...context });
      if (scan.status === "complete") return scan;
      if (scan.status === "failed") throw new Error(scan.error || "Library scan failed");
      await new Promise((resolve) => window.setTimeout(resolve, 650));
    }
  }
  async function scanLibrary(folder, recursive = true) {
    const targets = folder
      ? [{ path: folder, recursive }]
      : (catalog?.roots || []).map((root) => ({ path: root.path, recursive: Boolean(root.recursive) }));
    // "add" was never a rendered view, so this navigated to a blank screen.
    if (!targets.length) { navigate("import"); return; }
    setScanState("scanning");
    setScanProgress(null);
    try {
      let changed = 0;
      let reused = 0;
      for (let index = 0; index < targets.length; index += 1) {
        const target = targets[index];
        const queued = await apiRequest("/api/v1/scans", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ folder: target.path, recursive: target.recursive, metadataMode: "local" }),
        });
        const scan = await pollScan(queued.id, {
          rootIndex: index + 1, rootTotal: targets.length, rootPath: target.path,
        });
        changed += Number(scan.changed_files || 0);
        reused += Number(scan.reused_files || 0);
      }
      await loadCatalog();
      setScanState("done");
      showToast(`Library inventory complete · ${changed} changed, ${reused} unchanged`);
    } catch (error) {
      setScanState("idle");
      showToast(error.message, "error");
    }
  }
  async function updateLibraryRoot(root, recursive) {
    try {
      await apiRequest(`/api/v1/library-roots/${root.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recursive }),
      });
      await loadCatalog();
      showToast("Library folder setting saved");
    } catch (error) {
      showToast(error.message, "error");
      throw error;
    }
  }
  async function removeLibraryRoot(root) {
    try {
      await apiRequest(`/api/v1/library-roots/${root.id}`, { method: "DELETE" });
      await loadCatalog();
      showToast("Folder removed from Flipparr · comic files were untouched");
    } catch (error) {
      showToast(error.message, "error");
      throw error;
    }
  }
  async function resolveReview(item) {
    try {
      await apiRequest("/api/v1/reviews/resolve", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: item.path, code: item.code, fingerprint: item.fingerprint }),
      });
      await loadCatalog();
      showToast("Review item resolved");
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  async function addSeriesAlias(seriesId, alias) {
    try {
      await apiRequest(`/api/v1/series/${seriesId}/aliases`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alias }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === String(seriesId));
      refreshOpenSeries(refreshed);
      showToast("Series alias confirmed");
      return { ok: true };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  async function createSeriesFamily(name, runIds) {
    try {
      await apiRequest("/api/v1/families", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, runIds }),
      });
      const selectedId = selectedSeries?.id;
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === selectedId);
      refreshOpenSeries(refreshed);
      showToast(`Collection “${name}” created`);
      return { ok: true };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  async function setSeriesFamily(seriesId, familyId) {
    try {
      await apiRequest(`/api/v1/series/${seriesId}/family`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ familyId }),
      });
      const selectedId = selectedSeries?.id;
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === selectedId);
      refreshOpenSeries(refreshed);
      showToast(familyId ? "Run moved into collection" : "Run removed from collection");
      return { ok: true };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  async function openStoryStructure(collection) {
    setStructureError("");
    try {
      const data = await apiRequest(`/api/v1/collections/${collection.id}/structure`);
      setStructureWorkbench(data);
    } catch (error) { showToast(error.message, "error"); }
  }
  async function saveStoryStructure(arcs) {
    setStructureBusy(true); setStructureError("");
    try {
      const result = await apiRequest(`/api/v1/collections/${structureWorkbench.collection.id}/structure`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ arcs }),
      });
      const data = await loadCatalog();
      const refreshed = data?.families?.find((item) => item.id === result.collection.id);
      if (refreshed) setSelectedCollection(refreshed);
      setStructureWorkbench(null);
      showToast(`${result.arcCount} story group${result.arcCount === 1 ? "" : "s"} saved`);
    } catch (error) { setStructureError(error.message); }
    setStructureBusy(false);
  }
  const [rebuildingRun, setRebuildingRun] = useState(false);
  const [rebuildResult, setRebuildResult] = useState("");
  async function rebuildSeriesRun(series) {
    setRebuildingRun(true);
    setRebuildResult("");
    try {
      const result = await apiRequest(`/api/v1/series/${series.id}/rebuild`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      const data = await loadCatalog();
      const updated = data?.series?.find((item) => item.id === String(series.id));
      refreshOpenSeries(updated);
      const providerNote = result?.refresh?.status === "unavailable"
        ? " The provider could not be reached, so run Refresh details later."
        : "";
      setRebuildResult(
        `Rebuilt ${result?.clearedIssues ?? 0} issue${result?.clearedIssues === 1 ? "" : "s"}`
        + ` from ${result?.rederivedFromFiles ?? 0} file${result?.rederivedFromFiles === 1 ? "" : "s"}.${providerNote}`
      );
    } catch (error) {
      setRebuildResult(error.message || "This run could not be rebuilt");
    } finally {
      setRebuildingRun(false);
    }
  }
  async function syncSeriesIssues(seriesId, automatic = false) {
    try {
      const result = await apiRequest(`/api/v1/series/${seriesId}/issues/sync`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === String(seriesId));
      refreshOpenSeries(refreshed);
      const metadata = result.metadata || {};
      const repaired = (metadata.repairedTitleCount || 0) + (metadata.repairedDateCount || 0);
      const remaining = (metadata.missingTitleCount || 0) + (metadata.missingDateCount || 0);
      if (repaired) {
        showToast(`${repaired} missing field${repaired === 1 ? "" : "s"} filled${remaining ? ` · ${remaining} still unavailable` : " · metadata complete"}`);
      } else if (remaining) {
        showToast(`Metadata checked · ${remaining} field${remaining === 1 ? "" : "s"} still unavailable from providers`);
      } else {
        showToast(`${result.issueCount} issues checked · metadata complete`);
      }
      return { ok: true, result };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  async function openSeriesRunWorkbench(series) {
    setRunError("");
    setRunLoading(true);
    setRunWorkbench({ series: { id: series.id, title: series.title, year: series.year, publisher: series.publisher, ownedIssueNumbers: series.issues?.filter((issue) => issue.directOwned || issue.collectionOwned).map((issue) => issue.number) || [] }, candidates: null });
    try {
      const data = await apiRequest(`/api/v1/series/${series.id}/issue-runs`);
      setRunWorkbench(data);
    } catch (error) {
      setRunError(error.message);
    }
    setRunLoading(false);
  }
  async function confirmSeriesRun(providerSeriesId) {
    setRunBusy(true); setRunError("");
    try {
      const result = await apiRequest(`/api/v1/series/${runWorkbench.series.id}/issue-run`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ providerSeriesId }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === runWorkbench.series.id);
      refreshOpenSeries(refreshed);
      setRunWorkbench(null);
      showToast(`${result.issueCount} canonical issues loaded from the confirmed run`);
    } catch (error) {
      setRunError(error.message);
    }
    setRunBusy(false);
  }
  function mergeCandidateScore(source, candidate) {
    const compact = (value) => String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
    let score = 0;
    if (compact(source.title) === compact(candidate.title)) score += 1000;
    if (compact(source.publisher) && compact(source.publisher) === compact(candidate.publisher)) score += 200;
    if (source.year && candidate.year) score -= Math.min(100, Math.abs(Number(source.year) - Number(candidate.year)));
    return score;
  }
  async function previewSeriesMerge(source, targetId, candidates) {
    setMergeBusy(true); setMergeError("");
    setMergeWorkbench({ source, targetId, candidates, preview: null });
    try {
      const preview = await apiRequest("/api/v1/series/merge/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sourceId: source.id, targetId }),
      });
      setMergeWorkbench({ source, targetId, candidates, preview });
    } catch (error) { setMergeError(error.message); }
    setMergeBusy(false);
  }
  function openSeriesMergeWorkbench(source) {
    const candidates = (catalog?.series || [])
      .filter((candidate) => String(candidate.id) !== String(source.id))
      .sort((left, right) => mergeCandidateScore(source, right) - mergeCandidateScore(source, left));
    const likely = candidates.find((candidate) => mergeCandidateScore(source, candidate) >= 1000);
    setMergeError("");
    setMergeWorkbench({ source, targetId: likely?.id || "", candidates, preview: null });
    if (likely) previewSeriesMerge(source, likely.id, candidates);
  }
  async function confirmSeriesMerge(allowProviderConflicts) {
    if (!mergeWorkbench?.targetId) return;
    setMergeBusy(true); setMergeError("");
    try {
      const result = await apiRequest("/api/v1/series/merge", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sourceId: mergeWorkbench.source.id, targetId: mergeWorkbench.targetId, allowProviderConflicts }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((series) => String(series.id) === String(result.seriesId));
      setSelectedSeries(refreshed || null);
      setMergeWorkbench(null);
      showToast(`${result.title} is now one publication run`);
    } catch (error) { setMergeError(error.message); }
    setMergeBusy(false);
  }
  async function buildSeriesCollection(providerSeriesIds, name, options = {}) {
    setRunBusy(true); setRunError("");
    try {
      const result = await apiRequest(`/api/v1/series/${runWorkbench.series.id}/collection-structure`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ providerSeriesIds, name, ...options }),
      });
      const data = await loadCatalog();
      const collection = data?.families?.find((item) => item.id === result.collection.id);
      setRunWorkbench(null);
      setSelectedSeries(null);
      if (collection) setSelectedCollection(collection);
      showToast(`${name} added · ${result.issueCount} issues monitored · ${ACQUISITION_LABELS[options.acquisitionPreference] || ACQUISITION_LABELS.either}`);
    } catch (error) { setRunError(error.message); }
    setRunBusy(false);
  }
  async function openFileWorkbench(file, mode) {
    setWorkbenchError("");
    try {
      const data = await apiRequest(`/api/v1/files/${file.id}`);
      setWorkbench({ data, mode });
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  async function openFileRunWorkbench(file) {
    setFileRunError("");
    try {
      const data = await apiRequest(`/api/v1/files/${file.id}`);
      setFileRunWorkbench(data);
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  async function moveFileToRun(payload) {
    setFileRunBusy(true); setFileRunError("");
    try {
      const result = await apiRequest(`/api/v1/files/${fileRunWorkbench.file.id}/series-run`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      const data = await loadCatalog();
      const movedRun = data?.series?.find((series) => series.id === result.seriesId);
      setSelectedSeries(movedRun || null);
      setFileRunWorkbench(null);
      showToast(`File moved to “${result.title}”`);
    } catch (error) {
      setFileRunError(error.message);
    }
    setFileRunBusy(false);
  }
  async function finishMetadataChange(message, result) {
    const healing = result?.seriesHealing;
    const selectedId = healing?.seriesId || selectedSeries?.id;
    const data = await loadCatalog();
    const refreshed = data?.series?.find((item) => item.id === selectedId);
    if (refreshed) refreshOpenSeries(refreshed);
    else if (selectedSeriesRef.current) setSelectedSeries(null);
    setWorkbench(null);
    if (healing?.status === "healed" && healing.healedFileCount > 1) {
      showToast(`${message} · ${healing.healedFileCount} connected files updated`);
    } else if (healing?.status === "review_required") {
      showToast(`${message} · connected files kept separate for review`);
    } else {
      showToast(message);
    }
  }
  async function saveFileMetadata(fields, lockedFields) {
    setWorkbenchBusy(true); setWorkbenchError("");
    try {
      const result = await apiRequest(`/api/v1/files/${workbench.data.file.id}/metadata`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fields, lockedFields }) });
      await finishMetadataChange("Metadata corrected and locked", result);
    } catch (error) { setWorkbenchError(error.message); }
    setWorkbenchBusy(false);
  }
  async function applyFileMatch(candidateKey) {
    setWorkbenchBusy(true); setWorkbenchError("");
    try {
      const result = await apiRequest(`/api/v1/files/${workbench.data.file.id}/match`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ candidateKey }) });
      await finishMetadataChange("Match replaced and locked", result);
    } catch (error) { setWorkbenchError(error.message); }
    setWorkbenchBusy(false);
  }
  async function searchFileMatches(query) {
    setWorkbenchBusy(true); setWorkbenchError("");
    try {
      const data = await apiRequest(`/api/v1/files/${workbench.data.file.id}/candidates/search`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query }),
      });
      setWorkbench((current) => current ? { ...current, data } : current);
    } catch (error) { setWorkbenchError(error.message); }
    setWorkbenchBusy(false);
  }
  async function resetFileMetadata() {
    setWorkbenchBusy(true); setWorkbenchError("");
    try {
      await apiRequest(`/api/v1/files/${workbench.data.file.id}/metadata/reset`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await finishMetadataChange("Original provider metadata restored");
    } catch (error) { setWorkbenchError(error.message); }
    setWorkbenchBusy(false);
  }
  function openIssueWorkbench(issue) {
    setIssueError("");
    setIssueWorkbench(issue);
  }
  async function finishIssueMetadataChange(message) {
    const selectedSeriesId = selectedSeries?.id;
    const selectedCollectionId = selectedCollection?.id;
    const data = await loadCatalog();
    if (selectedSeriesId) {
      const refreshedSeries = data?.series?.find((item) => item.id === selectedSeriesId);
      refreshOpenSeries(refreshedSeries);
    }
    if (selectedCollectionId) {
      const refreshedCollection = data?.families?.find((item) => item.id === selectedCollectionId);
      if (refreshedCollection) setSelectedCollection(refreshedCollection);
    }
    setIssueWorkbench(null);
    showToast(message);
  }
  async function saveIssueMetadata(fields) {
    setIssueBusy(true); setIssueError("");
    try {
      await apiRequest(`/api/v1/issues/${issueWorkbench.id}/metadata`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fields),
      });
      await finishIssueMetadataChange("Issue metadata corrected and locked");
    } catch (error) { setIssueError(error.message); }
    setIssueBusy(false);
  }
  async function resetIssueMetadata() {
    setIssueBusy(true); setIssueError("");
    try {
      await apiRequest(`/api/v1/issues/${issueWorkbench.id}/metadata/reset`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      await finishIssueMetadataChange("Provider issue metadata restored");
    } catch (error) { setIssueError(error.message); }
    setIssueBusy(false);
  }
  // A reader's Follow and Pull ask the admin instead. The answer is a
  // request waiting (or, for a reader the admin trusts, started at once), and
  // the caller's card settles on "Requested".
  async function askAdmin(body) {
    try {
      const result = await apiRequest("/api/v1/member-requests", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      await loadCatalog();
      if (result.status === "already") {
        showToast(result.message);
        return { ok: true, result };
      }
      const started = result.status === "approved";
      const follow = isFollow(result);
      showToast(started ? (follow ? `Following ${result.title}` : `${result.title} · on its way`)
        : follow ? `Asked to follow ${result.title} · waiting for approval` : `${result.title} requested · waiting for approval`);
      return { ok: true, result, requested: !started };
    } catch (error) {
      showToast(error.message, "error");
      return { ok: false, error: error.message };
    }
  }
  // Approve, decline or cancel a reader's request. Returns an error to show on
  // the card, or nothing.
  async function decideMemberRequest(request, action, body) {
    try {
      const result = await apiRequest(`/api/v1/member-requests/${request.id}/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
      });
      await loadCatalog();
      if (action === "approve") {
        showToast(result.status === "failed" ? `${request.title} couldn't be added · ${result.failure || "try again"}`
          : `${request.title} approved · on its way`, result.status === "failed" ? "error" : undefined);
      } else {
        showToast(action === "decline" ? `${request.title} declined` : `${request.title} request cancelled`);
      }
      // A failed approval is on the card itself (status and reason), so it is
      // not said again beneath it.
      return "";
    } catch (error) {
      await loadCatalog();
      return error.message;
    }
  }
  async function createAcquisitionRequest(target) {
    const collection = target?.isCollectionSeries ? target.collection : target;
    const isCollection = Boolean(collection?.runs);
    const requestKey = `${isCollection ? "collection" : "series"}:${collection.id}`;
    if (!viewerIsAdmin) {
      setRequestBusyKey(requestKey);
      const asked = await askAdmin(isCollection ? { kind: "collection", collectionId: collection.id }
        : { kind: "run", seriesId: collection.id });
      setRequestBusyKey("");
      return asked;
    }
    setRequestBusyKey(requestKey);
    try {
      const request = await apiRequest("/api/v1/requests", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scopeType: isCollection ? "collection" : "series",
          scopeId: collection.id,
          acquisitionPreference: collection.acquisitionPreference || "either",
          includeSpecials: collection.includeSpecials ?? true,
        }),
      });
      const data = await loadCatalog();
      if (isCollection) {
        const refreshedCollection = data?.families?.find((item) => String(item.id) === String(collection.id));
        if (refreshedCollection && selectedCollection) setSelectedCollection(refreshedCollection);
      } else {
        const refreshedSeries = data?.series?.find((item) => String(item.id) === String(collection.id));
        refreshOpenSeries(refreshedSeries);
      }
      const wantedCount = Number(request.wantedIssueCount || 0);
      showToast(wantedCount
        ? `Following ${request.title} · ${wantedCount} missing issue${wantedCount === 1 ? "" : "s"} added to Wanted`
        : `Following ${request.title} · you’re up to date`);
      return { ok: true, request };
    } catch (error) {
      showToast(error.message, "error");
      return { ok: false, error: error.message };
    } finally {
      setRequestBusyKey("");
    }
  }
  async function pullDiscoveredIssue(issue) {
    if (!viewerIsAdmin) {
      return askAdmin({ kind: "discover_issues", provider: "metron", providerSeriesId: issue.providerSeriesId,
        providerIssueId: issue.providerIssueId,
        numbers: [issue.number], title: issue.seriesTitle || issue.title, query: issue.seriesTitle,
        publisher: issue.publisher, cover: issue.cover });
    }
    try {
      const result = await apiRequest("/api/v1/discover/pull-issue", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: "metron",
          providerSeriesId: issue.providerSeriesId,
          providerIssueId: issue.providerIssueId,
          number: issue.number,
          seriesTitle: issue.seriesTitle,
        }),
      });
      // A pull starts real work, and the catalog poll only runs while
      // something is active -- so without this the Pull List does not know
      // about it until something else happens to refresh.
      await loadCatalog();
      showToast(`${issue.title} added to your Pull List`);
      return { ok: true, result };
    } catch (error) {
      showToast(error.message, "error");
      return { ok: false, error: error.message };
    }
  }

  async function pullDiscoveredIssues({ provider, providerSeriesId, numbers, released, title, query, publisher, cover, year }) {
    if (!viewerIsAdmin) {
      return askAdmin({ kind: "discover_issues", provider, providerSeriesId, numbers, released: Boolean(released),
        title, query: query || title, publisher, cover, year });
    }
    try {
      const result = await apiRequest("/api/v1/discover/pull-issue", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider, providerSeriesId, numbers, released: Boolean(released),
          seriesTitle: query || title,
        }),
      });
      await loadCatalog();
      const count = (result.numbers || []).length;
      showToast(`${title} · ${count} issue${count === 1 ? "" : "s"} added to your Pull List`);
      return { ok: true, result };
    } catch (error) {
      showToast(error.message, "error");
      return { ok: false, error: error.message };
    }
  }

  async function pullStoryArc({ arcId, name, cover, issueCount }) {
    if (!viewerIsAdmin) {
      return askAdmin({ kind: "discover_arc", provider: "metron", arcId, title: name, cover, issueCount });
    }
    try {
      const result = await apiRequest("/api/v1/discover/pull-arc", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ arcId }),
      });
      await loadCatalog();
      // Pulling saved the arc as well, so the Comics grid gains it.
      setListsVersion((version) => version + 1);
      const failed = (result.failed || []).map((item) => item.title).filter(Boolean);
      showToast(`${name} · ${result.count} issue${result.count === 1 ? "" : "s"} added to your Pull List${failed.length ? ` · ${failed.join(", ")} could not be pulled` : ""}`,
        failed.length ? "error" : undefined);
      return { ok: true, result };
    } catch (error) {
      showToast(error.message, "error");
      return { ok: false, error: error.message };
    }
  }
  async function requestDiscoveredSeries(target) {
    if (!viewerIsAdmin) {
      return askAdmin({ kind: "discover_run", provider: target.provider, providerSeriesId: target.providerSeriesId,
        title: target.title, query: target.query || target.title, publisher: target.publisher,
        cover: target.cover, year: Number.isInteger(target.year) ? target.year : undefined });
    }
    try {
      const result = await apiRequest("/api/v1/discover", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: target.query,
          provider: target.provider,
          providerSeriesId: target.providerSeriesId,
          acquisitionPreference: "either",
        }),
      });
      await loadCatalog();
      // Staying put is the point: the card settles into "On your Pull List" so
      // the reader can keep browsing the results they were reading.
      showToast(`${result.series.title} added · ${result.issueCount} issues followed`);
      return { ok: true, result };
    } catch (error) {
      showToast(error.message, "error");
      return { ok: false, error: error.message };
    }
  }
  function openReplacementRequest(file) {
    setReplacementError("");
    setReplacementFile({ ...file, id: file.fileId || file.id, filename: file.filename || file.file });
  }
  async function createFileReplacement(options) {
    setReplacementBusy(true); setReplacementError("");
    try {
      const selectedSeriesId = selectedSeries?.id;
      const request = await apiRequest(`/api/v1/files/${replacementFile.id}/replacement`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(options),
      });
      const data = await loadCatalog();
      if (selectedSeriesId) {
        const refreshed = data?.series?.find((item) => item.id === selectedSeriesId);
        refreshOpenSeries(refreshed);
      }
      setReplacementFile(null);
      showToast(`Replacement requested for ${request.targetTitle}`);
    } catch (error) { setReplacementError(error.message); }
    setReplacementBusy(false);
  }
  async function cancelFileReplacement(request) {
    try {
      await apiRequest(`/api/v1/replacements/${request.id}/status`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "cancelled" }),
      });
      await loadCatalog();
      showToast(`Replacement cancelled for ${request.targetTitle}`);
    } catch (error) { showToast(error.message, "error"); }
  }
  // Deleted, not cancelled: a pull is one click to make again, and a cancelled
  // row would sit on the Pull List as a comic nobody wants.
  async function deletePull(request, issue) {
    try {
      await apiRequest(issue
        ? `/api/v1/requests/${request.id}/issues/${issue.id}`
        : `/api/v1/requests/${request.id}`, { method: "DELETE" });
      await loadCatalog();
      showToast(issue
        ? `Deleted #${issue.number} from your Pull List`
        : `Deleted ${request.title} from your Pull List`);
    } catch (error) { showToast(error.message, "error"); }
  }
  async function setSeriesFormat(series, format) {
    try {
      await apiRequest(`/api/v1/series/${series.id}/format`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ format }),
      });
      await loadCatalog();
      showToast(format === "manga" ? `${series.title} is filed as manga` : `${series.title} is filed as a comic`);
    } catch (error) { showToast(error.message, "error"); }
  }
  /**
   * Which way this run's pages turn, whatever it is filed as.
   *
   * null restores "follow the medium". The format goes along for the ride
   * because the route sets both, and sending the run's current one leaves it
   * where it is.
   */
  /** Your rating for a run or one of its issues; null takes it back. */
  async function rateSeries(series, rating) {
    await saveRating(`/api/v1/series/${series.id}/rating`, rating, series.title);
  }
  async function rateIssue(issue, rating) {
    await saveRating(`/api/v1/issues/${issue.id}/rating`, rating,
      `${issue.contextLabel || "Issue"} #${issue.number}`);
  }
  async function saveRating(path, rating, subject) {
    try {
      await apiRequest(path, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === selectedSeries?.id);
      refreshOpenSeries(refreshed);
      showToast(rating ? `${subject} rated ${rating} star${rating === 1 ? "" : "s"}` : `Rating removed from ${subject}`);
    } catch (error) { showToast(error.message, "error"); }
  }
  async function setSeriesAgeRating(series, rating) {
    try {
      await apiRequest(`/api/v1/series/${series.id}/age-rating`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rating }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === series.id);
      refreshOpenSeries(refreshed);
      showToast(rating ? `${series.title} rated ${RATING_LABELS[rating]}` : `${series.title} uses the rating found for it`);
    } catch (error) { showToast(error.message, "error"); }
  }
  async function setSeriesDirection(series, direction) {
    try {
      const saved = await apiRequest(`/api/v1/series/${series.id}/format`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ format: series.medium, readingDirection: direction }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === series.id);
      refreshOpenSeries(refreshed);
      showToast(saved.readingDirection
        ? `${series.title} reads ${saved.readingDirection === "rtl" ? "right to left" : "left to right"}`
        : `${series.title} follows its medium again`);
    } catch (error) { showToast(error.message, "error"); }
  }
  // Irreversible, so it says exactly what goes -- files and space -- before
  // it asks, and the server checks every path again before deleting any.
  async function removeSeries(series) {
    try {
      const plan = await apiRequest(`/api/v1/series/${series.id}/removal`);
      if (plan.activeDownloads) {
        showToast("Something for this run is still downloading. Remove it once that has finished.", "error");
        return;
      }
      const size = plan.sizeBytes >= 1e9 ? `${(plan.sizeBytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(plan.sizeBytes / 1e6))} MB`;
      const files = plan.fileCount
        ? `This permanently deletes ${plan.fileCount} comic file${plan.fileCount === 1 ? "" : "s"} (${size}) from disk.`
        : "It has no comic files on disk.";
      if (!window.confirm(`Remove ${plan.title} from your library?\n\n${files} Its issue list and requests go too. This cannot be undone.`)) return;
      const result = await apiRequest(`/api/v1/series/${series.id}`, { method: "DELETE" });
      setSelectedSeries(null);
      await loadCatalog();
      showToast(result.filesDeleted
        ? `Removed ${plan.title} and deleted ${result.filesDeleted} file${result.filesDeleted === 1 ? "" : "s"}`
        : `Removed ${plan.title}`);
    } catch (error) { showToast(error.message, "error"); }
  }
  async function openCoverWorkbench(file) {
    setCoverError("");
    try {
      const data = await apiRequest(`/api/v1/files/${file.id}`);
      setCoverWorkbench(data);
    } catch (error) { showToast(error.message, "error"); }
  }
  async function finishCoverChange(message) {
    const selectedId = selectedSeries?.id;
    const data = await loadCatalog();
    const refreshed = data?.series?.find((item) => item.id === selectedId);
    refreshOpenSeries(refreshed);
    setCoverWorkbench(null);
    showToast(message);
  }
  async function selectFileCover(source, url) {
    setCoverBusy(true); setCoverError("");
    try {
      await apiRequest(`/api/v1/files/${coverWorkbench.file.id}/cover`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source, url }) });
      await finishCoverChange(source === "auto" ? "Automatic cover selection restored" : "Preferred cover updated");
    } catch (error) { setCoverError(error.message); }
    setCoverBusy(false);
  }
  async function uploadFileCover(file) {
    setCoverBusy(true); setCoverError("");
    try {
      await apiRequest(`/api/v1/files/${coverWorkbench.file.id}/cover/upload`, { method: "POST", headers: { "Content-Type": file.type }, body: file });
      await finishCoverChange("Uploaded cover saved");
    } catch (error) { setCoverError(error.message); }
    setCoverBusy(false);
  }
  // `runId` is passed by the drawer's Edit panel, which hosts the same picker
  // without opening the workbench these were written for.
  async function saveSeriesBackdrop(body, message, runId = backdropWorkbench?.series?.id) {
    if (!runId) return;
    setBackdropBusy(true); setBackdropError("");
    try {
      await apiRequest(`/api/v1/series/${runId}/backdrop`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setBackdropVersion((value) => value + 1);
      setBackdropWorkbench(null);
      showToast(message);
    } catch (error) { setBackdropError(error.message); }
    setBackdropBusy(false);
  }
  async function openSeriesCoverWorkbench(series) {
    setCoverError("");
    try {
      setSeriesCoverWorkbench(await apiRequest(`/api/v1/series/${series.id}/cover`));
    } catch (error) { showToast(error.message, "error"); }
  }
  async function finishSeriesCoverChange(message) {
    const selectedId = selectedSeries?.id;
    const data = await loadCatalog();
    const refreshed = data?.series?.find((item) => item.id === selectedId);
    refreshOpenSeries(refreshed);
    setSeriesCoverWorkbench(null);
    showToast(message);
  }
  async function selectSeriesCover(source, url, fileId, runId = seriesCoverWorkbench?.series?.id) {
    setCoverBusy(true); setCoverError("");
    try {
      await apiRequest(`/api/v1/series/${runId}/cover`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source, url, fileId }) });
      await finishSeriesCoverChange(source === "auto" ? "Automatic cover selection restored" : "Series cover updated");
    } catch (error) { setCoverError(error.message); }
    setCoverBusy(false);
  }
  async function uploadSeriesCover(file, runId = seriesCoverWorkbench?.series?.id) {
    setCoverBusy(true); setCoverError("");
    try {
      await apiRequest(`/api/v1/series/${runId}/cover/upload`, { method: "POST", headers: { "Content-Type": file.type }, body: file });
      await finishSeriesCoverChange("Uploaded cover saved");
    } catch (error) { setCoverError(error.message); }
    setCoverBusy(false);
  }
  async function unfollowSeries(series) {
    setUnfollowBusy(true);
    try {
      await apiRequest(`/api/v1/series/${series.id}/monitoring`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ monitored: false }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === series.id);
      refreshOpenSeries(refreshed);
      showToast(`No longer following ${series.title}`);
    } catch (error) { showToast(error.message, "error"); }
    setUnfollowBusy(false);
  }
  // From a Discover drawer: the run as the library knows it, by its id.
  async function unfollowDiscoveredRun({ runId, title }) {
    if (!runId) return { ok: false };
    try {
      await apiRequest(`/api/v1/series/${runId}/monitoring`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ monitored: false }),
      });
      await loadCatalog();
      showToast(`No longer following ${title}`);
      return { ok: true };
    } catch (error) {
      showToast(error.message, "error");
      return { ok: false, error: error.message };
    }
  }
  async function unfollowCollection(collection) {
    setUnfollowBusy(true);
    try {
      await apiRequest(`/api/v1/collections/${collection.id}/monitoring`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ monitored: false }),
      });
      const data = await loadCatalog();
      const refreshed = data?.families?.find((item) => item.id === collection.id);
      if (refreshed) setSelectedCollection(refreshed);
      showToast(`No longer following ${collection.name}`);
    } catch (error) { showToast(error.message, "error"); }
    setUnfollowBusy(false);
  }
  async function openSeriesMatchWorkbench(series) {
    setMatchError(""); setMatchBusy("");
    setMatchWorkbench({ series, candidates: [], query: series.title });
    setMatchLoading(true);
    try {
      setMatchWorkbench(await apiRequest(`/api/v1/series/${series.id}/match-candidates`));
    } catch (error) { setMatchError(error.message); }
    setMatchLoading(false);
  }
  async function searchSeriesMatches(query) {
    setMatchError(""); setMatchLoading(true);
    try {
      const data = await apiRequest(
        `/api/v1/series/${matchWorkbench.series.id}/match-candidates?` +
        new URLSearchParams({ query }).toString()
      );
      setMatchWorkbench(data);
    } catch (error) { setMatchError(error.message); }
    setMatchLoading(false);
  }
  async function confirmSeriesMatch(item) {
    const seriesId = matchWorkbench.series.id;
    setMatchBusy(`${item.provider || matchWorkbench.provider}-${item.providerSeriesId}`);
    setMatchError("");
    try {
      const result = await apiRequest(`/api/v1/series/${seriesId}/match`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: item.provider || matchWorkbench.provider,
          providerSeriesId: item.providerSeriesId,
          query: matchWorkbench.query,
        }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((entry) => entry.id === seriesId);
      refreshOpenSeries(refreshed);
      setMatchWorkbench(null);
      showToast(`Matched to ${result.matchedTitle || item.title}`);
    } catch (error) { setMatchError(error.message); }
    setMatchBusy("");
  }
  async function openContentsWorkbench(file) {
    setContentsError("");
    try {
      const data = await apiRequest(`/api/v1/files/${file.id}`);
      if (!data.collectionContents) throw new Error("This file is not linked to a volume");
      setContentsWorkbench(data);
    } catch (error) { showToast(error.message, "error"); }
  }
  async function finishContentsChange(data, message) {
    const selectedId = selectedSeries?.id;
    const selectedCollectionId = selectedCollection?.id;
    const catalogData = await loadCatalog();
    const refreshed = catalogData?.series?.find((item) => item.id === selectedId);
    refreshOpenSeries(refreshed);
    const refreshedCollection = catalogData?.families?.find((item) => item.id === selectedCollectionId);
    if (refreshedCollection) setSelectedCollection(refreshedCollection);
    setContentsWorkbench(data);
    showToast(message);
  }
  async function changeCollectionContents(change) {
    setContentsBusy(true); setContentsError("");
    try {
      const data = await apiRequest(`/api/v1/files/${contentsWorkbench.file.id}/contents`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(change) });
      await finishContentsChange(data, change.included ? "Volume contents updated" : "Issue excluded from volume");
    } catch (error) { setContentsError(error.message); }
    setContentsBusy(false);
  }
  async function resetCollectionContents() {
    setContentsBusy(true); setContentsError("");
    try {
      const data = await apiRequest(`/api/v1/files/${contentsWorkbench.file.id}/contents/reset`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await finishContentsChange(data, "Provider volume contents restored");
    } catch (error) { setContentsError(error.message); }
    setContentsBusy(false);
  }
  useEffect(() => { loadCatalog(); }, []);
  useEffect(() => { loadSetupState(); }, []);
  // Write the current view into the address bar so a refresh, a bookmark or a
  // shared link lands where the user actually was. Held back while a ?series=
  // link is still waiting on the catalog, or this would strip the parameter
  // before it could be resolved.
  const locationRef = useRef(window.location.pathname + window.location.search);
  useEffect(() => {
    if (pendingSeriesId) return;
    const target = locationForState({
      active, settingsSection, libraryTab, searchQuery: viewQuery, seriesId: selectedSeries?.id, readFileId,
    });
    locationRef.current = target;
    // Pushed, written over, or Back: history-step.js, which also keeps the
    // reader's and drawer's entries from being left behind when they close.
    // On Back, popstate finds the address already matching (locationRef is set
    // above), so the edge gesture's guard is not asked about it.
    const step = historyStep(window.location.pathname + window.location.search, target, window.history.state);
    if (step.kind === "back") window.history.back();
    else if (step.kind === "replace") window.history.replaceState(step.state, "", target);
    else if (step.kind === "push") window.history.pushState(step.state, "", target);
  }, [active, settingsSection, libraryTab, viewQuery, selectedSeries?.id, readFileId, pendingSeriesId]);
  // Back and forward move between views, and close the drawer when the entry
  // being returned to did not have it open.
  //
  // Except when Back was not meant: on a phone the reader's "previous" tap
  // zone runs to the screen's left edge, and iOS takes a tap there that
  // drifts as its own Back gesture -- the touch is cancelled, the entry
  // popped, and the reader gone mid-issue. An edge touch the system
  // cancelled a moment before the pop is that gesture; the reader keeps its
  // entry and stays. The close button, Esc, a pull down and a Back with no
  // edge touch behind it still close it.
  //
  // An edge touch that ends normally counts too. Since the reader takes a
  // swipe to the end (2026-10-07), iOS no longer cancels the touch: the
  // reader turns the page, the finger lifts, and Safari's Back lands after
  // it. In the iOS 27 simulator a swipe from the edge closed the reader
  // that way (2026-10-08).
  const edgeTouch = useRef({ startedAt: 0, cancelledAt: 0 });
  useEffect(() => {
    function onPointerDown(event) {
      if (event.pointerType === "mouse") return;
      edgeTouch.current.startedAt = isEdgeTouch(event.clientX, window.innerWidth) ? Date.now() : 0;
    }
    function onPointerEnd() {
      if (edgeTouch.current.startedAt) edgeTouch.current.cancelledAt = Date.now();
    }
    window.addEventListener("pointerdown", onPointerDown, { passive: true, capture: true });
    window.addEventListener("pointercancel", onPointerEnd, { passive: true, capture: true });
    window.addEventListener("pointerup", onPointerEnd, { passive: true, capture: true });
    return () => {
      window.removeEventListener("pointerdown", onPointerDown, { capture: true });
      window.removeEventListener("pointercancel", onPointerEnd, { capture: true });
      window.removeEventListener("pointerup", onPointerEnd, { capture: true });
    };
  }, []);
  useEffect(() => {
    function applyLocation() {
      const next = stateFromLocation(window.location.pathname, window.location.search);
      const reading = new URLSearchParams(locationRef.current.split("?")[1] || "").get("read");
      if (reading && !next.readFileId && isStolenBack(edgeTouch.current.cancelledAt, Date.now())) {
        edgeTouch.current.cancelledAt = 0;
        // Put back as it was opened: remembering the page it came from, so
        // closing it later is still a Back (the location effect).
        window.history.pushState({ from: window.location.pathname + window.location.search }, "", locationRef.current);
        return;
      }
      // An entry marked for a dialog nobody has open -- development's double
      // mount leaves one, a reload keeps one -- is not a place; Back goes on.
      const mark = window.history.state?.dialog;
      if (mark && !openDialogs.some((node) => dialogMarks.get(node) === mark)) {
        window.history.back();
        return;
      }
      // Back took the dialog in front's own entry: the dialog closes and the
      // page stays, its address untouched. Only if Back went further -- to
      // another page -- does the location apply as well.
      if (dialogLeftByBack()) {
        closeTopDialog();
        if (window.location.pathname + window.location.search === locationRef.current) return;
      }
      setActive(next.active);
      setSettingsSection(next.settingsSection);
      setLibraryTab(next.libraryTab);
      setSearchQuery(next.searchQuery);
      setReadFileId(next.readFileId);
      if (next.seriesId) setPendingSeriesId(next.seriesId);
      else { setPendingSeriesId(""); setDrawerDismissSignal((count) => count + 1); }
      canonicaliseLocation(next);
    }
    // An address that parses to a view but is not how that view is spelled --
    // an unknown path, or a settings section that does not exist -- renders
    // correctly but would otherwise leave the wrong text in the address bar,
    // so a bookmark of a typo stays a typo. Replace rather than push: this is
    // a correction, not somewhere the user navigated to.
    function canonicaliseLocation(next) {
      const canonical = locationForState(next);
      if (canonical !== window.location.pathname + window.location.search) {
        window.history.replaceState(null, "", canonical);
      }
    }
    canonicaliseLocation(BOOT_ROUTE);
    window.addEventListener("popstate", applyLocation);
    return () => window.removeEventListener("popstate", applyLocation);
  }, []);
  // A settings section opens at its top, and the list is at its top on the
  // way back: a long section left scrolled would otherwise hand its offset
  // to the short list, which then sat under the header.
  useEffect(() => { window.scrollTo(0, 0); }, [settingsSection]);
  // Pull to refresh, for the app on a Home Screen: installed, it has no
  // browser chrome and so none of Safari's reload -- the pull was never the
  // page's. This one rides the phone's own rubber band: while a finger is
  // down at the top, the stretch it reports is the pull's depth, past a
  // threshold the pill says so, and letting go there reloads. Nothing is
  // moved by hand, so nothing repaints per frame. A Safari tab keeps its
  // own, and a page a dialog holds is left alone.
  const [pull, setPull] = useState("idle");
  useEffect(() => {
    const installed = window.matchMedia?.("(display-mode: standalone)").matches || window.navigator.standalone === true;
    if (!installed) return undefined;
    let pulling = false;
    let armed = false;
    const onStart = () => {
      pulling = window.scrollY <= 0 && !document.documentElement.classList.contains("page-scroll-locked");
      armed = false;
    };
    const onScroll = () => {
      if (!pulling) return;
      const past = -window.scrollY >= PULL_REFRESH_PX;
      if (past !== armed) { armed = past; setPull(past ? "armed" : "idle"); }
    };
    const onEnd = () => {
      if (!pulling) return;
      pulling = false;
      if (!armed) { setPull("idle"); return; }
      setPull("refreshing");
      window.location.reload();
    };
    window.addEventListener("touchstart", onStart, { passive: true });
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("touchend", onEnd, { passive: true });
    window.addEventListener("touchcancel", onEnd, { passive: true });
    return () => {
      window.removeEventListener("touchstart", onStart);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("touchend", onEnd);
      window.removeEventListener("touchcancel", onEnd);
    };
  }, []);
  // Resolve a ?series= link once the catalog is in. An id that no longer exists
  // simply clears, and the effect above then tidies it out of the URL.
  useEffect(() => {
    if (!pendingSeriesId || !catalog) return;
    const match = (catalog.series || []).find((item) => String(item.id) === String(pendingSeriesId));
    if (match) setSelectedSeries(match);
    setPendingSeriesId("");
  }, [pendingSeriesId, catalog]);
  useEffect(() => {
    const acquisitionEntries = [...(catalog?.requests || []), ...(catalog?.replacementRequests || [])];
    // `isWorking` rather than a fourth copy of the downloadStatus list: a job
    // that is queued or out searching has no downloadStatus yet, so the list
    // was blind to precisely the stretch a reader watches.
    const activeDownload = acquisitionEntries.some(isWorking);
    // A scan counts too. Polling was tied to enrichment and downloads only, so
    // a page opened during a scan sat still until something else woke it.
    const scanning = Boolean(catalog?.activeScan);
    if (!(catalog?.enrichment?.active > 0) && !activeDownload && !scanning) return undefined;
    const timer = window.setInterval(() => loadCatalog(), scanning ? 2000 : 5000);
    return () => window.clearInterval(timer);
  }, [catalog?.enrichment?.active, catalog?.requests, catalog?.replacementRequests, catalog?.activeScan]);
  const visibleSeries = catalog?.series ?? (backendStatus === "offline" ? DEMO_SERIES : []);
  const runCollectionList = useMemo(() => collectionCards(catalog?.runCollections, catalog?.series || []), [catalog?.runCollections, catalog?.series]);
  const runCollectionCard = runCollectionId ? runCollectionList.find((item) => String(item.id) === String(runCollectionId)) || null : null;
  const collectionPickerCard = collectionPickerId ? runCollectionList.find((item) => String(item.id) === String(collectionPickerId)) || null : null;
  const logicalSeries = useMemo(() => logicalCatalogSeries(catalog, visibleSeries), [catalog, backendStatus]);
  const logicalSeriesCount = logicalSeries.length;
  async function switchToProfile(profile) {
    // A profile that asks for something asks on the picker; one that does
    // not opens at once.
    if (isLocked(profile)) { setPicker({ ask: profile }); return; }
    try {
      const result = await apiRequest("/api/v1/profiles/switch", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ userId: profile.id }),
      });
      enterProfile(result.viewer.id);
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  const header = {
    bell,
    query: SEARCH_VIEWS.has(active) ? viewQuery : "", onSearch: openSearch, onClearSearch: () => openSearch(""),
    search: {
      draft: searchDraft, catalogs: can(authStatus?.viewer, "discover.search"),
      onDraft: (value) => { setSearchDraft(value); goSearch(); },
      onSubmit: () => openSearch(searchDraft), onClear: leaveSearch, onOpen: goSearch,
    },
    profile: {
      household: Boolean(authStatus?.household), method: authStatus?.method, version: profilesVersion,
      onOpenProfile: () => navigate("profile"), onSwitchTo: switchToProfile, onPicker: () => setPicker({}),
      onAdd: () => setAddingProfile(true), onSignOut: () => signOut({ forgetDevice: true }),
    },
  };
  // What is being read, named: the run it belongs to and the file's own name.
  const readingSeries = readFileId
    ? (catalog?.series || []).find((item) => (item.fileDetails || []).some((file) => String(file.id) === String(readFileId)))
    : null;
  const readingTitle = readFileId
    ? (readingSeries?.fileDetails || []).find((file) => String(file.id) === String(readFileId))?.filename || readingSeries?.title || "Reading"
    : "";
  // What the reader needs when a comic ends: the issue it was, so it can be
  // rated, and the one after it, so finishing is not a dead end.
  const readingIssues = readingSeries?.issues || [];
  const readingIssue = readFileId
    ? readingIssues.find((issue) => String(issue.fileId) === String(readFileId)) || null
    : null;
  const readingNext = readingIssue
    ? readingIssues.slice(readingIssues.indexOf(readingIssue) + 1).find((issue) => issue.fileId) || null
    : null;
  const finished = Boolean(readFileId) && finishedFileId === readFileId;
  const navActive = active === "import" ? "settings" : active;
  useEffect(() => { loadAuthStatus(); }, []);
  // A shared device with more than one profile asks who is reading.
  if (authStatus?.profileRequired) {
    return <WhoIsReadingView signInAvailable={authStatus.method === "forms"} />;
  }
  if (authStatus && authStatus.method === "forms" && !authStatus.authenticated) {
    return <LoginView onSignedIn={(result) => enterProfile(result?.viewer?.id ?? null)} />;
  }
  // Setup takes the whole screen: there is nothing useful in the nav until a
  // library folder exists. "Set up later" defers for this session only rather
  // than marking the instance configured, since it is not.
  //
  // An existing install upgrading into this version has setupCompleted false
  // simply because the setting did not exist when it was configured. A library
  // root is proof that the one required step was already done, so wait for the
  // catalog and treat any configured root as setup already finished.
  const setupOutstanding = setupCompleted === false
    && catalog !== null
    && !(catalog?.roots || []).length;
  if (setupOutstanding) {
    return <SetupView catalog={catalog} onFinish={finishSetup} />;
  }
  return <Host.Provider value={readerHost}><ViewerContext.Provider value={authStatus?.viewer || null}><CollectedEditionsContext.Provider value={Boolean(catalog?.collectedEditionsEnabled)}><HeaderContext.Provider value={header}><div className="app-shell"><Nav active={navActive} onNavigate={navigate} catalog={catalog} backendStatus={backendStatus} logicalSeriesCount={logicalSeriesCount} authStatus={authStatus} onSignOut={signOut} scanning={scanState === "scanning" || Boolean(catalog?.activeScan)} onScanLibrary={() => scanLibrary()} /><main className="main-content"><div className="page-view" key={active}>{catalog?.collectedEditionsEnabled ? <div className="collected-editions-notice"><WarningCircle size={17} weight="fill" /> <span>Collected-edition support is on. Trades, hardcovers and omnibuses have less complete metadata and file availability than Issues, and never fulfill Issue ownership or acquisition.</span></div> : null}{active === "profile" ? <ProfileView onRead={readComic} onNavigate={navigate} authStatus={authStatus} onSwitch={() => setPicker({})} /> : null}{active === "library" ? <LibraryView readingList={readingListEntries} onToggleReadingList={toggleReadingList} onOpenRunCollection={(card) => setRunCollectionId(String(card.id))} onNewRunCollection={() => setNewCollectionOpen(true)} tab={libraryTab} onTab={(next) => { setLibraryTab(next); window.scrollTo({ top: 0 }); }} onNavigate={navigate} onOpenSeries={openSeries} onOpenCollection={openCollection} onOpenList={openList} onImportList={() => setImportingList(true)} onNewArc={() => setNewArcOpen(true)} onSearch={openSearch} onRead={readComic} readingVersion={readingVersion} listsVersion={listsVersion} catalog={catalog} backendStatus={backendStatus} /> : null}{active === "discover" && !can(authStatus?.viewer, "nav.discover") ? <><PageHeader title="Discover" /><div className="empty-state"><MagnifyingGlass size={35} weight="duotone" /><strong>Discover is off for this profile</strong><span>New comics come through the admin.</span></div></> : null}{active === "search" || (active === "discover" && can(authStatus?.viewer, "nav.discover")) ? <DiscoverView onImportCommunity={viewerIsAdmin ? importCommunityList : undefined} key={active} mode={active} query={viewQuery} draft={searchDraft} onDraft={setSearchDraft} onLeaveSearch={leaveSearch} runCollections={runCollectionList} onOpenRunCollection={(card) => setRunCollectionId(String(card.id))} onOpenList={openList} catalog={catalog} backendStatus={backendStatus} onSearch={openSearch} onClearSearch={() => openSearch("")} onOpenSeries={openSeries} onOpenCollection={openCollection} onDiscoverRequest={requestDiscoveredSeries} onUnfollowRun={unfollowDiscoveredRun} onPullIssue={pullDiscoveredIssue} onPullIssues={pullDiscoveredIssues} onPullArc={pullStoryArc} onSaveArc={saveStoryArc} onReadArc={readComic} /> : null}{active === "import" ? <ImportLibraryView onNavigate={navigate} onStartInventory={scanLibrary} onScanLibrary={() => scanLibrary()} onUpdateRoot={updateLibraryRoot} onRemoveRoot={removeLibraryRoot} catalog={catalog} backendStatus={backendStatus} scanState={scanState} scanProgress={scanProgress} /> : null}{active === "requests" ? (viewerIsAdmin ? <RequestsView catalog={catalog} backendStatus={backendStatus} focus={requestFocus} onCancelReplacement={cancelFileReplacement} onDeletePull={deletePull} onRefresh={loadCatalog} onDecide={decideMemberRequest} /> : <MyRequestsView catalog={catalog} backendStatus={backendStatus} focus={requestFocus} onDecide={decideMemberRequest} />) : null}{active === "settings" ? <SettingsView catalog={catalog} backendStatus={backendStatus} logicalSeriesCount={logicalSeriesCount} onNavigate={navigate} onAuthChanged={loadAuthStatus} onSignOut={signOut} authStatus={authStatus} section={settingsSection} onSectionChange={setSettingsSection} health={{ items: catalog?.inbox ?? [], loading: catalogPending(catalog, backendStatus), focus: reviewFocus, backendStatus, onResolve: resolveReview, onReplace: openReplacementRequest }} onScanLibrary={() => scanLibrary()} scanState={scanState} scanProgress={scanProgress} onCatalogChanged={loadCatalog} /> : null}</div></main>{readFileId ? <ReaderView fileId={readFileId} title={readingTitle} medium={readingSeries?.medium} directionOverride={readingSeries?.readingDirection} startPage={readFrom} behind={Boolean(selectedSeries) || Boolean(selectedList) || finished} onFinish={() => setFinishedFileId(readFileId)} onProgressSaved={noteReadingChanged} onOpenRun={readingSeries ? () => openSeries(readingSeries) : undefined} onClose={() => { setReadFileId(""); setReadFrom(null); setFinishedFileId(""); setReadingList(null); }} /> : null}{finished ? <FinishDrawer key={readFileId} fileId={readFileId} series={readingSeries} issue={readingIssue} nextIssue={readingNext} medium={readingSeries?.medium} title={readingTitle} readingVersion={readingVersion} list={readingList} onPullMissing={pullReadingList} onRequestMissing={requestReadingListMissing} onRateIssue={rateIssue} onRead={readComic} onOpenSeries={openSeries} onClose={() => setFinishedFileId("")} /> : null}{runCollectionCard ? <RunCollectionDrawer readingList={readingListEntries || EMPTY_ENTRIES} onToggleReadingList={toggleReadingList} key={runCollectionCard.id} card={runCollectionCard} readingVersion={readingVersion} onClose={() => setRunCollectionId(null)} onOpenSeries={openSeries} onRead={readComic} onSave={saveRunCollection} onDelete={deleteRunCollection} onUploadCover={uploadRunCollectionCover} onMarkRuns={markRunsRead} onAddRuns={() => setCollectionPickerId(runCollectionCard.id)} /> : null}{collectionAdd ? <AddToGroupSheet kind="collection" groups={runCollectionList} ids={collectionAdd.ids} itemName={collectionAdd.name} onClose={() => setCollectionAdd(null)}
    onAdd={async (group) => { await saveRunCollection(group.id, { add: collectionAdd.ids }); setCollectionAdd(null); showToast(`${collectionAdd.name} added to ${group.name}`); }}
    onCreate={async (name) => { await createRunCollection({ name, seriesIds: collectionAdd.ids }, { open: false }); setCollectionAdd(null); }} /> : null}{newCollectionOpen ? <NewGroupModal kind="collection" onClose={() => setNewCollectionOpen(false)} onCreate={(fields) => createRunCollection(fields, { pick: true })} /> : null}{collectionPickerCard ? <RunPickerModal title={`Add to ${collectionPickerCard.name}`} series={visibleSeries} already={collectionPickerCard.runIds} onClose={() => setCollectionPickerId(null)} onAdd={(ids) => addRunsToCollection(collectionPickerCard.id, ids)} /> : null}{selectedSeries ? <SeriesDrawer onAddToArc={addIssueToArc} onAddIssuesToArc={addIssuesToArc} readingList={readingListEntries || EMPTY_ENTRIES} onToggleReadingList={toggleReadingList} runCollections={runCollectionList} onOpenRunCollection={(card) => { setSelectedSeries(null); setRunCollectionId(String(card.id)); }} onAddToCollection={(run) => setCollectionAdd({ ids: [String(run.id)], name: run.title })} key={selectedSeries.id} readingVersion={readingVersion} onMarkIssue={markIssueRead} onMarkRun={markRunRead} coverBusy={coverBusy} coverError={coverError} onSelectSeriesCover={selectSeriesCover} onUploadSeriesCover={uploadSeriesCover} backdropBusy={backdropBusy} backdropError={backdropError} onSaveBackdrop={saveSeriesBackdrop} series={selectedSeries} families={catalog?.families || []} allSeries={visibleSeries} parentCollection={seriesParentCollection} dismissSignal={drawerDismissSignal} onBack={returnToCollection} onClose={() => { setSelectedSeries(null); setSeriesParentCollection(null); }} onRead={readComic} onSetAgeRating={setSeriesAgeRating} onRequest={() => createAcquisitionRequest(selectedSeries)} requested={waitingKeys(catalog?.memberRequests).has(`run:${selectedSeries.id}`)} onViewRequests={() => navigate("requests")} requestBusy={requestBusyKey === `series:${selectedSeries.id}`} onAddAlias={addSeriesAlias} onSyncIssues={syncSeriesIssues} onFindRun={openSeriesRunWorkbench} onMergeRun={openSeriesMergeWorkbench} onRebuildRun={rebuildSeriesRun} rebuilding={rebuildingRun} rebuildResult={rebuildResult} onCreateFamily={createSeriesFamily} onSetFamily={setSeriesFamily} onOpenWorkbench={openFileWorkbench} onOpenCover={openCoverWorkbench} onChangeSeriesCover={openSeriesCoverWorkbench} onFixSeriesMatch={openSeriesMatchWorkbench} onSetFormat={setSeriesFormat} onSetDirection={setSeriesDirection} onRate={rateSeries} onRateIssue={rateIssue} onRemove={removeSeries} onUnfollow={unfollowSeries} unfollowBusy={unfollowBusy} onOpenContents={openContentsWorkbench} onChangeRun={openFileRunWorkbench} onEditIssue={openIssueWorkbench} onReplace={openReplacementRequest} onOpenSeries={openSeries} onChangeBackdrop={(item, current) => { setBackdropError(""); setBackdropWorkbench({ series: item, current }); }} backdropVersion={backdropVersion} /> : null}{selectedCollection ? <CollectionDrawer collection={selectedCollection} tab={collectionTab} onTabChange={setCollectionTab} onClose={() => setSelectedCollection(null)} onFindStructure={openStoryStructure} onOpenSeries={openCollectionRun} onOpenContents={openContentsWorkbench} onRequest={() => createAcquisitionRequest(selectedCollection)} requested={waitingKeys(catalog?.memberRequests).has(`collection:${selectedCollection.id}`)} onViewRequests={() => navigate("requests")} requestBusy={requestBusyKey === `collection:${selectedCollection.id}`} onEditIssue={openIssueWorkbench} onUnfollow={unfollowCollection} unfollowBusy={unfollowBusy} /> : null}{importingList ? <ImportReadingListModal onClose={() => setImportingList(false)} onImported={(result) => { setImportingList(false); listImported(result); }} /> : null}{arcPicker ? <ArcPickerModal issues={arcPicker.issues} issueName={arcPicker.name} onClose={() => setArcPicker(null)}
  onAdded={(result) => {
    setArcPicker(null); setListsVersion((version) => version + 1);
    const added = result.added?.length || 0;
    const several = arcPicker.issues.length > 1;
    showToast(!added ? `${several ? "They are" : `${arcPicker.name} is`} already in ${result.name}`
      : several ? `${added} issue${added === 1 ? "" : "s"} added to ${result.name}` : `${arcPicker.name} added to ${result.name}`);
  }}
  onCreated={(made) => {
    setArcPicker(null); setListsVersion((version) => version + 1);
    showToast(`${made.name} started with ${arcPicker.name}`);
  }} /> : null}{newArcOpen ? <NewGroupModal kind="arc" onClose={() => setNewArcOpen(false)} onCreate={createStoryArc} /> : null}{issuePickerList ? <ArcIssuePickerModal list={issuePickerList} series={visibleSeries} onClose={() => setIssuePickerList(null)}
    onAdded={() => setListsVersion((version) => version + 1)} /> : null}{selectedList ? <ReadingListDrawer onAddToArc={addIssueToArc} onGone={arcGone} onAddIssues={(list) => setIssuePickerList({ id: list.id, name: list.name })} readingList={readingListEntries || EMPTY_ENTRIES} onToggleReadingList={toggleReadingList} key={selectedList.id} list={selectedList} allSeries={visibleSeries} readingVersion={readingVersion + listsVersion} onClose={() => setSelectedList(null)} onRead={readComic} onOpenSeries={openSeries} onMarkIssue={markIssueRead} onRateIssue={rateIssue} onEditIssue={openIssueWorkbench} onPullMissing={pullReadingList} onRequestMissing={requestReadingListMissing} onRefresh={refreshReadingList} onDelete={deleteReadingList} onMark={markListRead} onChanged={() => setListsVersion((version) => version + 1)} onOpenWorkbench={openFileWorkbench} onOpenCover={openCoverWorkbench} onOpenContents={openContentsWorkbench} onChangeRun={openFileRunWorkbench} onReplace={openReplacementRequest} /> : null}{workbench ? <MetadataWorkbench data={workbench.data} mode={workbench.mode} busy={workbenchBusy} error={workbenchError} onClose={() => setWorkbench(null)} onSave={saveFileMetadata} onMatch={applyFileMatch} onSearch={searchFileMatches} onReset={resetFileMetadata} /> : null}{issueWorkbench ? <IssueMetadataWorkbench issue={issueWorkbench} busy={issueBusy} error={issueError} onClose={() => setIssueWorkbench(null)} onSave={saveIssueMetadata} onReset={resetIssueMetadata} /> : null}{coverWorkbench ? <CoverWorkbench data={coverWorkbench} busy={coverBusy} error={coverError} onClose={() => setCoverWorkbench(null)} onSelect={selectFileCover} onUpload={uploadFileCover} /> : null}{matchWorkbench ? <SeriesMatchWorkbench data={matchWorkbench} loading={matchLoading} busy={matchBusy} error={matchError} onClose={() => setMatchWorkbench(null)} onSearch={searchSeriesMatches} onConfirm={confirmSeriesMatch} /> : null}{seriesCoverWorkbench ? <CoverWorkbench data={seriesCoverWorkbench} title={seriesCoverWorkbench.series.title} busy={coverBusy} error={coverError} onClose={() => setSeriesCoverWorkbench(null)} onSelect={selectSeriesCover} onUpload={uploadSeriesCover} /> : null}{backdropWorkbench ? <BackdropWorkbench series={backdropWorkbench.series} current={backdropWorkbench.current} busy={backdropBusy} error={backdropError} onClose={() => setBackdropWorkbench(null)} onChoose={(fileId, page) => saveSeriesBackdrop({ fileId, page }, "Header background updated")} onAutomatic={() => saveSeriesBackdrop({ source: "auto" }, "Automatic background restored")} /> : null}{contentsWorkbench ? <VolumeContentsWorkbench data={contentsWorkbench} busy={contentsBusy} error={contentsError} onClose={() => setContentsWorkbench(null)} onChange={changeCollectionContents} onReset={resetCollectionContents} /> : null}{runWorkbench ? <SeriesRunWorkbench data={runWorkbench} loading={runLoading} busy={runBusy} error={runError} onClose={() => setRunWorkbench(null)} onConfirm={confirmSeriesRun} onBuildCollection={buildSeriesCollection} /> : null}{fileRunWorkbench ? <FileRunWorkbench data={fileRunWorkbench} busy={fileRunBusy} error={fileRunError} onClose={() => setFileRunWorkbench(null)} onMove={moveFileToRun} /> : null}{structureWorkbench ? <StoryStructureWorkbench data={structureWorkbench} busy={structureBusy} error={structureError} onClose={() => setStructureWorkbench(null)} onSave={saveStoryStructure} /> : null}{mergeWorkbench ? <SeriesMergeWorkbench data={mergeWorkbench} busy={mergeBusy} error={mergeError} onClose={() => setMergeWorkbench(null)} onTargetChange={(targetId) => targetId ? previewSeriesMerge(mergeWorkbench.source, targetId, mergeWorkbench.candidates) : setMergeWorkbench((current) => ({ ...current, targetId: "", preview: null }))} onConfirm={confirmSeriesMerge} /> : null}{replacementFile ? <ReplacementModal file={replacementFile} busy={replacementBusy} error={replacementError} onClose={() => setReplacementFile(null)} onSubmit={createFileReplacement} /> : null}{pull !== "idle" ? <div className="pull-refresh" role="status" aria-live="polite">{pull === "refreshing" ? <LoadingSpinner size={16} /> : <ArrowsClockwise size={16} />} {pull === "refreshing" ? "Refreshing…" : "Release to refresh"}</div> : null}{profileSheet ? <ProfileSheet onClose={() => setProfileSheet(false)} onRead={readComic} onNavigate={navigate} authStatus={authStatus} onSwitch={() => setPicker({})} /> : null}{picker ? <WhoIsReadingView key={profilesVersion} overlay current={authStatus?.viewer?.id ?? null} ask={picker.ask || null}
      onClose={() => setPicker(null)} canAdd={isAdmin(authStatus?.viewer)} onAdd={() => setAddingProfile(true)} /> : null}{addingProfile ? <AddProfileSheet onClose={() => setAddingProfile(false)}
      onAdded={() => { setAddingProfile(false); setProfilesVersion((value) => value + 1); showToast("Profile added"); }} /> : null}{toast ? <div className={`toast toast--${toastTone}${toastLeaving ? " leaving" : ""}`} role={toastTone === "error" ? "alert" : "status"} key={toast}>{toastTone === "error" ? <WarningCircle size={20} weight="fill" /> : <CheckCircle size={20} weight="fill" />} {toast}</div> : null}</div></HeaderContext.Provider></CollectedEditionsContext.Provider></ViewerContext.Provider></Host.Provider>;
}
