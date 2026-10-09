import { CoverArt, SeriesCover } from "../components/CoverArt.jsx";
import { StatusBadge } from "../components/StatusBadge";
import { ActiveRunIcon, CollectionIcon, FollowedIcon } from "../design-icons.jsx";
import { issueLabel } from "../discover.js";
import { isAdmin } from "../profiles.js";
import { arcMaker, arcOwnerLine, arcYears } from "../reading-list.js";
import { collectionReadTarget, countsLine } from "../run-collections.js";
import { useViewer } from "../viewer.js";
import { BookOpen, Check, CheckCircle, ClockCounterClockwise, DotsThree, WarningCircle } from "@phosphor-icons/react";

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
  if (series.monitoringStatus !== "monitored") return null;
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
  const percent = Math.max(5, Math.round((series.owned / series.total) * 100));
  // "Match needs attention" is the admin's to act on. A reader sees what is
  // owned -- as a count alone, since a run waiting on its match has no total
  // to trust yet.
  const admin = isAdmin(useViewer());
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
  return <div className={`ownership ${reviewing || series.status !== "warning" ? series.status : ""}`}><div className="ownership-label">{reviewing ? <WarningCircle size={19} weight="fill" /> : catalogUnknown ? <ClockCounterClockwise size={19} weight="fill" /> : <CheckCircle size={19} weight="fill" />}<strong>{label}</strong></div>{!reviewing && !catalogUnknown ? <div className="progress"><i style={{ width: `${percent}%` }} /></div> : null}{visibleDetail && visibleDetail !== label ? <span>{visibleDetail}</span> : null}</div>;
}

export function SeriesList({ series, onOpen, onRead, reading, view, cardAction }) {
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
      <ReadRunOverlay run={item} reading={reading} onRead={onRead} />
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
  const missing = Math.max(0, issueCount - owned);
  return <>
    <span className="series-card-rule" />
    <span className="series-card-foot series-card-counts">{countsLine(runCount, issueCount)}</span>
    {issueCount && missing ? <Ownership series={arcOwnership({ owned, issueCount, missing })} compact /> : null}
    {maker ? <span className="series-card-foot series-card-maker">Made by <strong>{maker}</strong></span> : null}
  </>;
}

/** A collection, as a card among runs: its cover, name, years and size. */
export function CollectionCard({ card, index, reading, onOpen, onRead, action = null }) {
  const count = `${card.runCount} run${card.runCount === 1 ? "" : "s"}`;
  return <article className="series-card collection-card" style={{ "--card-index": Math.min(index, 11) }}>
    <span className="series-card-art">
      <CoverArt id={card.key} title={card.name} cover={card.cover} coverCandidates={card.coverCandidates} />
      <span className="collection-card-mark" aria-hidden="true"><CollectionIcon size={16} /></span>
      <CollectionReadOverlay card={card} reading={reading} onRead={onRead} />
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
  // The marker says the same words an issue's badge does: "Read" once every
  // issue is, "In progress" on a run that has been started. A bare count of
  // what was left said nothing on its own (owner, 2026-09-28).
  return <>
    <ReadingMark place={place} />
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
    <ReadingMark place={allRead ? { state: "finished" } : anyRead ? { state: "continue" } : null} />
    <button type="button" className="series-card-read" title={verb}
      onClick={() => onRead(started ? { id: place.fileId } : { runId: run.id })} aria-label={`${verb} ${card.name}: ${run.title}`}>
      <BookOpen size={16} weight="fill" />
      <b>{verb}</b>
    </button>
  </>;
}

// The card's marker, the same on a run and a story arc: "Read" once every
// comic is, "In progress" once it has been started, nothing before.
export function ReadingMark({ place }) {
  if (!place) return null;
  return place.state === "finished"
    ? <span className="series-card-mark done"><Check size={13} weight="bold" aria-hidden="true" /> Read</span>
    : <span className="series-card-mark">In progress</span>;
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
    <ReadingMark place={place} />
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
  return <article className="series-card" style={{ "--card-index": Math.min(index, 11) }}>
    <span className="series-card-art">
      <CoverArt id={`arc-${list.listId || list.id}`} title={list.name} cover={list.cover} />
      <ReadListOverlay list={list} reading={reading} onRead={onRead} />
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
