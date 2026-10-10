import { useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CoverArt } from "../components/CoverArt.jsx";
import { GlassSegmented, GlassSelect, LoadingSpinner } from "../components/glass.jsx";
import { GridViewIcon, ListViewIcon } from "../design-icons.jsx";
import { useDialog } from "../dialogs.jsx";
import { issueLabel } from "../discover.js";
import { editionKindLabel, identityKey, useCollectedEditions, volumeTerminology } from "../editions.js";
import { RATINGS, RATING_LABELS, ratingSource } from "../profiles.js";
import { RATING_SOURCES } from "../ratings.js";
import { READING_DIRECTIONS } from "../reader.js";
import { StarRating } from "./frame.jsx";
import { BackdropPicker, CoverPicker } from "./groups.jsx";
import { ArrowCounterClockwise, ArrowLeft, ArrowRight, ArrowsClockwise, BookOpen, Books, CaretRight, Check, CheckCircle, CloudArrowDown, DotsThree, Eye, HardDrive, ListBullets, ListNumbers, MagnifyingGlass, PencilSimple, Plus, ShieldCheck, WarningCircle } from "@phosphor-icons/react";
import { Host } from "../host.js";
import { CoverStatus } from "../components/CoverStatus.jsx";
import { ReadState } from "../components/ReadState.jsx";
import { ProgressRing, StopSquare } from "../components/ProgressRing.jsx";

export function CollectionCoverage({ series, editionsOn }) {
  const directOwned = series.issues?.filter((issue) => issue.directOwned).length ?? 0;
  const inVolumes = series.issues?.filter((issue) => issue.collectionOwned).length ?? 0;
  const toVerify = editionsOn
    ? series.editions?.reduce((count, edition) => count + (edition.coverageGroups?.filter((claim) => !claim.resolved).length ?? 0), 0) ?? 0
    : 0;
  // A collected-volume series owns volumes, not loose issues, so every figure
  // here read 0 -- "0 Single issues owned / 0 Issues in volumes" sitting
  // directly beneath "3 volumes owned". Most of a collected library is these,
  // so most series showed a panel of zeros that read as "you own nothing".
  if (!directOwned && !inVolumes && !toVerify) return null;
  return <section className="coverage-overview"><span><strong>{directOwned}</strong>Single issues owned</span><span><strong>{inVolumes}</strong>Issues in volumes</span>{editionsOn ? <span><strong>{toVerify}</strong>Volume contents to verify</span> : null}</section>;
}

/** Download what a route answers as a file, the session's cookie and all. */
export function downloadFrom(url) {
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
}

/**
 * What an issue's Read control says, from the file behind it.
 *
 * Simpler than a run's: an issue is one comic, so it is unread, part-read or
 * finished. Nothing is offered for an issue with no readable file -- missing,
 * upcoming, a PDF, or owned only inside a volume, which the row already says.
 */
export function issueReadState(issue, readingFiles) {
  const file = issue?.fileId ? readingFiles?.[String(issue.fileId)] : null;
  if (!file?.readable) return null;
  // The same rule the server's resolver uses for a run: a record that is not
  // finished is a place to continue from, whatever page it is on. Two rules
  // for one question is exactly what the resolver exists to prevent.
  const place = file.stale ? null : file;
  if (!place?.pageCount) return { label: "Read", fraction: 0, detail: "", finished: false };
  if (place.finishedAt) return { label: "Read again", fraction: 0, detail: "", finished: true };
  return {
    label: "Continue", fraction: Math.min(1, (place.page + 1) / place.pageCount),
    detail: `page ${place.page + 1} of ${place.pageCount}`, finished: false,
  };
}

// A small menu of actions on one thing, drawn over everything from its
// button's position (as GlassSelect's list is), right edge under the button's.
// It joins the dialog stack, so Escape closes it and not what is under it.
export function ActionMenu({ anchor, label, items, onClose }) {
  const menuRef = useDialog(onClose);
  const [place, setPlace] = useState(null);
  useLayoutEffect(() => {
    const button = anchor.current?.getBoundingClientRect();
    const menu = menuRef.current;
    if (!button || !menu) return;
    const gap = 6;
    const margin = 12;
    const width = Math.min(240, window.innerWidth - margin * 2);
    const below = window.innerHeight - button.bottom - gap - margin;
    const up = menu.scrollHeight > below && button.top - gap - margin > below;
    setPlace({
      width, left: Math.min(Math.max(margin, button.right - width), window.innerWidth - margin - width),
      ...(up ? { bottom: window.innerHeight - button.top + gap } : { top: button.bottom + gap }),
      transformOrigin: up ? "bottom right" : "top right",
    });
  }, []);
  useEffect(() => {
    function outside(event) {
      if (menuRef.current?.contains(event.target) || anchor.current?.contains(event.target)) return;
      onClose();
    }
    document.addEventListener("pointerdown", outside, true);
    window.addEventListener("scroll", onClose, true);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("scroll", onClose, true);
    };
  }, [onClose]);
  return createPortal(<div className="glass-menu glass-select-menu action-menu" ref={menuRef} role="menu" aria-label={label}
    style={place ? { ...place } : { visibility: "hidden", top: 0, left: 0 }} onMouseDown={(event) => event.stopPropagation()}>
    {items.map((item) => <button type="button" role="menuitem" key={item.key} disabled={item.disabled} onClick={() => { onClose(); item.onSelect(); }}>
      <span className="sort-menu-check" aria-hidden="true">{item.icon}</span>
      <span>{item.label}</span>
    </button>)}
  </div>, document.body);
}

// What can be done to one issue, as every reader offers it: a menu from a
// "..." (shown on hover on a desktop, always to a finger) or a long press on
// the tile -- never a tap on the cover, which opens the comic. Read or
// continue it, mark it read or unread for this profile, and, for the admin,
// edit its metadata.
/**
 * Touch and hold `container` (the closest match of `selector` to `anchorRef`)
 * to call `onHold`, as iPad's context menus open (Apple HIG, Context menus).
 * A finger only: a mouse has its button. The release that follows a hold
 * does not also count as a tap.
 */
export function useLongPress(anchorRef, selector, onHold, enabled = true) {
  const hold = useRef(onHold);
  hold.current = onHold;
  useEffect(() => {
    if (!enabled) return undefined;
    const tile = anchorRef.current?.closest(selector);
    if (!tile) return undefined;
    let timer = 0;
    let held = false;
    const cancel = () => { window.clearTimeout(timer); timer = 0; };
    function onDown(event) {
      if (event.pointerType === "mouse" || event.button !== 0) return;
      held = false;
      timer = window.setTimeout(() => { held = true; hold.current(); }, 500);
    }
    // A press that became the menu must not also open the comic on release.
    function onClick(event) {
      if (held) { event.preventDefault(); event.stopPropagation(); held = false; }
    }
    tile.addEventListener("pointerdown", onDown);
    tile.addEventListener("pointermove", cancel);
    tile.addEventListener("pointerup", cancel);
    tile.addEventListener("pointercancel", cancel);
    tile.addEventListener("click", onClick, true);
    return () => {
      cancel();
      tile.removeEventListener("pointerdown", onDown);
      tile.removeEventListener("pointermove", cancel);
      tile.removeEventListener("pointerup", cancel);
      tile.removeEventListener("pointercancel", cancel);
      tile.removeEventListener("click", onClick, true);
    };
  }, [enabled, selector, anchorRef]);
}

export function IssueMenu({ issue, medium, read, onRead, onMark, onEditIssue, onAddToArc, className, longPress = false }) {
  const host = useContext(Host);
  const [open, setOpen] = useState(false);
  const buttonRef = useRef(null);
  const close = useCallback(() => setOpen(false), []);
  useLongPress(buttonRef, ".issue-tile", () => setOpen(true), longPress);
  const label = issueLabel(issue.number, medium);
  const items = [];
  if (read && onRead) items.push({ key: "read", label: read.label, icon: <BookOpen size={16} weight="fill" />, onSelect: () => onRead({ id: issue.fileId }) });
  // Part-read, it can be finished or forgotten; read, forgotten; untouched,
  // finished. Someone else's place on your profile is cleared the same way.
  if (read && onMark && !read.finished) items.push({ key: "mark-read", label: "Mark as read", icon: <Check size={16} weight="bold" />, onSelect: () => onMark(issue, true) });
  if (read && onMark && (read.finished || read.fraction)) items.push({ key: "mark-unread", label: "Mark as unread", icon: <ArrowCounterClockwise size={16} />, onSelect: () => onMark(issue, false) });
  // Owned or missing alike: an arc holds the issue either way, and pulls it.
  if (onAddToArc && /^\d+$/.test(String(issue.id))) items.push({ key: "arc", label: "Add to story arc\u2026", icon: <ListNumbers size={16} />, onSelect: () => onAddToArc(issue) });
  // The host's own, as Flipparr View's Download and Remove download.
  items.push(...(host?.issueExtras?.(issue) || []));
  if (onEditIssue) items.push({ key: "edit", label: "Edit issue metadata", icon: <PencilSimple size={16} />, onSelect: () => onEditIssue(issue) });
  if (!items.length) return null;
  return <>
    <button type="button" ref={buttonRef} className={className} onClick={() => setOpen((value) => !value)}
      aria-haspopup="menu" aria-expanded={open} aria-label={`Options for ${label}`} title="Options">
      <DotsThree size={18} weight="bold" />
    </button>
    {open ? <ActionMenu anchor={buttonRef} label={`Options for ${label}`} items={items} onClose={close} /> : null}
  </>;
}

// What an issue's badge says about reading it, in the ownership badge's spot,
// once the issue is owned: "In progress" over its bar, "Read" when finished.
// Unread is plain, as in every reader looked at (Komga, Plex, Panels).
export function readingBadge(read) {
  if (!read) return null;
  if (read.finished) return { label: "Read", tone: "reading-read" };
  if (read.fraction) return { label: "In progress", tone: "reading-progress" };
  return null;
}

// The run's own menu in its group header: mark every issue read, or unread.
export function RunMenu({ runRead, runStarted, onMarkRun }) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef(null);
  const close = useCallback(() => setOpen(false), []);
  const items = [
    ...(runRead ? [] : [{ key: "mark-read", label: "Mark run as read", icon: <Check size={16} weight="bold" />, onSelect: () => onMarkRun(true) }]),
    ...(runRead || runStarted ? [{ key: "mark-unread", label: "Mark run as unread", icon: <ArrowCounterClockwise size={16} />, onSelect: () => onMarkRun(false) }] : []),
  ];
  return <>
    <button type="button" ref={buttonRef} className="issue-group-menu" onClick={() => setOpen((value) => !value)}
      aria-haspopup="menu" aria-expanded={open} aria-label="Options for this run" title="Options">
      <DotsThree size={18} weight="bold" />
    </button>
    {open ? <ActionMenu anchor={buttonRef} label="Options for this run" items={items} onClose={close} /> : null}
  </>;
}

export function GroupedIssueInventory({ issues: allIssues, onEditIssue, onRead, onRate, onMark, onMarkRun, onAddToArc, onAddIssuesToArc, empty = null, runRead = false, runStarted = false, readingFiles, medium }) {
  // The host's badge for an issue with nothing else to say: Flipparr View's
  // download state. The web gives none.
  const hostBadge = useContext(Host)?.issueBadge || (() => null);
  // And its mark beside the number, as the lock sits: Flipparr View's
  // "downloaded", which reading's badge would otherwise hide.
  const hostMark = useContext(Host)?.issueMark || (() => null);
  // And its download control, seen without opening a menu (Flipparr View's
  // Phase 6: the menu's Download was too hard to find).
  const hostDownload = useContext(Host)?.issueDownload || (() => null);
  // A reader host lists only what there is to read: no Missing, Upcoming or
  // counts of what is owned (Flipparr View; the owner, 2026-10-10).
  const readerOnly = Boolean(useContext(Host)?.readerOnly);
  const readable = useContext(Host)?.readable || (() => true);
  const issues = readerOnly ? (allIssues || []).filter((issue) => issue.fileId && readable({ kind: "issue", fileId: String(issue.fileId) })) : allIssues;
  const downloadButton = (issue, className) => {
    const state = issue.fileId ? hostDownload(issue) : null;
    if (!state) return null;
    return <button type="button" className={`${className}${state.done ? " done" : ""}`} onClick={state.onPress}
      aria-label={state.label} title={state.label}>
      {state.busy ? <ProgressRing progress={state.progress}><StopSquare /></ProgressRing> : state.done ? <CheckCircle size={15} weight="fill" /> : <CloudArrowDown size={15} />}
    </button>;
  };
  // Covers are why the tab exists, so posters lead. A long run is also less
  // scrolling this way than as tall rows: four across beats one down.
  const [view, setView] = useState("grid");
  // Select (Komga's and Kavita's pattern): tick many issues, then add them
  // to a story arc at once. A tap toggles instead of reading.
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const selectable = onAddIssuesToArc ? (issues || []).filter((issue) => /^\d+$/.test(String(issue.id))) : [];
  function endSelecting() { setSelecting(false); setSelected(new Set()); }
  function toggleSelected(id) {
    setSelected((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  }
  const selectToggle = (issue) => selecting && /^\d+$/.test(String(issue.id))
    ? <button type="button" className={`issue-select-toggle${selected.has(String(issue.id)) ? " selected" : ""}`} aria-pressed={selected.has(String(issue.id))}
      aria-label={`Select ${issueLabel(issue.number, medium)}`} onClick={() => toggleSelected(String(issue.id))}>
      <span className="issue-select-check" aria-hidden="true">{selected.has(String(issue.id)) ? <Check size={14} weight="bold" /> : null}</span>
    </button> : null;
  if (!issues?.length && empty) return empty;
  if (!issues?.length) return <div className="drawer-empty"><BookOpen size={26} weight="duotone" /><strong>No issue list linked yet</strong><span>Flipparr will add series runs and their issues automatically when a match is found.</span></div>;
  const groups = [];
  const byRun = new Map();
  for (const issue of issues) {
    const key = String(issue.contextRunId || issue.contextLabel || "unknown");
    if (!byRun.has(key)) {
      const group = {
        key,
        title: issue.contextGroupName || issue.contextLabel || "Unknown run",
        runTitle: issue.contextLabel,
        year: issue.contextYear,
        type: issue.contextType || "main",
        issues: [],
      };
      byRun.set(key, group);
      groups.push(group);
    }
    byRun.get(key).issues.push(issue);
  }
  // The same toggle and icons as the Comics toolbar, in the first run's
  // header rather than on a row of its own above it.
  // The Comics tools row's switch, so grid or list reads the same everywhere.
  const viewToggle = <GlassSegmented label="Choose issue view" value={view} onChange={setView} className="issue-view-toggle"
    items={[{ id: "grid", title: "Grid view", icon: <GridViewIcon /> }, { id: "list", title: "List view", icon: <ListViewIcon /> }]} />;
  const allSelected = selectable.length > 0 && selectable.every((issue) => selected.has(String(issue.id)));
  return <div className={`grouped-issue-inventory ${view}${selecting ? " selecting" : ""}`}>
    {groups.map((group, groupIndex) => {
    const owned = group.issues.filter((issue) => issue.ownership !== "unowned").length;
    return <section className="issue-run-group" key={group.key}>
      <header><div><span>{group.type === "specials" ? "Special / one-shot" : group.type === "arc" ? "Story arc" : "Series run"}</span><h3>{group.title}</h3>{group.runTitle !== group.title || group.year ? <small>{[group.runTitle !== group.title ? group.runTitle : null, group.year].filter(Boolean).join(" · ")}</small> : null}</div><div className="issue-run-group-aside">{groupIndex === 0 ? viewToggle : null}<strong>{readerOnly ? `${group.issues.length} ${group.issues.length === 1 ? "issue" : "issues"}` : `${owned} of ${group.issues.length} owned`}</strong>{groupIndex === 0 && (selectable.length || (onMarkRun && readingFiles)) ? <span className="issue-run-group-tools">{selectable.length ? <button type="button" className="glass-button issue-select-button" aria-pressed={selecting} onClick={() => (selecting ? endSelecting() : setSelecting(true))}>{selecting ? "Cancel" : "Select"}</button> : null}{onMarkRun && readingFiles && !selecting ? <RunMenu runRead={runRead} runStarted={runStarted} onMarkRun={onMarkRun} /> : null}</span> : null}</div></header>
      <div className={view === "grid" ? "issue-tile-grid" : ""}>{group.issues.map((issue) => {
        // "Volume 5" under "Vol. 5" says it twice.
        const genericTitle = !issue.title || identityKey(issue.title) === identityKey(`Issue ${issue.number}`)
          || (medium === "manga" && identityKey(issue.title) === identityKey(`Volume ${issue.number}`));
        const releaseLabel = issue.publicationDate
          ? new Date(`${issue.publicationDate}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
          : issue.publicationYear ? `Published ${issue.publicationYear}` : "Release date unknown";
        // Owned as a single issue is what the Issues tab already says, so it
        // carries no badge; the other states are news and keep theirs.
        const stateLabel = issue.ownership === "both" ? "Single + volume"
          : issue.ownership === "direct" ? null
          : issue.ownership === "collection" ? "In volume"
          : issue.releaseState === "upcoming" ? "Upcoming"
          : issue.releaseState === "unknown" ? "Date needed" : "Missing";
        const owned = issue.ownership !== "unowned";
        const read = issueReadState(issue, readingFiles);
        if (view === "grid") {
          // A missing issue keeps its tile, dimmed, so a gap in a run is
          // visible rather than silently absent from the grid.
          return <article className={`issue-tile ${owned ? "" : "unowned"}`} key={issue.id}>
            {selectToggle(issue)}
            {/* The badge sits over the cover, beside it rather than inside it,
                so a missing issue's dimmed cover does not dim its badge too. */}
            <span className="issue-tile-art">
              <span className="issue-tile-cover"><CoverArt id={`issue-${issue.id}`} title={`${issue.contextLabel || "Issue"} #${issue.number}`} cover={issue.fileCover || issue.cover} decorative placeholderSize={22} /></span>
              {stateLabel ? <span className={`ownership-source ${issue.ownership} ${issue.acquisitionState || ""}`}>{stateLabel}</span>
                : readerOnly ? null
                : readingBadge(read) ? <span className={`ownership-source ${readingBadge(read).tone}`}>{readingBadge(read).label}</span>
                : !readerOnly && hostBadge(issue) ? <span className={`ownership-source ${hostBadge(issue).tone}`}>{hostBadge(issue).label}</span> : null}
              {/* A reader host: the cover itself reads, status sits in its
                  corner, and the rest is the long-press menu (owner,
                  2026-10-10; Apple HIG, Context menus). */}
              {readerOnly && read && onRead ? <button type="button" className="issue-tile-open" onClick={() => onRead({ id: issue.fileId })}
                aria-label={`${read.label} ${issueLabel(issue.number, medium)}${read.detail ? `, ${read.detail}` : ""}`} /> : null}
              {readerOnly ? <ReadState finished={read?.finished} fraction={read?.fraction} /> : null}
              {readerOnly ? <CoverStatus target={{ kind: "issue", fileId: String(issue.fileId) }} /> : null}
              {/* Inside the cover's box, not the tile's: anchored to the tile
                  it would sit under the issue title. */}
              {!readerOnly && read && onRead ? <button type="button" className="issue-tile-read" onClick={() => onRead({ id: issue.fileId })}
                aria-label={`${read.label} ${issueLabel(issue.number, medium)}${read.detail ? `, ${read.detail}` : ""}`}
                title={read.label}><BookOpen size={14} weight="fill" /></button> : null}
              {read?.fraction && !readerOnly ? <span className="read-progress-bar" aria-hidden="true"><i style={{ width: `${Math.round(read.fraction * 100)}%` }} /></span> : null}
              <IssueMenu issue={issue} medium={medium} read={read} onRead={onRead} onMark={onMark} onEditIssue={onEditIssue} onAddToArc={onAddToArc} className={`issue-tile-menu${readerOnly ? " quiet" : ""}`} longPress />
              {readerOnly ? null : downloadButton(issue, "issue-tile-download")}
            </span>
            {onEditIssue ? <button type="button" className="issue-tile-edit" onClick={() => onEditIssue(issue)} aria-label={`Edit metadata for ${issue.contextLabel || "issue"} issue ${issue.number}`} title="Edit issue metadata"><PencilSimple size={14} /></button> : null}
            <strong>{issueLabel(issue.number, medium)}{issue.metadataLocked ? <ShieldCheck className="issue-local-lock" size={12} weight="fill" aria-label="Local metadata correction locked" /> : null}{hostMark(issue)}</strong>
            {!genericTitle ? <small className="issue-tile-title">{issue.title}</small> : null}
          </article>;
        }
        return <article key={issue.id}>{selectToggle(issue)}<span className={`grouped-issue-cover ${issue.fileCover ? "from-file" : ""}`}><CoverArt id={`issue-${issue.id}`} title={`${issue.contextLabel || "Issue"} #${issue.number}`} cover={issue.fileCover || issue.cover} decorative placeholderSize={16} /></span><span className="grouped-issue-number">{issueLabel(issue.number, medium)}{hostMark(issue)}</span><div>{!genericTitle ? <strong>{issue.title}{issue.metadataLocked ? <ShieldCheck className="issue-local-lock" size={13} weight="fill" aria-label="Local metadata correction locked" /> : null}</strong> : null}<small>{releaseLabel}{read?.detail ? ` · ${read.detail}` : ""}</small></div><div className="issue-row-actions">{!stateLabel && readingBadge(read) ? <span className={`ownership-source ${readingBadge(read).tone}`}>{readingBadge(read).label}</span> : null}{onRate && issue.ownership !== "unowned" ? <StarRating rating={{ value: issue.yourRating || 0, source: issue.yourRating ? RATING_SOURCES.yours : RATING_SOURCES.none, count: 0 }} title={`${issue.contextLabel || "Issue"} ${issueLabel(issue.number, medium)}`} size={15} onRate={(value) => onRate(issue, value)} /> : null}{stateLabel ? <span className={`ownership-source ${issue.ownership} ${issue.acquisitionState || ""}`}>{stateLabel}</span> : null}{read && onRead ? <button type="button" className="issue-row-read" onClick={() => onRead({ id: issue.fileId })} aria-label={`${read.label} ${issueLabel(issue.number, medium)}${read.detail ? `, ${read.detail}` : ""}`} title={read.label}><BookOpen size={14} weight="fill" /></button> : null}{onEditIssue ? <button type="button" className="issue-row-edit" onClick={() => onEditIssue(issue)} aria-label={`Edit metadata for ${issue.contextLabel || "issue"} issue ${issue.number}`} title="Edit issue metadata"><PencilSimple size={14} /></button> : null}{downloadButton(issue, "issue-row-download")}<IssueMenu issue={issue} medium={medium} read={read} onRead={onRead} onMark={onMark} onEditIssue={onEditIssue} onAddToArc={onAddToArc} className="issue-row-menu" /></div></article>;
      })}</div>
    </section>;
  })}
    {selecting ? <div className="issue-select-bar" role="toolbar" aria-label="Selected issues">
      <span className="issue-select-count"><strong role="status">{selected.size} selected</strong>
        <button type="button" onClick={() => setSelected(allSelected ? new Set() : new Set(selectable.map((issue) => String(issue.id))))}>{allSelected ? "Clear all" : "Select all"}</button></span>
      <button type="button" className="primary-button" disabled={!selected.size}
        onClick={() => { onAddIssuesToArc(selectable.filter((issue) => selected.has(String(issue.id)))); endSelecting(); }}><ListNumbers size={17} /> Add to story arc</button>
    </div> : null}
  </div>;
}

export function VolumeInventory({ editions }) {
  if (!editions?.length) return <div className="drawer-empty"><Books size={26} weight="duotone" /><strong>No volumes linked yet</strong><span>Collected files will appear here after inventory.</span></div>;
  return <div className="edition-inventory">{editions.map((edition) => { const title = edition.subtitle ? `${edition.title}: ${edition.subtitle}` : edition.title; return <article key={edition.logicalVolumeKey || edition.id}><span className="edition-cover"><CoverArt id={`edition-${edition.id}`} title={title} cover={edition.cover} decorative placeholderSize={20} /></span><div className="edition-card-content"><header><div><strong>{title}</strong><small>{[edition.publisher, edition.publicationYear, edition.format].filter(Boolean).join(" · ") || "Volume details incomplete"}</small></div><span><b>{editionKindLabel(edition.editionKind)}{edition.volume ? ` · Vol. ${edition.volume}` : ""}</b><small>{edition.copyCount > 1 ? `${edition.copyCount} library files` : edition.source || "Local metadata"}</small>{edition.coverageOverrideCount ? <small>{edition.coverageOverrideCount} local contents correction{edition.coverageOverrideCount === 1 ? "" : "s"}</small> : null}</span></header>{edition.isbns?.length ? <p><b>ISBN</b> {edition.isbns.join(", ")}</p> : null}<div className="coverage-groups">{edition.coverageGroups?.length ? edition.coverageGroups.map((coverage) => <div className={coverage.resolved ? "resolved" : "unresolved"} key={`${coverage.seriesLabel}-${coverage.issueLabel}-${coverage.source}`}><span><strong>{coverage.seriesLabel} #{coverage.issueLabel}</strong><small>{coverage.resolved ? "Counts toward canonical ownership" : "Visible claim; canonical numbering unresolved"}</small></span><b>{coverage.confidence}</b><p>{coverage.source}{coverage.evidence ? ` · ${volumeTerminology(coverage.evidence)}` : ""}</p></div>) : <div className="no-coverage"><WarningCircle size={18} /><span><strong>Contents not established</strong><small>{volumeTerminology(edition.coverageStatus) || "No structured issue coverage was returned by the current sources."}</small></span></div>}</div></div></article>; })}</div>;
}

export function FileActionButtons({ file, readable, onRead, onOpenWorkbench, onOpenCover, onOpenContents, onChangeRun, onReplace }) {
  const editionsOn = useCollectedEditions();
  // Editing a collected edition's issue contents is an edition-management
  // surface; the file itself stays visible and fixable either way.
  // Read comes first: it is the only thing here you would do for pleasure.
  // Whether it can be read is the server's answer, from the file's first
  // bytes -- the suffix lies in both directions. A .cb7 needs a tool the
  // container may not carry, and a zip named .bin opens perfectly.
  return <>{readable ? <button onClick={() => onRead(file)}><BookOpen size={14} /> Read</button>
    : <small className="file-not-readable">Not a readable archive</small>}{file.identityKind === "edition" && editionsOn ? <button onClick={() => onOpenContents(file)}><ListBullets size={14} /> Issues</button> : null}<button onClick={() => onReplace(file)}><CloudArrowDown size={14} /> Replace</button><button onClick={() => onChangeRun(file)}><Books size={14} /> Change run</button><button onClick={() => onOpenCover(file)}><BookOpen size={14} /> Cover</button><button onClick={() => onOpenWorkbench(file, "match")}><ArrowsClockwise size={14} /> Fix match</button><button onClick={() => onOpenWorkbench(file, "edit")}><PencilSimple size={14} /> Metadata</button></>;
}

// A phone's file row: the same tools as the desktop's buttons, from a "..."
// beside the file, as an issue row's menu is (owner, 2026-10-01: the old
// labelled Actions button sat on a row of its own under every file).
export function FileMenu({ file, readable, onRead, onOpenWorkbench, onOpenCover, onOpenContents, onChangeRun, onReplace }) {
  const editionsOn = useCollectedEditions();
  const [open, setOpen] = useState(false);
  const buttonRef = useRef(null);
  const close = useCallback(() => setOpen(false), []);
  const items = [
    readable ? { key: "read", label: "Read", icon: <BookOpen size={16} />, onSelect: () => onRead(file) }
      : { key: "read", label: "Not a readable archive", icon: <BookOpen size={16} />, disabled: true, onSelect: () => {} },
    ...(file.identityKind === "edition" && editionsOn ? [{ key: "issues", label: "Issues", icon: <ListBullets size={16} />, onSelect: () => onOpenContents(file) }] : []),
    { key: "replace", label: "Replace", icon: <CloudArrowDown size={16} />, onSelect: () => onReplace(file) },
    { key: "run", label: "Change run", icon: <Books size={16} />, onSelect: () => onChangeRun(file) },
    { key: "cover", label: "Cover", icon: <BookOpen size={16} />, onSelect: () => onOpenCover(file) },
    { key: "match", label: "Fix match", icon: <ArrowsClockwise size={16} />, onSelect: () => onOpenWorkbench(file, "match") },
    { key: "edit", label: "Metadata", icon: <PencilSimple size={16} />, onSelect: () => onOpenWorkbench(file, "edit") },
  ];
  const label = `Actions for ${file.filename}`;
  return <>
    <button type="button" ref={buttonRef} className="file-menu-button" onClick={() => setOpen((value) => !value)}
      aria-haspopup="menu" aria-expanded={open} aria-label={label} title="Actions">
      <DotsThree size={22} weight="bold" />
    </button>
    {open ? <ActionMenu anchor={buttonRef} label={label} items={items} onClose={close} /> : null}
  </>;
}

export function FileInventory({ files, readingFiles, onRead, onOpenWorkbench, onOpenCover, onOpenContents, onChangeRun, onReplace }) {
  if (!files?.length) return <div className="drawer-empty"><HardDrive size={26} weight="duotone" /><strong>No local files linked</strong></div>;
  return <div className="file-inventory">{files.map((file) => <article key={file.path}><HardDrive size={20} weight="duotone" /><div><strong>{file.filename}</strong><small>{file.identityKind === "issue" ? "Single issue" : editionKindLabel(file.editionKind)} · {(file.sizeBytes / 1024 / 1024).toFixed(1)} MB</small>{file.metadataLocked ? <small className="metadata-lock"><ShieldCheck size={13} weight="fill" /> Local corrections locked</small> : null}</div><span className="file-row-actions"><FileActionButtons file={file} readable={Boolean(readingFiles?.[String(file.id)]?.readable)} onRead={onRead} onOpenWorkbench={onOpenWorkbench} onOpenCover={onOpenCover} onOpenContents={onOpenContents} onChangeRun={onChangeRun} onReplace={onReplace} /></span><FileMenu file={file} readable={Boolean(readingFiles?.[String(file.id)]?.readable)} onRead={onRead} onOpenWorkbench={onOpenWorkbench} onOpenCover={onOpenCover} onOpenContents={onOpenContents} onChangeRun={onChangeRun} onReplace={onReplace} /></article>)}</div>;
}

export function IssueCatalogCard({ series, catalogKnown, syncing, error, lastResult, onSync, onReviewFiles, onFindRun }) {
  const catalog = series.issueCatalog || { status: "unknown", syncReady: false };
  const collectionIssueCount = series.issues?.filter((issue) => issue.collectionOwned).length || 0;
  const directFiles = series.inventory?.directIssueFiles || 0;
  const editionCount = series.inventory?.editionCount || 0;
  const issues = series.issues || [];
  const numberedOnly = catalog.titlePolicy === "numbered_only";
  const missingTitleIssues = numberedOnly ? [] : issues.filter((issue) => !String(issue.title || "").trim());
  const upcomingUntitled = missingTitleIssues.filter((issue) => issue.releaseState === "upcoming");
  const repairableTitles = missingTitleIssues.filter((issue) => issue.releaseState !== "upcoming");
  const missingDateIssues = issues.filter((issue) => issue.publicationYear == null);
  const repairableFields = repairableTitles.length + missingDateIssues.length;
  const resultMetadata = lastResult?.metadata;
  const repairedFields = (resultMetadata?.repairedTitleCount || 0) + (resultMetadata?.repairedDateCount || 0);
  const providerErrors = lastResult?.providerErrors || [];
  const checkedAt = catalog.lastSyncedAt ? new Date(catalog.lastSyncedAt).toLocaleString() : null;
  if (catalogKnown) {
    let tone = repairableFields ? "repairing" : "";
    let heading = "Issue details complete";
    let outcome = `No missing issue titles or release dates across ${catalog.issueCount || issues.length} issues.`;
    if (syncing) {
      heading = "Checking metadata sources";
      outcome = "Looking for newly available issue titles and release dates.";
    } else if (error || catalog.error) {
      tone = "error";
      heading = "Metadata check couldn’t finish";
      outcome = "Existing issue information is unchanged. You can try again without losing local corrections.";
    } else if (repairableFields) {
      heading = `${repairableFields} issue detail${repairableFields === 1 ? " is" : "s are"} still unavailable`;
      const parts = [];
      if (repairableTitles.length) parts.push(`${repairableTitles.length} missing title${repairableTitles.length === 1 ? "" : "s"}`);
      if (missingDateIssues.length) parts.push(`${missingDateIssues.length} missing release date${missingDateIssues.length === 1 ? "" : "s"}`);
      outcome = `${parts.join(" and ")}. Enabled providers were checked but do not currently supply ${repairableFields === 1 ? "this field" : "these fields"}.`;
    } else if (numberedOnly) {
      heading = "Issue metadata current";
      outcome = "This run is listed by issue number. The matched sources do not publish separate issue titles.";
    } else if (upcomingUntitled.length) {
      heading = "Metadata current";
      const labels = upcomingUntitled.map((issue) => `#${issue.number}`).join(", ");
      outcome = `${labels} ${upcomingUntitled.length === 1 ? "is" : "are"} upcoming; ${upcomingUntitled.length === 1 ? "its title has" : "their titles have"} not been announced yet.`;
    }
    return <section className={`issue-catalog-card synced ${tone}`}><div className="issue-catalog-outcome"><strong>{heading}</strong><small>{outcome}</small>{repairedFields ? <em><CheckCircle size={14} weight="fill" /> {repairedFields} field{repairedFields === 1 ? "" : "s"} filled during the latest check.</em> : resultMetadata && !syncing ? <em>No additional fields were available during the latest check.</em> : null}<details><summary>View metadata details</summary><div className="issue-catalog-details"><p>{catalog.detail || `The issue list is linked through ${catalog.provider?.toUpperCase() || "the metadata provider"}.`}</p>{checkedAt ? <time>Last checked {checkedAt}</time> : null}{providerErrors.length ? <ul>{providerErrors.map((item) => <li key={item.provider}><b>{item.provider}</b>: {item.error}</li>)}</ul> : null}<p>Local corrections remain locked and are never replaced by provider refreshes.</p></div></details></div><button onClick={onSync} disabled={syncing}>{syncing ? <LoadingSpinner size={17} /> : <ArrowsClockwise size={17} />} {syncing ? "Checking…" : "Refresh details"}</button>{error || catalog.error ? <p>{error || catalog.error}</p> : null}</section>;
  }
  if (catalog.syncReady) {
    const providerNames = (catalog.anchorProviders || []).map((provider) => ({ gcd: "GCD", metron: "Metron", comic_vine: "Comic Vine" }[provider] || provider));
    const providerLabel = providerNames.length ? providerNames.join(" and ") : "a metadata service";
    return <section className="issue-catalog-card ready"><div><strong>Complete issue list is ready to load</strong><small>Your confirmed issue match is linked through {providerLabel}. Loading the run will add complete issue numbering and improve ownership totals.</small></div><button onClick={onSync} disabled={syncing} aria-busy={syncing}>{syncing ? <LoadingSpinner size={17} /> : <CloudArrowDown size={17} />} Load issue list</button>{error || catalog.error ? <p>{error || catalog.error}</p> : null}</section>;
  }
  if (editionCount > 0 && directFiles === 0) return <section className="issue-catalog-card pending"><div><strong>Find the full series</strong><small>We can see {collectionIssueCount} issue{collectionIssueCount === 1 ? "" : "s"} covered by your volumes. Flipparr can find the complete issue list, related miniseries, and specials automatically.</small><em>You choose how to acquire gaps; provider-run maintenance stays automatic.</em></div><button onClick={() => onFindRun(series)}><MagnifyingGlass size={17} /> Find full series</button></section>;
  if (directFiles > 0) return <section className="issue-catalog-card pending"><div><strong>Confirm one issue match to load the full series</strong><small>Your issue files are in the library, but none is linked to a verified external issue yet. Review one file and use Fix Match to choose the correct result.</small></div><button onClick={onReviewFiles}><Eye size={17} /> Review issue files</button></section>;
  return <section className="issue-catalog-card pending"><div><strong>Full issue list not available</strong><small>No verified issue-run evidence is available yet. Your existing library records are still safe and usable.</small><em>No action is required.</em></div></section>;
}

export function CollectionManagement({ series, families, allSeries, onCreateFamily, onSetFamily }) {
  const [name, setName] = useState("");
  const [targetFamily, setTargetFamily] = useState("");
  const [candidateRun, setCandidateRun] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const family = families.find((item) => item.id === series.family?.id);
  const familyRunIds = new Set(family?.runIds || []);
  const otherFamilies = families.filter((item) => item.id !== family?.id);
  const availableRuns = allSeries.filter((item) => !familyRunIds.has(item.id));
  async function create(event) {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true); setError("");
    const result = await onCreateFamily(name.trim(), [series.id]);
    if (result.ok) setName(""); else setError(result.error);
    setBusy(false);
  }
  async function assign(runId, familyId) {
    setBusy(true); setError("");
    const result = await onSetFamily(runId, familyId || null);
    if (!result.ok) setError(result.error);
    setBusy(false);
  }
  return <div className="family-management">
    <section className={`current-family ${family ? "linked" : ""}`}><Books size={24} weight="duotone" /><div><span>{family ? "Collection" : "Independent publication run"}</span><strong>{family?.name || series.title}</strong><small>{family ? `${family.runCount} runs are grouped in this collection. Each run keeps its own numbering and metadata.` : "This run is currently shown independently. It can be added to a collection without merging its identity."}</small></div></section>
    {family ? <section className="family-members"><header><strong>Publication runs</strong><small>Remove any run to show it independently again.</small></header>{family.runs.map((run) => <article key={run.id}><div><strong>{run.title}</strong><small>{run.year} · {run.total} issue{run.total === 1 ? "" : "s"}</small></div><button disabled={busy} onClick={() => assign(run.id, null)}>Remove</button></article>)}</section> : null}
    {family && availableRuns.length ? <section className="family-assignment"><h3>Add or move a run into this collection</h3><div><GlassSelect label="Run to add" placeholder="Choose a run…" value={candidateRun} onChange={setCandidateRun} className="glass-select--fill" options={availableRuns.map((run) => ({ value: run.id, label: `${run.title} (${run.year})${run.family ? ` · from ${run.family.name}` : ""}` }))} /><button disabled={busy || !candidateRun} onClick={() => assign(candidateRun, family.id)}><Plus size={17} /> Add run</button></div><small>Moving a run changes only its collection membership.</small></section> : null}
    {otherFamilies.length ? <section className="family-assignment"><h3>{family ? "Move this run to another collection" : "Add this run to an existing collection"}</h3><div><GlassSelect label="Collection" placeholder="Choose a collection…" value={targetFamily} onChange={setTargetFamily} className="glass-select--fill" options={otherFamilies.map((item) => ({ value: item.id, label: `${item.name} · ${item.runCount} run${item.runCount === 1 ? "" : "s"}` }))} /><button disabled={busy || !targetFamily} onClick={() => assign(series.id, targetFamily)}>{family ? "Move run" : "Join collection"}</button></div></section> : null}
    <form className="family-create-form" onSubmit={create}><h3>Create a new collection from this run</h3><p>The current run will move into the new collection. Files, issues, volumes, aliases, and provider matches stay on the run.</p><div><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Collection name…" /><button disabled={busy || !name.trim()}><Plus size={17} /> Create collection</button></div></form>
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
  </div>;
}

// What a run's Edit panel can change, in the order it lists them. Assets
// first: they are what people come here to change, and they are the two the
// drawer's header used to spend its only button on.
export const EDIT_SECTIONS = [
  ["cover", "Series cover", "Use another issue's art, a provider's cover, or your own image."],
  ["background", "Header background", "The page behind this drawer's header."],
];

/**
 * Edit, the way iOS Settings works: a list, then one section at a time, with
 * Back to where you came from.
 *
 * It is a panel inside the drawer rather than a dialog over it. That is what
 * keeps one scrim on screen -- tapping outside dismisses the drawer, as it
 * always did -- and it means the cover and background pickers are shown in
 * place rather than stacked as a second modal, which this app has never done
 * and whose layering is decided by render order rather than by design.
 *
 * What it holds is what a run can actually change. A run's title, publisher
 * and year come from the provider it is matched to and there is no route to
 * override them, so they are shown as facts with the one action that does
 * change them, rather than as fields that would quietly discard what you type.
 */
export function SeriesEditPanel({
  series, section, onSection, shownBackdrop, medium, readingFiles, directionOverride, onSetDirection,
  cover, onSelectCover, onUploadCover, coverBusy, coverError,
  onChooseBackdrop, onAutomaticBackdrop, backdropBusy, backdropError,
  alternateTitles, aliasForm, onFixMatch, onSetFormat, onSetAgeRating,
}) {
  if (section === "cover") {
    return <div className="edit-section">
      <p className="edit-section-intro">Choose art from the comic, a metadata provider, or upload your own image. The comic file is not altered.</p>
      {cover
        ? <CoverPicker data={cover} busy={coverBusy} error={coverError} onSelect={onSelectCover} onUpload={onUploadCover} />
        : <div className="edit-section-status" role="status"><LoadingSpinner size={18} /> Looking for cover art…</div>}
    </div>;
  }
  if (section === "background") {
    return <div className="edit-section">
      <p className="edit-section-intro">Choose a page from {series.title} to show behind this header.</p>
      <BackdropPicker series={series} current={shownBackdrop} busy={backdropBusy} error={backdropError} readable={readingFiles}
        onChoose={onChooseBackdrop} onAutomatic={onAutomaticBackdrop} />
    </div>;
  }
  return <div className="edit-index">
    {/* Facts, not fields. A run's identity is its provider match, and the
        honest way to offer a change is the match, not a text box that would
        be thrown away. */}
    <section className="edit-identity">
      <dl>
        <div><dt>Title</dt><dd>{series.title}</dd></div>
        {series.publisher ? <div><dt>Publisher</dt><dd>{series.publisher}</dd></div> : null}
        {series.year ? <div><dt>Year</dt><dd>{series.year}</dd></div> : null}
      </dl>
      <p>These come from the run this is matched to. Fix the match to change them.</p>
      <button type="button" className="ghost-button" onClick={() => onFixMatch(series)}><MagnifyingGlass size={16} /> Fix series match</button>
    </section>
    <div className="edit-rows">
      {EDIT_SECTIONS.map(([id, label, detail]) => <button type="button" className="edit-row" onClick={() => onSection(id)} key={id}>
        <span><strong>{label}</strong><small>{detail}</small></span>
        <CaretRight size={17} />
      </button>)}
    </div>
    <section className="advanced-card">
      <div><strong>{medium === "manga" ? "Filed as manga" : "Filed as a comic"}</strong><p>{medium === "manga" ? "Searched and filed by volume, in the Manga folder." : "Manga is searched and filed by volume, in the Manga folder."} Change it if the publisher misled Flipparr.</p></div>
      <button type="button" onClick={() => onSetFormat?.(series, medium === "manga" ? "comic" : "manga")}><Books size={16} /> {medium === "manga" ? "File as comic" : "File as manga"}</button>
    </section>
    {/* How it is filed and which way it turns are not the same question: an
        English edition of a manga is printed left to right, and no provider
        records that, so it is the reader's to say. */}
    <section className="advanced-card edit-direction">
      <div>
        <strong>Reading direction</strong>
        <p>{directionOverride
          ? `Set by hand: ${directionOverride === READING_DIRECTIONS.rtl ? "right to left" : "left to right"}.`
          : medium === "manga"
            ? "Right to left, because this is filed as manga."
            : "Left to right, because this is filed as a comic."}</p>
      </div>
      <GlassSegmented label="Choose reading direction" value={directionOverride || "auto"}
        onChange={(next) => onSetDirection?.(series, next === "auto" ? null : next)}
        className="direction-toggle"
        items={[
          { id: "auto", label: "Auto" },
          { id: READING_DIRECTIONS.ltr, title: "Left to right", icon: <ArrowRight size={16} /> },
          { id: READING_DIRECTIONS.rtl, title: "Right to left", icon: <ArrowLeft size={16} /> },
        ]} />
    </section>
    {/* Who may read it. What Metron or the cover said is kept; the admin's
        choice outranks it, and "Auto" goes back to it. */}
    <section className="advanced-card edit-rating">
      <div>
        <strong>Age rating</strong>
        <p>{ratingSource(series)}</p>
      </div>
      {/* A list, not a segmented control: five ratings do not fit across a phone. */}
      <GlassSelect label="Age rating" value={series.ageRatingOverride || "auto"}
        onChange={(next) => onSetAgeRating?.(series, next === "auto" ? null : next)}
        options={[{ value: "auto", label: series.ageRatingFound ? `Auto (${RATING_LABELS[series.ageRatingFound]})` : "Auto" },
          ...RATINGS.map((id) => ({ value: id, label: RATING_LABELS[id] }))]} />
    </section>
    {/* Small enough to edit where it is: only the cover and background
        pickers are big enough to step into (owner, 2026-10-01). */}
    <section className="advanced-card edit-titles">
      <div><strong>Alternate titles</strong><p>Other names this run&rsquo;s comics are filed under. Scans use these automatically; add one only if a scan keeps missing a file.</p></div>
      {alternateTitles.length ? <div className="alias-list">{alternateTitles.map((item) => <span className={item.confirmed ? "confirmed" : ""} key={`${item.name}-${item.source}`}><strong>{item.name}</strong><small>{item.confirmed ? "Manually confirmed" : item.source}</small></span>)}</div> : null}
      {aliasForm}
    </section>
  </div>;
}
