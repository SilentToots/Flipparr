import { CoverArt, SeriesCover } from "../components/CoverArt.jsx";
import { StatusBadge } from "../components/StatusBadge";
import { ActiveRunIcon, CollectionIcon, FollowedIcon } from "../design-icons.jsx";
import { issueLabel } from "../discover.js";

import { arcMaker, arcOwnerLine, arcYears } from "../reading-list.js";
import { collectionReadTarget, countsLine } from "../run-collections.js";

import { BookOpen, CheckCircle, CircleHalf, ClockCounterClockwise, DotsThree, WarningCircle } from "@phosphor-icons/react";
import { useCallback, useContext, useRef, useState } from "react";
import { Host, useManages, useReaderOnly } from "../host.js";
import { CoverStatus } from "../components/CoverStatus.jsx";
import { ReadState, placeFraction } from "../components/ReadState.jsx";
import { ActionMenu, useLongPress } from "../drawers/inventory.jsx";

export function publicationState(series) {
  const state = series?.publicationStatus || "unknown";
  return state === "completed"
    ? { label: "Run Complete", tone: "completed", known: true }
    : state === "ongoing"
      ? { label: "Active Run", tone: "ongoing", known: true }
      // "Unknown" read as a fault on a freshly scanned library, where almost
      // every row is waiting on provider metadata that will arrive.
      : { label: "Status pending", tone: "pending", known: false };
}

export function PublicationStatus({ series }) {
  const state = publicationState(series);
  // A badge on 94% of rows is not a status, it is background texture -- and it
  // buried the handful of rows that do carry one. Say nothing until there is
  // something to say, so a real Active run or Run complete stands out.
  if (!state.known) return null;
  return <span className={`publication-status ${state.tone}`}>
    {state.tone === "ongoing" ? <ActiveRunIcon size={null} /> : null}
    {state.label}
  </span>;
}

export function MonitoringStatus({ series }) {
  // Following is acquisition's word; a reader host does not say it.
  const readerOnly = useReaderOnly();
  if (readerOnly || series.monitoringStatus !== "monitored") return null;
  return <span className="monitoring-status"><FollowedIcon size={null} /> Following</span>;
}

export function seriesAttentionLabel(series) {
  const unhealthyFiles = (series?.fileDetails || []).filter((file) => file.health?.status === "error").length;
  if (unhealthyFiles) return `${unhealthyFiles} comic file${unhealthyFiles === 1 ? "" : "s"} ${unhealthyFiles === 1 ? "needs" : "need"} attention`;
  return "Match needs attention";
}

export function seriesAttentionDetail(series) {
  const hasFileError = (series?.fileDetails || []).some((file) => file.health?.status === "error");
  return hasFileError ? "Review the comic files for page or archive errors." : "Review the series match before using its ownership totals.";
}

export function Ownership({ series, compact = false }) {
  // A reader host shows what there is to read, not what is owned of what
  // exists: the count, without a total or a bar.
  const readerOnly = useReaderOnly();
  // "Match needs attention" is the admin's to act on. A reader sees what is
  // owned -- as a count alone, since a run waiting on its match has no total
  // to trust yet.
  const admin = useManages();
  if (readerOnly) {
    const count = Number(series.owned) || 0;
    return compact && count ? <span className="series-card-foot series-card-counts">{count} {count === 1 ? "issue" : "issues"}</span> : null;
  }
  const percent = Math.max(5, Math.round((series.owned / series.total) * 100));
  const reviewing = series.status === "warning" && admin;
  const catalogUnknown = series.catalogKnown === false || series.status === "unknown" || (series.status === "warning" && !admin);
  const volumeCount = series.inventory?.editionCount || 0;
  const collectedOnly = volumeCount > 0 && !series.inventory?.directIssueFiles;
  const coveredIssues = series.issues?.filter((issue) => issue.ownership !== "unowned").length || 0;
  const arcCoverageKnown = collectedOnly && coveredIssues > 0;
  const ownedLabel = arcCoverageKnown ? `${coveredIssues} issue${coveredIssues === 1 ? "" : "s"} owned` : collectedOnly ? `${volumeCount} volume${volumeCount === 1 ? "" : "s"} owned` : `${series.owned} owned`;
  const label = reviewing ? seriesAttentionLabel(series) : catalogUnknown ? ownedLabel : compact ? `${series.owned} of ${series.total} owned` : `${series.owned} of ${series.total}`;
  const coverageDetail = arcCoverageKnown ? `${coveredIssues} issue${coveredIssues === 1 ? "" : "s"} collected in ${volumeCount} volume${volumeCount === 1 ? "" : "s"}${series.family ? " · complete-series progress is tracked separately" : ""}` : "";
  const detail = reviewing ? [coverageDetail, seriesAttentionDetail(series)].filter(Boolean).join(" · ") : series.isCollectionSeries ? series.ownership : coverageDetail || series.ownership;
  // An issue that is not out yet is not missing. Counting the two together
  // read as a gap to close on a run that is simply still being published.
  const summary = series.releaseSummary || {};
  const shortfall = Math.max(0, series.total - series.owned);
  const upcoming = Math.max(0, Number(summary.upcoming ?? 0));
  const missing = summary.releasedMissing == null
    ? Math.max(0, shortfall - upcoming)
    : Math.max(0, Number(summary.releasedMissing));
  const plural = (count) => (count === 1 ? "" : "s");
  const compactDetail = reviewing
    ? "Review required"
    : catalogUnknown
      ? "Series total unknown"
      : missing && upcoming
        ? `${missing} missing · ${upcoming} not published yet`
        : missing
          ? `${missing} issue${plural(missing)} missing`
          : upcoming
            ? `${upcoming} issue${plural(upcoming)} not published yet`
            : "Complete";
  // With the total unknown, the backing sentence restates the count already in
  // the label -- "23 owned" above "23 issues owned · complete-run progress
  // unknown". Use the concise form at every width, not just on mobile. Warning
  // rows keep the long form: theirs explains what needs attention.
  const showsConciseDetail = compact || (catalogUnknown && !reviewing);
  const visibleDetail = showsConciseDetail ? compactDetail : detail;
  if (compact) {
    return <span className={`ownership ${series.status} compact`}>
      <span className="ownership-label">{catalogUnknown || reviewing
        ? label
        : <><strong>{series.owned}</strong> of {series.total} Owned</>}</span>
      {!reviewing && !catalogUnknown ? <span className="progress"><i style={{ width: `${percent}%` }} /></span> : null}
    </span>;
  }
  return <div className={`ownership ${reviewing || series.status !== "warning" ? series.status : ""}`}><div className="ownership-label">{reviewing ? <WarningCircle size={19} weight="fill" /> : catalogUnknown ? <ClockCounterClockwise size={19} weight="fill" /> : series.status === "partial" ? <CircleHalf size={19} weight="fill" /> : <CheckCircle size={19} weight="fill" />}<strong>{label}</strong></div>{!reviewing && !catalogUnknown ? <div className="progress"><i style={{ width: `${percent}%` }} /></div> : null}{visibleDetail && visibleDetail !== label ? <span>{visibleDetail}</span> : null}</div>;
}

export function SeriesList({ series, onOpen, onRead, reading, view, cardAction }) {
  const readerOnly = useReaderOnly();
  // --card-index staggers the arrival; past the first screenful there is
  // nothing to see, so the delay stops climbing.
  //
  // The card is an article wrapping a full-bleed button rather than a button
  // itself: Read has to be its own target, and a button inside a button is
  // not a thing a browser will render. `.pull-card` has always been built
  // this way, so the hover and press rules are shared with it.
  if (view === "grid") return <div className="series-grid">{series.map((item, index) => item.kind === "collection"
    ? <CollectionCard card={item} index={index} reading={reading} onOpen={onOpen} onRead={onRead} action={cardAction?.(item)} key={item.key} />
    : item.kind === "arc"
    ? <ArcCard list={item} index={index} reading={reading} onOpen={onOpen} onRead={onRead} action={cardAction?.(item)} key={item.id} />
    : <article className="series-card" style={{ "--card-index": Math.min(index, 11) }} key={item.id}>
    <span className="series-card-art">
      <SeriesCover series={item} />
      {readerOnly ? <ReaderCardOverlay target={{ kind: "run", id: String(item.id), title: item.title }} title={item.title} read={runReadAction(item, reading)} onRead={onRead} />
        : <ReadRunOverlay run={item} reading={reading} onRead={onRead} />}
      {cardAction?.(item)}
    </span>
    <span className="series-card-identity"><strong>{item.title}</strong><span className="series-card-byline">{item.publisher} • {item.year}</span></span>
    <span className="series-card-statuses"><PublicationStatus series={item} /><MonitoringStatus series={item} /></span>
    <span className="series-card-rule" />
    <Ownership series={item} compact />
    {/* Last, and over the whole card: the card opens the run as it always
        did, and Read sits above it with a target of its own. The card cannot
        be the button any more -- a button inside a button is not a thing. */}
    <button type="button" className="discover-open series-card-open" onClick={() => onOpen(item)} aria-label={`${item.title}. Show details`} />
  </article>)}</div>;
  return (
    <div className="series-table">
      <div className="series-table-head"><span>Series</span><span>Ownership</span><span>Format</span><span>Last updated</span><span /></div>
      {series.map((item) => item.kind === "collection" ? <button className="series-row" onClick={() => onOpen(item)} key={item.key}>
          <div className="series-identity"><CoverArt id={item.key} title={item.name} cover={item.cover} coverCandidates={item.coverCandidates} decorative /><div className="series-identity-copy"><strong>{item.name}</strong><small>Collection · {[item.publisher, item.years, `${item.runCount} run${item.runCount === 1 ? "" : "s"}`].filter(Boolean).join(" · ")}</small></div></div>
          <span className="table-copy" /><span className="table-copy">Collection</span><span className="table-copy" /><DotsThree size={22} />
        </button> : item.kind === "arc" ? <button className="series-row" onClick={() => onOpen(item)} key={item.id}>
          <div className="series-identity"><CoverArt id={`arc-${item.listId}`} title={item.name} cover={item.cover} decorative /><div className="series-identity-copy"><strong>{item.name}</strong><small>Story arc · {[item.publisher, arcYears(item)].filter(Boolean).map((part) => `${part} · `).join("")}{item.seriesCount} run{item.seriesCount === 1 ? "" : "s"} · {item.issueCount} issue{item.issueCount === 1 ? "" : "s"}{arcOwnerLine(item) ? ` · ${arcOwnerLine(item)}` : ""}</small><div className="mobile-list-ownership"><Ownership series={arcOwnership(item)} compact /></div></div></div>
          <Ownership series={arcOwnership(item)} /><span className="table-copy">Story arc</span><span className="table-copy" /><DotsThree size={22} />
        </button> : (
        <button className="series-row" onClick={() => onOpen(item)} key={item.id}>
          <div className="series-identity"><SeriesCover series={item} decorative /><div className="series-identity-copy"><strong>{item.title} <em>({item.year})</em></strong><small>{item.isCollectionSeries ? `${item.publisher} · ${item.run}` : item.publisher}</small><span className="tag-line"><PublicationStatus series={item} /><MonitoringStatus series={item} /></span><div className="mobile-list-ownership"><Ownership series={item} compact /></div></div></div>
          <Ownership series={item} /><span className="table-copy">{item.format}</span><span className="table-copy">{item.updated}<small>{item.time}</small></span><DotsThree size={22} />
        </button>
      ))}
    </div>
  );
}

/**
 * A card's foot under the rule, for a story arc or a collection: its size,
 * then -- only while some of it is missing -- the owned bar, then who made
 * it when that is not the household (2026-10-02, after the owner's mock).
 */
export function CardFoot({ runCount, issueCount, owned, maker = "" }) {
  const readerOnly = useReaderOnly();
  const missing = readerOnly ? 0 : Math.max(0, issueCount - owned);
  return <>
    <span className="series-card-rule" />
    <span className="series-card-foot series-card-counts">{countsLine(runCount, readerOnly ? owned : issueCount)}</span>
    {issueCount && missing ? <Ownership series={arcOwnership({ owned, issueCount, missing })} compact /> : null}
    {maker ? <span className="series-card-foot series-card-maker">Made by <strong>{maker}</strong></span> : null}
  </>;
}

/** A collection, as a card among runs: its cover, name, years and size. */
export function CollectionCard({ card, index, reading, onOpen, onRead, action = null }) {
  const readerOnly = useReaderOnly();
  const count = `${card.runCount} run${card.runCount === 1 ? "" : "s"}`;
  return <article className="series-card collection-card" style={{ "--card-index": Math.min(index, 11) }}>
    <span className="series-card-art">
      <CoverArt id={card.key} title={card.name} cover={card.cover} coverCandidates={card.coverCandidates} />
      <span className="collection-card-mark" aria-hidden="true"><CollectionIcon size={16} /></span>
      {readerOnly ? <ReaderCardOverlay target={{ kind: "collection", id: String(card.id), title: card.name, runIds: (card.runIds || []).map(String) }} title={card.name} read={collectionReadAction(card, reading)} onRead={onRead} />
        : <CollectionReadOverlay card={card} reading={reading} onRead={onRead} />}
      {action}
    </span>
    <span className="series-card-identity"><strong>{card.name}</strong>{card.publisher || card.years ? <span className="series-card-byline">{[card.publisher, card.years].filter(Boolean).join(" • ")}</span> : null}</span>
    <span className="series-card-statuses"><StatusBadge tone="muted">Collection</StatusBadge></span>
    <CardFoot runCount={card.runCount} issueCount={card.issueCount || 0} owned={card.owned || 0} maker={arcMaker(card)} />
    <button type="button" className="discover-open series-card-open" onClick={() => onOpen(card)} aria-label={`${card.name}, a collection of ${count}. Show details`} />
  </article>;
}

/**
 * Read, over a run's cover in the Comics grid.
 *
 * Icon only: a label does not fit a card at 320px, so the wording lives in
 * the accessible name. Nothing is drawn for a run with no reading history --
 * working out what is readable would mean opening every archive in the
 * library on a request the grid makes on every visit -- so an unopened run
 * gets a plain Read and resolves when it is clicked.
 */
/**
 * What a card's Read does, for its overlay and its long-press menu alike:
 * `{verb, label, target, place}` for a run (`runReadAction`), a collection
 * and a story arc, or null when there is nothing to read.
 */
export function runReadAction(run, reading) {
  const place = reading?.[String(run.id)];
  const started = place?.state === "continue" && place.pageCount;
  const verb = started ? "Continue" : place?.state === "next" ? "Begin" : place?.state === "finished" ? "Restart" : "Read";
  const label = started
    ? `Continue ${issueLabel(place.issueNumber, run.medium)} of ${run.title}, page ${place.page + 1} of ${place.pageCount}`
    : `${verb} ${run.title}`;
  return { verb, label, place, target: started ? { id: place.fileId } : { runId: run.id } };
}

export function collectionReadAction(card, reading) {
  if (!reading) return null;
  const { run, allRead, anyRead } = collectionReadTarget(card, reading);
  if (!run) return null;
  const place = reading[String(run.id)];
  // How far through the collection, each run counting alike: a reader host
  // shows it on the cover.
  const runs = card.runs || [];
  const fraction = runs.length ? runs.reduce((sum, item) => sum + placeFraction(reading[String(item.id)]), 0) / runs.length : 0;
  const started = place?.state === "continue" && place.pageCount;
  const verb = started ? "Continue" : allRead ? "Restart" : place?.state === "next" || anyRead ? "Begin" : "Read";
  return { verb, label: `${verb} ${card.name}: ${run.title}`, target: started ? { id: place.fileId } : { runId: run.id },
    place: allRead ? { state: "finished" } : anyRead ? { state: "continue", fraction } : null };
}

export function listReadAction(list, reading) {
  const place = reading?.[String(list.id)];
  const started = place?.state === "continue" && place.pageCount;
  const verb = started ? "Continue" : place?.state === "next" ? "Begin" : place?.state === "finished" ? "Restart" : "Read";
  const context = { listId: list.listId || list.id, listName: list.name };
  return { verb, place, label: started ? `Continue ${list.name}, page ${place.page + 1} of ${place.pageCount}` : `${verb} ${list.name}`,
    target: started ? { id: place.fileId, ...context } : context };
}

/**
 * A card's actions on a reader host (Flipparr View): touch and hold the card
 * for Read and the host's own (Download, Reading list), in Apple's
 * context-menu way, instead of buttons over the cover. The "…" that anchors
 * the menu is drawn only for the keyboard (focus), so the cover stays clear.
 */
export function CardMenu({ target, title, read, onRead }) {
  const host = useContext(Host);
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  const close = useCallback(() => setOpen(false), []);
  useLongPress(anchor, ".series-card", () => setOpen(true));
  const items = [];
  if (read && onRead) items.push({ key: "read", label: read.verb, icon: <BookOpen size={16} weight="fill" />, onSelect: () => onRead(read.target) });
  items.push(...(host?.cardMenu?.(target) || []));
  if (!items.length) return null;
  return <>
    <button type="button" ref={anchor} className="series-card-menu" onClick={() => setOpen((value) => !value)}
      aria-haspopup="menu" aria-expanded={open} aria-label={`Options for ${title}`}><DotsThree size={18} weight="bold" /></button>
    {open ? <ActionMenu anchor={anchor} label={`Options for ${title}`} items={items} onClose={close} /> : null}
  </>;
}

/**
 * On a reader host, a card's cover carries its state, not buttons: how far
 * it is read and its download mark in two corners, and the long-press menu.
 */
function ReaderCardOverlay({ target, title, read, onRead }) {
  const place = read?.place;
  return <>
    <ReadState finished={place?.state === "finished"} fraction={place?.fraction ?? placeFraction(place)} />
    <CoverStatus target={target} />
    <CardMenu target={target} title={title} read={read} onRead={onRead} />
  </>;
}

export function ReadRunOverlay({ run, reading, onRead }) {
  if (!onRead) return null;
  const place = reading?.[String(run.id)];
  const started = place?.state === "continue" && place.pageCount;
  // No progress hairline on a run's cover: a line across one issue's pages sat
  // above the bar that means how much of the run is owned, and read as the
  // same kind of thing. The place is still in the button's accessible name.
  // The drawer's verbs, in one word: Continue a comic mid-page, Begin the next
  // one when you are between issues, Restart a run read to its end. A run
  // never opened gets a plain Read and resolves when it is clicked.
  const verb = started ? "Continue" : place?.state === "next" ? "Begin" : place?.state === "finished" ? "Restart" : "Read";
  const label = started
    ? `Continue ${issueLabel(place.issueNumber, run.medium)} of ${run.title}, page ${place.page + 1} of ${place.pageCount}`
    : `${verb} ${run.title}`;
  // Only a comic mid-page is opened by file: between issues the latest file is
  // the one just finished, and the reader would reopen it from page one. The
  // run resolves to the next unread -- or, once every issue is read, to #1.
  // How far the run is read sits in the cover's top-right corner, as on
  // every cover (ReadState): a Read sash, or the share read.
  return <>
    <ReadState finished={place?.state === "finished"} fraction={placeFraction(place)} />
    <button type="button" className="series-card-read" title={verb}
      onClick={() => onRead(started ? { id: place.fileId } : { runId: run.id })} aria-label={label}>
      <BookOpen size={16} weight="fill" />
      <b>{verb}</b>
    </button>
  </>;
}

// A collection's Read, over its card, as an arc's is: the run its drawer's
// Read would open (`collectionReadTarget`), with the same verbs and mark.
export function CollectionReadOverlay({ card, reading, onRead }) {
  if (!onRead || !reading) return null;
  const { run, allRead, anyRead } = collectionReadTarget(card, reading);
  if (!run) return null;
  const place = reading[String(run.id)];
  const started = place?.state === "continue" && place.pageCount;
  const verb = started ? "Continue" : allRead ? "Restart" : place?.state === "next" || anyRead ? "Begin" : "Read";
  return <>
    <ReadState finished={allRead} fraction={collectionReadAction(card, reading)?.place?.fraction} />
    <button type="button" className="series-card-read" title={verb}
      onClick={() => onRead(started ? { id: place.fileId } : { runId: run.id })} aria-label={`${verb} ${card.name}: ${run.title}`}>
      <BookOpen size={16} weight="fill" />
      <b>{verb}</b>
    </button>
  </>;
}

// A story arc's Read, over its card: the same verbs a run's has, and the
// comic it opens is the arc's next in order -- resolved when it is clicked,
// unless a comic is mid-page, which opens straight away.
export function ReadListOverlay({ list, reading, onRead }) {
  if (!onRead) return null;
  const place = reading?.[String(list.id)];
  const started = place?.state === "continue" && place.pageCount;
  const verb = started ? "Continue" : place?.state === "next" ? "Begin" : place?.state === "finished" ? "Restart" : "Read";
  const label = started ? `Continue ${list.name}, page ${place.page + 1} of ${place.pageCount}` : `${verb} ${list.name}`;
  const context = { listId: list.listId || list.id, listName: list.name };
  return <>
    <ReadState finished={place?.state === "finished"} fraction={placeFraction(place)} />
    <button type="button" className="series-card-read" title={verb}
      onClick={() => onRead(started ? { id: place.fileId, ...context } : context)} aria-label={label}>
      <BookOpen size={16} weight="fill" />
      <b>{verb}</b>
    </button>
  </>;
}

// What an arc's ownership bar reads from: how many of its issues are here.
export function arcOwnership(list) {
  return { owned: list.owned, total: list.issueCount, status: "arc", catalogKnown: true, releaseSummary: { upcoming: 0, releasedMissing: list.missing } };
}

// A story arc as a card, built as the run cards are so the two read the
// same: cover, name, how many runs and issues, and how much is here. On the
// Runs shelf it stands in for runs it spans, which its size already counts.
export function ArcCard({ list, index, reading, onOpen, onRead, action = null }) {
  const readerOnly = useReaderOnly();
  return <article className="series-card" style={{ "--card-index": Math.min(index, 11) }}>
    <span className="series-card-art">
      <CoverArt id={`arc-${list.listId || list.id}`} title={list.name} cover={list.cover} />
      {readerOnly ? <ReaderCardOverlay target={{ kind: "arc", id: String(list.listId || list.id), title: list.name, issueIds: list.issueIds || [] }} title={list.name} read={listReadAction(list, reading)} onRead={onRead} />
        : <ReadListOverlay list={list} reading={reading} onRead={onRead} />}
      {action}
    </span>
    <span className="series-card-identity"><strong>{list.name}</strong>{list.publisher || arcYears(list) ? <span className="series-card-byline">{[list.publisher, arcYears(list)].filter(Boolean).join(" • ")}</span> : null}</span>
    <span className="series-card-statuses"><StatusBadge tone="violet">Story arc</StatusBadge></span>
    <CardFoot runCount={list.seriesCount} issueCount={list.issueCount || 0} owned={list.owned || 0} maker={arcMaker(list)} />
    <button type="button" className="discover-open series-card-open" onClick={() => onOpen(list)} aria-label={`${list.name}. Show details`} />
  </article>;
}

export function ArcGrid({ lists, reading, onOpen, onRead }) {
  return <div className="series-grid">{lists.map((list, index) => <ArcCard list={list} index={index} reading={reading} onOpen={onOpen} onRead={onRead} key={list.id} />)}</div>;
}
